"""
Nodos del grafo LangGraph.

Cada nodo:
- Recibe el estado actual + dependencias inyectadas (store, calendar, llm)
- Retorna SOLO un dict con los campos que modifica
- NUNCA lanza excepciones; las captura y pone en last_error
- Los nodos deterministas: 0 llamadas al LLM
- Los nodos LLM: exactamente 1 llamada

Todos los nodos son async y usan structlog.

FIXES APLICADOS:
- [CRÍTICO] node_entry solo mergeaba historial si state["messages"] estaba vacío
  (`if history and not state.get("messages")`). Como agent.py enviaba messages
  con datos, el historial nunca se mergeaba → el agente olvidaba la conversación.
  → Ahora SIEMPRE construye: history_del_store + [nuevo_HumanMessage].
  → agent.py ya no carga historial; solo pasa _incoming_message.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

from langchain_core.messages import HumanMessage

from src.llm_extractors import (
    extract_booking_data,
    extract_intent_llm,
    generate_deyy_response,
)
from src.state import (
    DIAS_ES,
    TIMEZONE,
    VALID_SERVICES,
    ArcadiumState,
    get_missing_fields,
    is_weekend_adjusted,
)

logger = structlog.get_logger("langgraph.nodes")


# ═══════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════


def _last_human_text(state: ArcadiumState) -> str:
    """Extrae el texto del último mensaje humano."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
            return msg.content
    return ""


def _safe_node(func_name: str):
    """Decorator que envuelve el nodo en try/except + logging."""
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
                elapsed = time.monotonic() - t0
                logger.info(
                    f"[node:{func_name}] completado",
                    elapsed_ms=round(elapsed * 1000, 1),
                    keys=list(result.keys()) if result else [],
                )
                return result or {}
            except Exception as e:
                elapsed = time.monotonic() - t0
                logger.error(
                    f"[node:{func_name}] error",
                    error=str(e),
                    elapsed_ms=round(elapsed * 1000, 1),
                )
                return {
                    "last_error": str(e),
                    "errors_count": kwargs.get("state", {}).get("errors_count", 0) + 1,
                    "should_escalate": kwargs.get("state", {}).get("errors_count", 0)
                    >= 2,
                }

        return wrapper

    return decorator


# ═══════════════════════════════════════════
# NODOS DETERMINISTAS (sin LLM)
# ═══════════════════════════════════════════


async def node_entry(
    state: ArcadiumState,
    *,
    store=None,
) -> Dict[str, Any]:
    """
    Primer nodo del grafo.
    - Calcula fechas con Python (nunca LLM)
    - Carga historial del store y construye messages = history + [nuevo mensaje]
    - Restaura campos persistentes del estado previo
    - Incrementa conversation_turns

    FIX: Antes solo mergeaba historial si state["messages"] estaba vacío.
    Como agent.py enviaba state["messages"] con datos, la condición era False
    y el historial nunca se incluía → el agente olvidaba la conversación.
    Ahora SIEMPRE carga desde el store y construye el historial completo,
    independientemente de lo que venga en state["messages"].
    """
    now = datetime.now(TIMEZONE)
    manana = now + timedelta(days=1)

    updates: Dict[str, Any] = {
        "fecha_hoy": now.strftime("%Y-%m-%d"),
        "hora_actual": now.strftime("%H:%M"),
        "dia_semana_hoy": DIAS_ES[now.weekday()],
        "manana_fecha": manana.strftime("%Y-%m-%d"),
        "manana_dia": DIAS_ES[manana.weekday()],
        "conversation_turns": state.get("conversation_turns", 0) + 1,
        "_extract_data_calls": 0,
    }

    # Obtener el mensaje nuevo desde _incoming_message (enviado por agent.py)
    incoming = state.get("_incoming_message", "")
    if not incoming:
        # Fallback: tomar el último HumanMessage del estado si existe
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
                incoming = msg.content
                break

    new_message = HumanMessage(content=incoming)

    if store:
        try:
            phone = state.get("phone_number", "")

            # FIX: SIEMPRE cargar historial del store y añadir el mensaje nuevo.
            # No condicionarlo a si state["messages"] está vacío o no.
            history = await store.get_history(phone, limit=50)
            updates["messages"] = list(history) + [new_message]

            # Restaurar campos persistentes que no vinieron en el estado
            prev_state = await store.get_agent_state(phone)
            if prev_state:
                for f in [
                    "patient_name",
                    "selected_service",
                    "service_duration",
                    "intent",
                    "datetime_preference",
                    "patient_phone",
                    "appointment_id",
                    "google_event_id",
                ]:
                    if f in prev_state and not state.get(f):
                        updates[f] = prev_state[f]

        except Exception as e:
            logger.warning("no se pudo cargar estado previo", error=str(e))
            # Fallback seguro: al menos incluir el mensaje nuevo
            updates["messages"] = [new_message]
    else:
        updates["messages"] = [new_message]

    # Escalación por número de turns
    if updates["conversation_turns"] >= 10:
        updates["should_escalate"] = True

    return updates


async def node_route_intent(state: ArcadiumState) -> Dict[str, Any]:
    """
    Detecta intención por keywords (determinista).
    Si no hay match suficiente → marca para fallback LLM.
    """
    from src.intent_router import route_by_keywords

    text = _last_human_text(state)
    intent = route_by_keywords(text)

    return {
        "intent": intent,
        "current_step": "route_intent_done",
    }


async def node_check_missing(state: ArcadiumState) -> Dict[str, Any]:
    """
    Evalúa qué campos obligatorios faltan.
    Determina el siguiente paso sin llamadas externas.
    """
    missing = get_missing_fields(state)
    return {
        "missing_fields": missing,
        "current_step": "missing_checked",
    }


async def node_adjust_weekend(state: ArcadiumState) -> Dict[str, Any]:
    """
    Si datetime_preference cae en fin de semana, ajusta al lunes.
    Determinista puro.
    """
    dt_iso = state.get("datetime_preference")
    if not dt_iso:
        return {}

    adjusted, new_iso = is_weekend_adjusted(dt_iso)
    if adjusted:
        logger.info(
            "Fecha ajustada fin de semana → lunes", original=dt_iso, adjusted=new_iso
        )
        return {
            "datetime_preference": new_iso,
            "datetime_adjusted": True,
        }
    return {"datetime_adjusted": False}


async def node_check_availability(
    state: ArcadiumState,
    *,
    calendar_service=None,
) -> Dict[str, Any]:
    """
    Consulta slots disponibles vía Google Calendar.
    Sin LLM.
    """
    dt_iso = state.get("datetime_preference")
    duration = state.get("service_duration", 30)

    if not dt_iso or not calendar_service:
        return {
            "available_slots": [],
            "last_error": "No hay fecha para consultar disponibilidad"
            if not dt_iso
            else "Calendar service no disponible",
        }

    try:
        dt = datetime.fromisoformat(dt_iso)
        # Ajustar fin de semana si no se hizo ya
        if dt.weekday() >= 5:
            days = 7 - dt.weekday()
            dt = dt + timedelta(days=days)

        slots = await calendar_service.get_available_slots(
            date=dt.date(),
            duration_minutes=duration,
        )

        return {
            "available_slots": slots,
            "current_step": "awaiting_selection",
        }
    except Exception as e:
        return {
            "available_slots": [],
            "last_error": f"Error consultando disponibilidad: {e}",
        }


async def node_detect_confirmation(state: ArcadiumState) -> Dict[str, Any]:
    """
    Detecta si el usuario confirmó, rechazó, o eligió un slot.
    Sin LLM — regex y keywords.
    """
    from src.intent_router import detect_confirmation, extract_slot_from_text

    text = _last_human_text(state)
    result = detect_confirmation(text)

    selected_slot = None
    if result == "slot_choice":
        selected_slot = extract_slot_from_text(text, state.get("available_slots", []))

    return {
        "confirmation_result": result,
        "selected_slot": selected_slot or state.get("selected_slot"),
        "current_step": "confirmation_detected",
    }


async def node_validate_and_confirm(state: ArcadiumState) -> Dict[str, Any]:
    """
    Valida que el slot elegido esté en available_slots.
    Si es válido, marca awaiting_confirmation.
    """
    selected = state.get("selected_slot")
    available = state.get("available_slots", [])

    if selected and selected in available:
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "book",
            "current_step": "awaiting_final_confirmation",
        }

    return {
        "last_error": "Slot seleccionado no está disponible",
        "should_escalate": False,
    }


async def node_book_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    db_service=None,
) -> Dict[str, Any]:
    """
    Agenda en Google Calendar y DB.
    DETERMINISTA — cero llamadas al LLM.
    """
    slot = state.get("selected_slot") or state.get("datetime_preference")
    if not slot:
        return {"last_error": "No hay slot seleccionado para agendar"}

    try:
        dt = datetime.fromisoformat(slot)
        duration = state.get("service_duration", 30)
        end_dt = dt + timedelta(minutes=duration)

        patient = state.get("patient_name", "Paciente")
        service = state.get("selected_service", "consulta")

        # Crear en Google Calendar
        event_id = None
        event_link = None
        if calendar_service:
            event_id, event_link = await calendar_service.create_event(
                start=dt,
                end=end_dt,
                title=f"{service} - {patient}",
                description=f"Paciente: {patient}\nTeléfono: {state.get('phone_number', '')}",
            )

        # Crear en DB
        appt_id = None
        if db_service:
            try:
                success, msg, appt = await db_service.create_appointment(
                    session=None,
                    phone_number=state.get("phone_number", ""),
                    appointment_datetime=dt,
                    service_type=service,
                    project_id=state.get("project_id"),
                    metadata={"google_event_id": event_id, "patient_name": patient},
                )
                if appt:
                    appt_id = str(appt.id)
            except Exception as e:
                logger.warning("Error creando cita en DB", error=str(e))

        logger.info(
            "Cita agendada",
            patient=patient,
            service=service,
            slot=slot,
            event_id=event_id,
        )

        return {
            "appointment_id": appt_id or "pending_db",
            "google_event_id": event_id,
            "google_event_link": event_link,
            "confirmation_sent": True,
            "current_step": "resolution",
        }

    except Exception as e:
        return {
            "last_error": f"Error agendando cita: {e}",
            "should_escalate": True,
        }


async def node_cancel_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    db_service=None,
) -> Dict[str, Any]:
    """
    Cancela cita en Google Calendar y DB.
    DETERMINISTA — cero LLM.
    """
    event_id = state.get("google_event_id")
    appt_id = state.get("appointment_id")

    try:
        if calendar_service and event_id:
            await calendar_service.delete_event(event_id)

        if db_service and appt_id:
            try:
                import uuid as _uuid

                await db_service.cancel_appointment(
                    session=None,
                    appointment_id=_uuid.UUID(appt_id),
                )
            except Exception as e:
                logger.warning("Error cancelando en DB", error=str(e))

        return {
            "current_step": "resolution",
            "confirmation_sent": True,
        }

    except Exception as e:
        return {
            "last_error": f"Error cancelando cita: {e}",
        }


async def node_save_state(
    state: ArcadiumState,
    *,
    store=None,
) -> Dict[str, Any]:
    """
    Persiste el estado actual en DB a través del store.
    Guarda mensajes nuevos y actualiza user_profiles.
    """
    if not store:
        return {}

    try:
        phone = state.get("phone_number", "")

        # FIX: usar filter_persistent_state para excluir campos transitorios
        # (fechas, current_step, _extract_data_calls, available_slots, etc.)
        # que no deben restaurarse en sesiones futuras.
        from src.state import filter_persistent_state

        await store.save_agent_state(phone, filter_persistent_state(state))

        # Persistir mensajes nuevos (HumanMessage y AIMessage únicamente)
        messages = state.get("messages", [])
        from langchain_core.messages import AIMessage
        from langchain_core.messages import HumanMessage as HM

        for msg in messages:
            if isinstance(msg, (HM, AIMessage)):
                try:
                    await store.add_message(
                        phone, msg, project_id=state.get("project_id")
                    )
                except Exception as e:
                    logger.warning("Error guardando mensaje", error=str(e))

        # Actualizar perfil del usuario
        profile_updates = {}
        if state.get("patient_name"):
            profile_updates["patient_name"] = state["patient_name"]
        if state.get("patient_phone"):
            profile_updates["patient_phone"] = state["patient_phone"]

        if profile_updates:
            await store.upsert_user_profile(phone, profile_updates)

        return {"current_step": "state_saved"}

    except Exception as e:
        logger.warning("Error guardando estado", error=str(e))
        return {"last_error": f"Error persistiendo: {e}"}


# ═══════════════════════════════════════════
# NODOS LLM (1 llamada cada uno)
# ═══════════════════════════════════════════


async def node_extract_intent(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Fallback del routing de keywords.
    SOLO se llama cuando route_by_keywords retornó None.
    1 llamada al LLM.
    """
    if not llm:
        return {"last_error": "LLM no disponible para extract_intent"}

    text = _last_human_text(state)
    history = state.get("messages", [])
    intent, confidence = await extract_intent_llm(text, llm, history=history)

    logger.info("intent extraído por LLM", intent=intent, confidence=confidence)
    return {"intent": intent, "current_step": "intent_extracted"}


async def node_extract_data(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Extrae servicio, fecha y nombre del texto libre.
    1 llamada al LLM.
    """
    if not llm:
        return {"last_error": "LLM no disponible para extract_data"}

    missing = get_missing_fields(state)
    if not missing:
        return {}  # Ya tenemos todo

    text = _last_human_text(state)
    context = {
        "fecha_hoy": state.get("fecha_hoy", ""),
        "manana_fecha": state.get("manana_fecha", ""),
        "dia_semana_hoy": state.get("dia_semana_hoy", ""),
        "manana_dia": state.get("manana_dia", ""),
        "missing_fields": missing,
    }

    history = state.get("messages", [])
    data = await extract_booking_data(text, context, llm, history=history)

    prev_calls = state.get("_extract_data_calls", 0)

    updates: Dict[str, Any] = {}
    updates["_extract_data_calls"] = prev_calls + 1

    if data.get("service"):
        svc = data["service"]
        svc_lower = svc.lower().strip()
        if svc_lower in VALID_SERVICES:
            updates["selected_service"] = svc_lower
            updates["service_duration"] = VALID_SERVICES[svc_lower]
        else:
            for known, duration in VALID_SERVICES.items():
                if known in svc_lower or svc_lower in known:
                    updates["selected_service"] = known
                    updates["service_duration"] = duration
                    break
            else:
                updates["selected_service"] = svc_lower
                updates["service_duration"] = 30

    if data.get("datetime_iso"):
        updates["datetime_preference"] = data["datetime_iso"]

    if data.get("patient_name"):
        updates["patient_name"] = data["patient_name"]

    # Recalcular missing
    merged = {**state, **updates}
    updates["missing_fields"] = get_missing_fields(merged)

    logger.info(
        "datos extraídos por LLM",
        extracted={k: v for k, v in updates.items() if k != "missing_fields"},
    )
    return updates


async def node_generate_response(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Genera el mensaje final de Deyy.
    1 llamada al LLM. Sin tools. Solo texto→texto.
    """
    if not llm:
        fallback = "Lo siento, hubo un error. Por favor intente nuevamente o llame a la clínica. 📞"
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content=fallback)]}

    context = _build_llm_context(state)
    history = state.get("messages", [])
    text = await generate_deyy_response(context, llm, history=history)

    from langchain_core.messages import AIMessage

    return {"messages": [AIMessage(content=text)]}


def _build_llm_context(state: ArcadiumState) -> Dict[str, Any]:
    """Construye dict de contexto para node_generate_response."""
    return {
        "intent": state.get("intent"),
        "patient_name": state.get("patient_name"),
        "missing_fields": state.get("missing_fields", []),
        "available_slots": state.get("available_slots", []),
        "selected_slot": state.get("selected_slot"),
        "confirmation_result": state.get("confirmation_result"),
        "confirmation_type": state.get("confirmation_type"),
        "appointment_id": state.get("appointment_id"),
        "google_event_link": state.get("google_event_link"),
        "selected_service": state.get("selected_service"),
        "datetime_preference": state.get("datetime_preference"),
        "last_error": state.get("last_error"),
        "conversation_turns": state.get("conversation_turns", 0),
    }
