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
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

from langchain_core.messages import HumanMessage, ToolMessage

from src.llm_extractors import (
    extract_booking_data,
    extract_intent_llm,
    generate_deyy_response,
)
from memory_agent_integration.memory_tools import upsert_memory_arcadium
from agents.langchain_compat import create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from functools import partial
from src.state import (
    DIAS_ES,
    TIMEZONE,
    VALID_SERVICES,
    ArcadiumState,
    get_missing_fields,
    is_weekend_adjusted,
)

logger = structlog.get_logger("langgraph.nodes")


# ═══════════════════════════════════════════════════════════
# PROMPT PARA GENERACIÓN DE RESPUESTA CON TOOL-CALLING
# ═══════════════════════════════════════════════════════════

_GENERATE_RESPONSE_SYSTEM_WITH_TOOLS = """\
Eres Deyy, asistente virtual de recepción de Arcadium Rehabilitación Oral (Ecuador).

REGLAS INQUEBRANTABLES:
1. Habla en español usando "usted" (no "tú", no "vos").
2. MÁXIMO 2 líneas de texto por mensaje.
3. MÁXIMO 2 emojis, y SOLO de este set: 😊 👋 📅 ✅ ❌ 🦷 ⏰ 📞
4. NUNCA anuncies lo que vas a hacer ("Voy a revisar la disponibilidad...").
5. NUNCA digas "Estoy aquí para ayudarle" ni frases robóticas similares.
6. Sé cálida pero profesional.
7. Si hay slots disponibles, muestra máximo 4 los más cercanos.
8. Si falta información, pregunta por UNA sola cosa a la vez.
9. Si se agendó exitosamente, confirma fecha + hora + servicio.
10. Si hay error, sugiere llamar a la clínica: 📞.
11. NUNCA repitas una pregunta que ya hiciste en el historial.
12. Si el usuario ya dio un dato (nombre, servicio, fecha), NO lo pidas de nuevo.

INSTRUCCIÓN ADICIONAL (HERRAMIENTA DE MEMORIA):
Si el usuario revela información personal importante (nombre, alergias, preferencias, datos médicos, etc.)
que deba recordarse en futuras conversaciones, usa la herramienta upsert_memory_arcadium.
- content: describe el hecho de forma clara y concisa.
- context: indica cuándo/por qué se mencionó (ej: "Mencionado durante conversación del 2025-04-07").
No anuncies que guardas la información; simplemente usa la herramienta cuando corresponda.

SITUACIÓN ACTUAL:
{context}
"""


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
        "_tool_iterations": 0,
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
            logger.info("node_entry: historial cargado", phone=phone, history_len=len(history))
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

        saved_count = 0
        for msg in messages:
            if isinstance(msg, (HM, AIMessage)):
                try:
                    await store.add_message(
                        phone, msg, project_id=state.get("project_id")
                    )
                    saved_count += 1
                except Exception as e:
                    logger.warning("Error guardando mensaje", error=str(e))
        logger.info("node_save_state: mensajes guardados", phone=phone, count=saved_count)

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
    """Construye dict de contexto para generación de respuesta."""
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
        "semantic_memory_context": state.get("semantic_memory_context", ""),
    }


# ═══════════════════════════════════════════════════════════
# NODO GENERATE_RESPONSE CON TOOL-CALLING
# ═══════════════════════════════════════════════════════════

async def node_generate_response_with_tools(
    state: ArcadiumState,
    *,
    llm=None,
    vector_store=None,
) -> Dict[str, Any]:
    """
    Genera la respuesta final de Deyy con soporte para tool-calling.

    Si vector_store está disponible, bindea la herramienta upsert_memory_arcadium.
    Flujo:
    1. Construye prompt con contexto.
    2. Invoca LLM con bind_tools.
    3. Devuelve mensaje AI (puede contener tool_calls).

    El routing condicional decidirá si ejecutar herramientas o guardar estado.
    """
    if not llm:
        fallback = "Lo siento, hubo un error. Por favor intente nuevamente o llame a la clínica. 📞"
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content=fallback)]}

    # Incrementar contador de iteraciones
    iterations = state.get("_tool_iterations", 0) + 1

    # Construir contexto para system prompt
    context_dict = _build_llm_context(state)
    context_parts = []

    intent = context_dict.get("intent")
    if intent:
        context_parts.append(f"Intención del usuario: {intent}")

    missing = context_dict.get("missing_fields", [])
    if missing:
        context_parts.append(
            f"Datos que faltan: {', '.join(missing)}. Pídelos de a uno."
        )

    patient_name = context_dict.get("patient_name")
    if patient_name:
        context_parts.append(f"Nombre del paciente: {patient_name}")

    selected_service = context_dict.get("selected_service")
    if selected_service:
        context_parts.append(f"Servicio seleccionado: {selected_service}")

    datetime_pref = context_dict.get("datetime_preference")
    if datetime_pref:
        context_parts.append(f"Fecha/hora preferida: {datetime_pref}")

    slots = context_dict.get("available_slots", [])
    if slots:
        readable = _format_slots(slots[:4])
        context_parts.append(f"Slots disponibles: {readable}")

    selected_slot = context_dict.get("selected_slot")
    if selected_slot:
        context_parts.append(f"Usuario eligió slot: {selected_slot}")

    appt_id = context_dict.get("appointment_id")
    if appt_id:
        svc = context_dict.get("selected_service", "")
        slot = context_dict.get("selected_slot") or context_dict.get("datetime_preference", "")
        context_parts.append(
            f"Cita agendada exitosamente: {svc} el {slot}. Confirma al usuario."
        )

    error = context_dict.get("last_error")
    if error:
        context_parts.append(f"Error ocurrido: {error}. Sugiere llamar a la clínica.")

    turns = context_dict.get("conversation_turns", 0)
    if turns >= 8:
        context_parts.append(
            "Ya van muchos mensajes. Considera ofrecer llamar a la clínica."
        )

    semantic = context_dict.get("semantic_memory_context")
    if semantic:
        context_parts.append(f"INFORMACIÓN PREVIA DEL USUARIO:\n{semantic}")

    context_str = (
        "\n".join(context_parts) if context_parts else "Sin contexto específico."
    )

    system_prompt = _GENERATE_RESPONSE_SYSTEM_WITH_TOOLS.format(context=context_str)

    # Preparar mensajes para el LLM: system + historial
    history = state.get("messages", [])
    from langchain_core.messages import SystemMessage

    lm_messages = [SystemMessage(content=system_prompt)] + list(history)

    # Bindear herramienta si hay vector_store y phone_number
    bound_tool = None
    if vector_store:
        user_id = state.get("phone_number", "")
        if user_id:
            bound_tool = upsert_memory_arcadium
        else:
            logger.warning("No hay phone_number en estado, omitiendo tool binding")

    try:
        if bound_tool:
            llm_with_tools = llm.bind_tools([bound_tool])
            response = await llm_with_tools.ainvoke(lm_messages)
        else:
            # Sin herramienta: generar respuesta simple (fallback)
            response = await llm.ainvoke(lm_messages)

        return {
            "messages": [response],
            "_tool_iterations": iterations,
        }

    except Exception as e:
        logger.error("Error en node_generate_response_with_tools", error=str(e))
        from langchain_core.messages import AIMessage

        return {
            "messages": [AIMessage(content=f"Lo siento, hubo un error generando la respuesta.")],
            "_tool_iterations": iterations,
            "last_error": str(e),
            "should_escalate": True,
        }


# ═══════════════════════════════════════════════════════════
# NODO EJECUCIÓN DE MEMORY TOOLS
# ═══════════════════════════════════════════════════════════

async def node_execute_memory_tools(
    state: ArcadiumState,
    *,
    vector_store=None,
) -> Dict[str, Any]:
    """
    Ejecuta los tool calls de upsert_memory_arcadium presentes en el último mensaje AI.

    Extrae tool_calls y guarda las memorias directamente en el vector_store.
    Devuelve ToolMessages con确认.
    """
    if not vector_store:
        logger.warning("vector_store no disponible, omitiendo ejecución de memory tools")
        return {}

    messages = state.get("messages", [])
    if not messages:
        return {}

    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", [])
    if not tool_calls:
        return {}

    tool_messages = []
    user_id = state.get("phone_number", "")

    for tc in tool_calls:
        tool_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        tool_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
        tool_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)

        if tool_name != "upsert_memory_arcadium":
            logger.warning("Tool desconocido en node_execute_memory_tools", tool_name=tool_name)
            continue

        if not user_id:
            logger.warning("No hay phone_number en estado, omitiendo tool call")
            continue

        try:
            content = tool_args.get("content", "")
            context = tool_args.get("context", "")
            memory_id = tool_args.get("memory_id")  # opcional, si None generamos nuevo

            #Namespace: por defecto ("memories", user_id). Futuro: project_id
            namespace = ("memories", user_id)
            mem_id = memory_id or str(uuid.uuid4())

            value = {
                "content": content,
                "context": context,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

            await vector_store.aput(namespace, key=mem_id, value=value)

            logger.info(
                "Memoria guardada por node_execute_memory_tools",
                user_id=user_id,
                memory_id=mem_id,
                content=content[:50],
            )

            result_msg = f"Memoria guardada. ID: {mem_id}"
            if memory_id:
                result_msg = f"Memoria actualizada. ID: {mem_id}"

            from langchain_core.messages import ToolMessage

            if tool_id:
                tool_messages.append(
                    ToolMessage(content=result_msg, tool_call_id=tool_id)
                )
            else:
                logger.warning("Tool call sin id, omitiendo ToolMessage")

        except Exception as e:
            logger.error("Error en node_execute_memory_tools", error=str(e), exc_info=True)
            if tool_id:
                from langchain_core.messages import ToolMessage
                tool_messages.append(
                    ToolMessage(content=f"Error guardando memoria: {str(e)}", tool_call_id=tool_id)
                )

    if tool_messages:
        return {"messages": tool_messages}
    return {}


# ═══════════════════════════════════════════════════════════
# EDGE: DESPUÉS DE GENERATE_RESPONSE
# ═══════════════════════════════════════════════════════════

def edge_after_generate_response(state: ArcadiumState) -> str:
    """
    Routing condicional después de generate_response_with_tools.

    Si el último mensaje AI tiene tool_calls y no se ha excedido el límite de iteraciones,
    va a execute_memory_tools. En caso contrario, va a save_state.
    """
    messages = state.get("messages", [])
    if not messages:
        return "save_state"

    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None)

    if tool_calls:
        iterations = state.get("_tool_iterations", 0)
        if iterations >= 2:
            logger.warning(
                "Límite de tool-iterations alcanzado, omitiendo tool calls",
                iterations=iterations,
            )
            return "save_state"
        logger.debug(
            "Tool calls detectados, enrutando a execute_memory_tools",
            iterations=iterations,
            tool_calls_count=len(tool_calls),
        )
        return "execute_memory_tools"

    return "save_state"
