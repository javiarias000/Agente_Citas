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
from typing import Any, Dict, Optional

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

ZONA HORARIA: Ecuador (UTC-5). La hora y fecha del contexto son locales de Ecuador.
NUNCA uses UTC para evaluar si una hora "ya pasó". Usa SIEMPRE hora_actual_ecuador.

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
            # Registrar cuántos mensajes existían antes de este turno.
            # node_save_state usará este valor para guardar SOLO los mensajes nuevos.
            updates["_history_len"] = len(history)

            # Restaurar campos persistentes desde el estado guardado.
            # IMPORTANTE: Sobrescribir SIEMPRE los valores del estado previo
            # (el state actual viene vacío/None por defecto en cada turno nuevo)
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
                    "awaiting_confirmation",
                    "confirmation_type",
                    # available_slots se persiste para que detect_confirmation
                    # pueda extraer el slot elegido en el turno siguiente
                    "available_slots",
                    "selected_slot",
                ]:
                    if f in prev_state and prev_state[f] is not None:
                        updates[f] = prev_state[f]

            # Fallback adicional para patient_name: intentar desde user_profiles
            # Cubre el caso donde agent_state no tiene patient_name (primer turno o
            # después de una sesión sin appointment) pero el perfil sí lo tiene.
            if not updates.get("patient_name"):
                try:
                    profile = await store.get_user_profile(phone)
                    if profile and profile.get("patient_name"):
                        updates["patient_name"] = profile["patient_name"]
                        logger.info(
                            "node_entry: patient_name restaurado desde user_profile",
                            phone=phone,
                            patient_name=profile["patient_name"],
                        )
                except Exception:
                    pass

        except Exception as e:
            logger.warning("no se pudo cargar estado previo", error=str(e))
            # Fallback seguro: al menos incluir el mensaje nuevo.
            # IMPORTANTE: resetear _history_len a 0 porque messages se sobreescribe
            # sin historial. Si _history_len quedara con un valor previo (del try block
            # parcialmente ejecutado), node_save_state calcularía new_messages[N:] vacío.
            updates["messages"] = [new_message]
            updates["_history_len"] = 0
    else:
        updates["messages"] = [new_message]
        updates["_history_len"] = 0

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
    Convierte los dicts de slot a ISO strings para que sean serializables
    y compatibles con extract_slot_from_text.
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

        # Normalizar a ISO strings para facilitar comparación y serialización
        slots_iso = []
        for s in slots:
            if isinstance(s, dict):
                start = s.get("start")
                if isinstance(start, datetime):
                    slots_iso.append(start.isoformat())
                else:
                    slots_iso.append(str(start))
            else:
                slots_iso.append(str(s))

        if not slots_iso:
            return {
                "available_slots": [],
                "last_error": "No hay slots disponibles para esa fecha. Intenta otra fecha.",
            }

        return {
            "available_slots": slots_iso,
            "current_step": "awaiting_selection",
            # Marcar que esperamos selección → el siguiente turno va a detect_confirmation
            "awaiting_confirmation": True,
            "confirmation_type": "book",
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
    Valida que hay un slot elegido. No requiere available_slots en estado
    (puede estar vacío si llegamos desde un turno posterior donde los slots
    ya se limpiaron; la validación real ocurrió en extract_slot_from_text).
    """
    selected = state.get("selected_slot")

    if selected:
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "book",
            "current_step": "awaiting_final_confirmation",
        }

    return {
        "last_error": "No se identificó el slot seleccionado. ¿Puede indicar la hora exacta?",
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

    INVARIANTE CRÍTICO: NUNCA retorna confirmation_sent=True si google_event_id es None.
    Si no hay evento en Calendar, retorna error. El LLM NO debe confirmar citas falsas.
    """
    logger.info(
        "[node_book_appointment] iniciando",
        phone=state.get("phone_number", ""),
        service=state.get("selected_service", ""),
        slot=state.get("selected_slot") or state.get("datetime_preference", ""),
        has_calendar_service=calendar_service is not None,
    )

    slot = state.get("selected_slot") or state.get("datetime_preference")
    if not slot:
        logger.error("[node_book_appointment] sin slot para agendar")
        return {"last_error": "No hay slot seleccionado para agendar"}

    # GUARD: calendar_service es OBLIGATORIO — sin él no hay cita real
    if not calendar_service:
        logger.error("[node_book_appointment] calendar_service no disponible — abortando")
        return {
            "last_error": (
                "El servicio de Google Calendar no está disponible en este momento. "
                "Por favor llame a la clínica directamente. 📞"
            ),
            "should_escalate": False,
        }

    try:
        dt = datetime.fromisoformat(slot)
        duration = state.get("service_duration", 30)
        end_dt = dt + timedelta(minutes=duration)

        patient = state.get("patient_name", "Paciente")
        service = state.get("selected_service", "consulta")

        # Crear en Google Calendar
        logger.info(
            "[node_book_appointment] llamando create_event",
            patient=patient,
            service=service,
            start=dt.isoformat(),
            end=end_dt.isoformat(),
        )
        event = await calendar_service.create_event(
            title=f"{service} - {patient}",
            start_time=dt,
            end_time=end_dt,
            description=f"Paciente: {patient}\nTeléfono: {state.get('phone_number', '')}",
        )
        event_id = event.get("id")
        event_link = event.get("htmlLink")

        # GUARD: verificar que el evento fue realmente creado
        if not event_id:
            logger.error(
                "[node_book_appointment] create_event no devolvió ID — fallo silencioso en Calendar",
                event_response=str(event)[:200],
            )
            return {
                "last_error": "Error confirmando la cita en Google Calendar (sin ID). Por favor llame a la clínica. 📞",
                "should_escalate": True,
            }

        logger.info(
            "[node_book_appointment] evento creado EXITOSAMENTE en Google Calendar",
            event_id=event_id,
            event_link=event_link,
            patient=patient,
            service=service,
            slot=slot,
        )

        # Crear en DB (opcional — no bloquea el flujo)
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
                logger.warning("[node_book_appointment] error creando cita en DB (no crítico)", error=str(e))

        return {
            # Usar event_id como fallback para appointment_id si no hay DB
            "appointment_id": appt_id or f"gcal_{event_id}",
            "google_event_id": event_id,
            "google_event_link": event_link,
            # confirmation_sent=True SOLO cuando google_event_id está confirmado
            "confirmation_sent": True,
            "current_step": "resolution",
            # Limpiar estado de selección para no reutilizar en próximos turnos
            "awaiting_confirmation": False,
            "available_slots": [],
            "confirmation_type": None,
        }

    except Exception as e:
        logger.error(
            "[node_book_appointment] excepción al agendar",
            error=str(e),
            phone=state.get("phone_number", ""),
        )
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
            # Limpiar IDs para que el LLM no confunda con una reserva activa
            "appointment_id": None,
            "google_event_id": None,
            "google_event_link": None,
        }

    except Exception as e:
        return {
            "last_error": f"Error cancelando cita: {e}",
        }


def _normalize_phone(phone: str) -> str:
    """Normaliza teléfono eliminando '+', espacios y guiones para comparación uniforme."""
    return phone.replace("+", "").replace(" ", "").replace("-", "").strip()


def _phone_in_text(phone: str, text: str) -> bool:
    """
    Devuelve True si el teléfono normalizado aparece dentro del texto normalizado.
    Cubre formatos: +5930999…, 5930999…, 0999… (todos se reducen a dígitos puros).
    """
    if not phone or not text:
        return False
    return _normalize_phone(phone) in _normalize_phone(text)


def _name_in_text(name: str, text: str) -> bool:
    """
    Devuelve True si el nombre aparece en el texto (insensible a mayúsculas/tildes).
    Requiere al menos 3 caracteres para evitar falsos positivos con nombres muy cortos.
    """
    if not name or len(name) < 3 or not text:
        return False
    return name.lower().strip() in text.lower()


def _service_in_text(service: str, text: str) -> bool:
    """
    Devuelve True si el nombre del servicio aparece en el texto (insensible a mayúsculas).
    Solo aplica cuando service tiene al menos 3 caracteres.
    """
    if not service or len(service) < 3 or not text:
        return False
    return service.lower().strip() in text.lower()


def _parse_event_start(ev: Dict[str, Any], tz: "ZoneInfo") -> Optional[datetime]:
    """
    Parsea el datetime de inicio de un evento de Google Calendar a un datetime tz-aware.
    Retorna None si no puede parsear.
    """
    start_raw = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
    if not start_raw:
        return None
    try:
        dt = datetime.fromisoformat(start_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        return None


def _event_to_dict(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un evento crudo de Calendar API a un dict normalizado para el estado."""
    start_raw = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
    # Quitar offset de timezone del string para uso interno (la TZ es siempre Guayaquil)
    dt_str = start_raw.split("+")[0].split("Z")[0] if start_raw else ""
    return {
        "event_id": ev.get("id"),
        "summary": ev.get("summary", ""),
        "start": dt_str,
        "html_link": ev.get("htmlLink", ""),
        "description": (ev.get("description") or "")[:200],
    }


async def node_check_existing_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
) -> Dict[str, Any]:
    """
    Forcing tool — SIEMPRE se ejecuta cuando el intent es agendar, cancelar o reagendar.

    Estrategia de búsqueda (DOS capas):
      1. list_events en los próximos 60 días → filtrar localmente por teléfono
      2. search_events_by_query con el nombre del paciente → resultados de la API de Google

    Filtros aplicados (sin falsos positivos):
      - PACIENTE: phone en description  OR  patient_name en summary/description
      - SERVICIO: si selected_service está en el estado, refinar la lista con ese servicio
      - DÍA: si datetime_preference está en el estado, filtrar para el día exacto
        (solo en el resultado "servicio+día", no en la búsqueda amplia de "cancelar")

    Retorna:
      calendar_appointment_found  → True SOLO si hay coincidencia real por paciente
      existing_appointments       → lista de citas del paciente (máx 3)
      calendar_total_for_patient  → total de citas encontradas del paciente
      calendar_slots_available    → slots libres aproximados en el día solicitado (si hay fecha)
      calendar_first_match        → primer evento que coincide con paciente+servicio+día
      google_event_id / link      → del evento más reciente del paciente (para cancel/reschedule)

    DETERMINISTA — cero LLM.
    """
    # ── Guard: sin calendar_service ──────────────────────────────────────────
    if not calendar_service:
        logger.warning("node_check_existing_appointment: sin calendar_service")
        return _no_appointment_found()

    phone = state.get("phone_number", "")
    patient_name = (state.get("patient_name") or "").strip()
    service = (state.get("selected_service") or "").strip().lower()
    dt_pref = state.get("datetime_preference")
    intent = state.get("intent", "")

    # ── Guard: sin ningún identificador del paciente ─────────────────────────
    if not phone and not patient_name:
        logger.warning("node_check_existing_appointment: sin teléfono ni nombre")
        return _no_appointment_found()

    try:
        tz = ZoneInfo("America/Guayaquil")
        now = datetime.now(tz)
        future = now + timedelta(days=60)

        # ── Resolver el día de la cita solicitada (si aplica) ─────────────────
        # Se usa para: (a) filtrar eventos del mismo día, (b) calcular slots libres.
        requested_day: Optional[datetime] = None
        if dt_pref:
            try:
                dt_parsed = datetime.fromisoformat(dt_pref)
                requested_day = dt_parsed.replace(tzinfo=tz) if dt_parsed.tzinfo is None else dt_parsed
            except ValueError:
                pass  # dt_pref con formato inválido — se ignora sin romper el flujo

        # ── Estrategia 1: list_events completo → filtro local por teléfono ────
        all_events = await calendar_service.list_events(
            start_date=now,
            end_date=future,
            max_results=50,
        )

        found_by_phone = [
            ev for ev in all_events
            if phone and _phone_in_text(phone, ev.get("description") or "")
        ]

        # ── Estrategia 2: búsqueda por nombre vía API de Google (q=) ─────────
        # Solo si hay nombre con suficientes caracteres para evitar resultados ruido.
        found_by_name: list[Dict[str, Any]] = []
        if patient_name and len(patient_name) >= 3:
            found_by_name = await calendar_service.search_events_by_query(
                q=patient_name,
                start_date=now,
                end_date=future,
            )

        # ── Combinar y deduplicar por event_id ───────────────────────────────
        seen_ids: set[str] = set()
        patient_events: list[Dict[str, Any]] = []
        for ev in found_by_phone + found_by_name:
            eid = ev.get("id")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                patient_events.append(ev)

        # ── Estrategia 3 (fallback para cancelar/reagendar): si hay eventos pero ninguno
        # coincide por teléfono/nombre, asumir que el primero es del paciente.
        # Esto cubre citas creadas manualmente (sin teléfono en descripción).
        # SOLO aplica para cancelar/reagendar (riesgo bajo: el usuario llama porque SU cita).
        # NO aplica para agendar (alto riesgo de falso positivo).
        if not patient_events and intent in ("cancelar", "reagendar") and all_events:
            logger.info(
                "node_check_existing_appointment: sin match por teléfono/nombre "
                "pero hay eventos — usando primer evento como fallback (intent: %s)",
                intent,
                phone=phone,
                patient_name=patient_name,
                total_events=len(all_events),
            )
            patient_events = all_events[:1]

        # ── Sin coincidencia real → NO hay cita ──────────────────────────────
        if not patient_events:
            logger.info(
                "node_check_existing_appointment: sin citas para el paciente",
                phone=phone,
                patient_name=patient_name,
                total_events_in_range=len(all_events),
            )
            # Calcular slots_available del día solicitado aunque no haya cita del paciente
            slots_avail = _compute_slots_available(all_events, requested_day, service, tz, state)
            return {
                **_no_appointment_found(),
                "calendar_slots_available": slots_avail,
            }

        # ── Refinar: coincidencia con servicio+día solicitado ─────────────────
        # Esto responde la pregunta "¿ya tiene una cita para ESTE servicio en ESTE día?"
        # Solo se aplica cuando ambos están disponibles (intent "agendar" normalmente).
        first_exact_match: Optional[Dict[str, Any]] = None
        if service and requested_day:
            for ev in patient_events:
                # Verificar que el evento es del mismo día calendario
                ev_start = _parse_event_start(ev, tz)
                if ev_start is None:
                    continue
                same_day = ev_start.date() == requested_day.date()
                # Verificar que el servicio aparece en el summary (formato: "servicio - Nombre")
                has_service = _service_in_text(service, ev.get("summary") or "")
                if same_day and has_service:
                    first_exact_match = _event_to_dict(ev)
                    break

        # ── Construir lista de citas del paciente (máx 3) ─────────────────────
        existing = [_event_to_dict(ev) for ev in patient_events[:3]]
        first = existing[0]

        # ── Calcular slots libres aproximados en el día solicitado ────────────
        slots_avail = _compute_slots_available(all_events, requested_day, service, tz, state)

        logger.info(
            "node_check_existing_appointment: citas del paciente encontradas",
            phone=phone,
            patient_name=patient_name,
            intent=intent,
            total_patient_events=len(patient_events),
            exact_match_found=first_exact_match is not None,
            matched_by_phone=len(found_by_phone) > 0,
            matched_by_name=len(found_by_name) > 0,
            slots_available=slots_avail,
        )

        return {
            "calendar_lookup_done": True,
            "calendar_appointment_found": True,
            "existing_appointments": existing,
            "calendar_total_for_patient": len(patient_events),
            "calendar_slots_available": slots_avail,
            "calendar_first_match": first_exact_match,
            # Poblar con la primera cita del paciente (útil para cancel/reschedule)
            "google_event_id": first["event_id"],
            "google_event_link": first["html_link"],
            "datetime_preference": first["start"],
        }

    except Exception as e:
        logger.error("node_check_existing_appointment: error", error=str(e))
        return _no_appointment_found()


def _no_appointment_found() -> Dict[str, Any]:
    """Dict base de respuesta cuando no se encuentra cita del paciente."""
    return {
        "calendar_lookup_done": True,
        "calendar_appointment_found": False,
        "existing_appointments": [],
        "calendar_total_for_patient": 0,
        "calendar_slots_available": None,
        "calendar_first_match": None,
        # Limpiar datos de cita anterior para no contaminar el contexto del LLM
        "google_event_id": None,
        "google_event_link": None,
        "appointment_id": None,
        "datetime_preference": None,
    }


def _compute_slots_available(
    all_events: list,
    requested_day: Optional[datetime],
    service: str,
    tz: "ZoneInfo",
    state: Dict[str, Any],
) -> Optional[int]:
    """
    Calcula de forma aproximada cuántos slots quedan libres en el día solicitado.

    Fórmula:
        total_slots = floor(business_minutes / service_duration)
        busy_slots  = número de eventos que ya existen en ese día
        available   = max(0, total_slots - busy_slots)

    Retorna None si no hay fecha de referencia.
    """
    if requested_day is None:
        return None

    from src.state import BUSINESS_HOURS, VALID_SERVICES, SLOT_MINUTES

    # Duración del servicio: usar la del estado o buscarla en VALID_SERVICES
    duration = state.get("service_duration")
    if not duration and service:
        duration = VALID_SERVICES.get(service)
    if not duration:
        duration = SLOT_MINUTES  # default 30 min

    business_minutes = (BUSINESS_HOURS[1] - BUSINESS_HOURS[0]) * 60  # ej. (18-9)*60 = 540
    total_slots = business_minutes // duration

    # Contar eventos que caen en el día solicitado
    busy = sum(
        1 for ev in all_events
        if (_parse_event_start(ev, tz) or datetime.min.replace(tzinfo=tz)).date()
        == requested_day.date()
    )

    return max(0, total_slots - busy)


async def node_lookup_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
) -> Dict[str, Any]:
    """
    Busca la cita real del cliente en Google Calendar.
    DETERMINISTA — cero LLM.

    Siempre consulta Google Calendar (no confía en memoria/estado).
    Busca en los próximos 60 días eventos cuya descripción contenga el número de teléfono.
    Actualiza el estado con el evento encontrado (google_event_id, datetime_preference, etc.)
    """
    if not calendar_service:
        logger.warning("node_lookup_appointment: sin calendar_service, saltando")
        return {}

    phone = state.get("phone_number", "")
    if not phone:
        logger.warning("node_lookup_appointment: sin phone_number")
        return {}

    try:
        from datetime import date
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Guayaquil")
        now = datetime.now(tz)
        future = now + timedelta(days=60)

        events = await calendar_service.list_events(
            start_date=now,
            end_date=future,
            max_results=50,
        )

        # Buscar eventos que contengan el teléfono del cliente en la descripción
        found = None
        for event in events:
            desc = event.get("description", "") or ""
            summary = event.get("summary", "") or ""
            if phone in desc or phone.lstrip("+") in desc:
                found = event
                break

        if not found:
            logger.info(
                "node_lookup_appointment: no se encontró cita para el cliente",
                phone=phone,
                events_checked=len(events),
            )
            # Limpiar estado de cita anterior para que el LLM no use datos viejos
            return {
                "google_event_id": None,
                "google_event_link": None,
                "appointment_id": None,
                "datetime_preference": None,
                "calendar_lookup_done": True,
                "calendar_appointment_found": False,
            }

        # Extraer datos del evento encontrado
        event_id = found.get("id")
        event_link = found.get("htmlLink")
        start_str = found.get("start", {}).get("dateTime") or found.get("start", {}).get("date")
        summary = found.get("summary", "")

        # Parsear datetime del evento
        dt_str = None
        if start_str:
            dt_str = start_str.split("+")[0].split("Z")[0]  # quitar timezone suffix

        logger.info(
            "node_lookup_appointment: cita encontrada en Calendar",
            phone=phone,
            event_id=event_id,
            start=dt_str,
            summary=summary,
        )

        return {
            "google_event_id": event_id,
            "google_event_link": event_link,
            "datetime_preference": dt_str,
            "calendar_lookup_done": True,
            "calendar_appointment_found": True,
        }

    except Exception as e:
        logger.error("node_lookup_appointment: error consultando Calendar", error=str(e))
        return {
            "calendar_lookup_done": True,
            "calendar_appointment_found": False,
        }


async def node_prepare_modification(state: ArcadiumState) -> Dict[str, Any]:
    """
    Nodo determinista que prepara el estado para flujos de cancelación/reagendamiento.
    Se ejecuta ANTES de detect_confirmation para setear confirmation_type
    basado en el intent ya detectado.

    Sin esto, edge_after_confirm recibe ctype=None y enruta a book_appointment
    en lugar de cancel_appointment.
    """
    intent = state.get("intent")
    if intent == "cancelar":
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "cancel",
            "current_step": "awaiting_cancel_confirmation",
        }
    elif intent == "reagendar":
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "reschedule",
            "current_step": "awaiting_reschedule_details",
        }
    return {}


async def node_reschedule_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    db_service=None,
) -> Dict[str, Any]:
    """
    Reagenda una cita: cancela el evento anterior y crea uno nuevo.
    DETERMINISTA — cero llamadas al LLM.
    """
    new_slot = state.get("selected_slot") or state.get("datetime_preference")
    if not new_slot:
        return {"last_error": "No hay nuevo slot para reagendar"}

    old_event_id = state.get("google_event_id")
    old_appt_id = state.get("appointment_id")

    try:
        # 1. Cancelar evento anterior en Google Calendar
        if calendar_service and old_event_id:
            try:
                await calendar_service.delete_event(old_event_id)
                logger.info("Evento anterior eliminado", event_id=old_event_id)
            except Exception as e:
                logger.warning("Error cancelando evento anterior en Calendar", error=str(e))

        # 2. Cancelar cita anterior en DB
        if db_service and old_appt_id:
            try:
                import uuid as _uuid
                await db_service.cancel_appointment(
                    session=None,
                    appointment_id=_uuid.UUID(old_appt_id),
                )
            except Exception as e:
                logger.warning("Error cancelando cita anterior en DB", error=str(e))

        # 3. Crear nuevo evento en Google Calendar
        dt = datetime.fromisoformat(new_slot)
        duration = state.get("service_duration", 30)
        end_dt = dt + timedelta(minutes=duration)
        patient = state.get("patient_name", "Paciente")
        service = state.get("selected_service", "consulta")

        new_event_id = None
        new_event_link = None
        if calendar_service:
            event = await calendar_service.create_event(
                title=f"{service} - {patient}",
                start_time=dt,
                end_time=end_dt,
                description=f"Paciente: {patient}\nTeléfono: {state.get('phone_number', '')}",
            )
            new_event_id = event.get("id")
            new_event_link = event.get("htmlLink")

        # 4. Crear nueva cita en DB
        new_appt_id = None
        if db_service:
            try:
                _, __, appt = await db_service.create_appointment(
                    session=None,
                    phone_number=state.get("phone_number", ""),
                    appointment_datetime=dt,
                    service_type=service,
                    project_id=state.get("project_id"),
                    metadata={"google_event_id": new_event_id, "patient_name": patient},
                )
                if appt:
                    new_appt_id = str(appt.id)
            except Exception as e:
                logger.warning("Error creando nueva cita en DB", error=str(e))

        logger.info(
            "Cita reagendada",
            patient=patient,
            service=service,
            new_slot=new_slot,
            old_event_id=old_event_id,
            new_event_id=new_event_id,
        )

        return {
            "appointment_id": new_appt_id or "pending_db",
            "google_event_id": new_event_id,
            "google_event_link": new_event_link,
            "confirmation_sent": True,
            "current_step": "resolution",
            # Limpiar estado de selección
            "awaiting_confirmation": False,
            "available_slots": [],
            "confirmation_type": None,
        }

    except Exception as e:
        return {
            "last_error": f"Error reagendando cita: {e}",
            "should_escalate": True,
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

        # Persistir SOLO el último HumanMessage y el último AIMessage limpio del turno.
        #
        # ¿Por qué no guardar todos los mensajes nuevos?
        # El flujo tool-calling produce: [HumanMessage, AIMessage(tool_calls=[...]),
        # ToolMessage, AIMessage(content="respuesta final")].
        # Si guardamos el AIMessage con tool_calls sin su ToolMessage correspondiente,
        # la próxima llamada a OpenAI falla: "assistant message with tool_calls must be
        # followed by tool messages". Guardando solo el par limpio (Human+AI_final)
        # la historia siempre es válida para la API.
        messages = state.get("messages", [])
        history_len = state.get("_history_len", 0)
        new_messages = messages[history_len:]

        from langchain_core.messages import AIMessage
        from langchain_core.messages import HumanMessage as HM

        # Encontrar el último HumanMessage y el último AIMessage sin tool_calls
        last_human = None
        last_ai = None
        for msg in reversed(new_messages):
            if last_ai is None and isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                last_ai = msg
            if last_human is None and isinstance(msg, HM):
                last_human = msg
            if last_human is not None and last_ai is not None:
                break

        to_save = [m for m in [last_human, last_ai] if m is not None]
        saved_count = 0
        for msg in to_save:
            try:
                await store.add_message(
                    phone, msg, project_id=state.get("project_id")
                )
                saved_count += 1
            except Exception as e:
                logger.warning("Error guardando mensaje", error=str(e))
        logger.info(
            "node_save_state: mensajes guardados",
            phone=phone,
            count=saved_count,
            history_len=history_len,
            total_messages=len(messages),
            new_messages_in_turn=len(new_messages),
        )

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
        # Tiempo actual en Ecuador — CRÍTICO para que el LLM no confunda
        # horas futuras con pasadas (el modelo usa UTC internamente)
        "hora_actual_ecuador": state.get("hora_actual", ""),
        "fecha_hoy_ecuador": state.get("fecha_hoy", ""),
        "dia_semana": state.get("dia_semana_hoy", ""),
        # Flujo de cita
        "intent": state.get("intent"),
        "patient_name": state.get("patient_name"),
        "missing_fields": state.get("missing_fields", []),
        "available_slots": state.get("available_slots", []),
        "selected_slot": state.get("selected_slot"),
        "confirmation_result": state.get("confirmation_result"),
        "confirmation_type": state.get("confirmation_type"),
        "awaiting_confirmation": state.get("awaiting_confirmation", False),
        "appointment_id": state.get("appointment_id"),
        "google_event_link": state.get("google_event_link"),
        "selected_service": state.get("selected_service"),
        "datetime_preference": state.get("datetime_preference"),
        "confirmation_sent": state.get("confirmation_sent", False),
        "last_error": state.get("last_error"),
        "conversation_turns": state.get("conversation_turns", 0),
        "semantic_memory_context": state.get("semantic_memory_context", ""),
        # Campos del forcing tool
        "calendar_lookup_done": state.get("calendar_lookup_done", False),
        "calendar_appointment_found": state.get("calendar_appointment_found", False),
        "existing_appointments": state.get("existing_appointments", []),
    }


# ═══════════════════════════════════════════════════════════
# NODO GENERATE_RESPONSE CON TOOL-CALLING
# ═══════════════════════════════════════════════════════════

from datetime import datetime
from typing import Dict, Any


def _format_datetime_readable(iso_str: str) -> str:
    """Convierte ISO string a texto legible: 'jueves 10 de abril a las 11:00'."""
    if not iso_str:
        return "fecha desconocida"
    try:
        dt = datetime.fromisoformat(iso_str)
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        return f"{dias[dt.weekday()]} {dt.day} de {meses[dt.month - 1]} a las {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str


def _format_slots(slots):
    """
    Convierte slots (ISO strings o dicts) a formato legible para WhatsApp (HH:MM).
    """
    formatted = []

    for slot in slots:
        if isinstance(slot, dict):
            start = slot.get("start")
        else:
            start = slot  # ISO string

        if isinstance(start, str):
            try:
                dt = datetime.fromisoformat(start)
            except ValueError:
                formatted.append(start)
                continue
        elif isinstance(start, datetime):
            dt = start
        else:
            continue

        formatted.append(dt.strftime("%H:%M"))

    return ", ".join(formatted)


async def node_generate_response_with_tools(
    state: Dict[str, Any],
    *,
    llm=None,
    vector_store=None,
) -> Dict[str, Any]:

    if not llm:
        from langchain_core.messages import AIMessage

        fallback = "Lo siento, hubo un error. Por favor intente nuevamente o llame a la clínica. 📞"
        return {"messages": [AIMessage(content=fallback)]}

    # ✅ contador de iteraciones
    iterations = state.get("_tool_iterations", 0) + 1

    # ✅ contexto
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

    # ✅ slots disponibles
    slots = context_dict.get("available_slots", [])
    if slots:
        readable = _format_slots(slots[:4])
        context_parts.append(f"Slots disponibles: {readable}")

    selected_slot = context_dict.get("selected_slot")
    if selected_slot:
        context_parts.append(f"Usuario eligió slot: {selected_slot}")

    ctype = context_dict.get("confirmation_type")
    confirmation_sent = context_dict.get("confirmation_sent", False)
    appt_id = context_dict.get("appointment_id")
    # CRÍTICO: usar google_event_id como fuente de verdad — appointment_id puede ser
    # "gcal_..." o "pending_db", pero solo google_event_id garantiza que el evento existe.
    google_event_id = context_dict.get("google_event_id")
    lookup_done = context_dict.get("calendar_lookup_done", False)
    cal_found = context_dict.get("calendar_appointment_found", False)
    existing_appts = context_dict.get("existing_appointments", [])
    awaiting = context_dict.get("awaiting_confirmation", False)

    # ── GUARDIAS CRÍTICAS: prevenir confirmaciones falsas ──────────────────
    # Regla global: si confirmation_sent=False, NINGUNA operación fue ejecutada.

    # 1. Agendar en progreso (slots mostrados, esperando selección del usuario)
    if awaiting and ctype == "book" and not confirmation_sent and not google_event_id:
        context_parts.append(
            "⚠️ INSTRUCCIÓN CRÍTICA: La cita AÚN NO ha sido creada en el sistema. "
            "Muestra los horarios disponibles y pide al usuario que confirme cuál prefiere. "
            "PROHIBIDO decir 'Su cita ha sido agendada' o frases similares hasta que el sistema lo confirme."
        )

    # 2. Reagendar/cancelar sin operación ejecutada — basado en INTENT (ignora ctype stale)
    # Esto cubre el caso donde ctype="book" de un turno anterior pero intent ya es reagendar/cancelar.
    if intent in ("reagendar", "cancelar") and not confirmation_sent:
        if lookup_done and not cal_found:
            context_parts.append(
                "⚠️ INSTRUCCIÓN CRÍTICA: Se verificó Google Calendar y NO existe ninguna cita activa "
                "para este usuario en el sistema. "
                "PROHIBIDO confirmar reagendamiento o cancelación. "
                "Informa que no hay cita activa y ofrece agendar una nueva si lo desea."
            )
        else:
            context_parts.append(
                "⚠️ INSTRUCCIÓN CRÍTICA: La operación de reagendamiento/cancelación AÚN NO se ha ejecutado. "
                "PROHIBIDO decir 'Su cita ha sido reagendada' o 'Su cita ha sido cancelada'. "
                "Solo usa esas frases cuando el sistema confirme explícitamente que la operación fue exitosa."
            )

    # ── Forcing tool: resultado de verificación en Calendar ──────────────
    if lookup_done and cal_found and existing_appts and intent == "agendar":
        # Usuario quiere agendar pero YA TIENE cita(s)
        lines = []
        for appt in existing_appts[:2]:
            svc_name = appt.get("summary", "cita")
            start_dt = appt.get("start", "")
            lines.append(f"• {svc_name} — {_format_datetime_readable(start_dt)}")
        appts_str = "\n".join(lines)
        context_parts.append(
            f"IMPORTANTE: Se consultó Google Calendar y el paciente YA TIENE cita(s) agendada(s):\n"
            f"{appts_str}\n"
            "Informa al usuario sobre su(s) cita(s) existente(s) y pregunta si desea "
            "reagendar, cancelar o agregar una cita adicional."
        )

    if confirmation_sent and ctype == "cancel":
        # Cancelación ejecutada exitosamente
        svc = context_dict.get("selected_service", "la cita")
        context_parts.append(
            f"La cita de {svc} ha sido cancelada exitosamente. Confirma al usuario con formato: "
            f'"Su cita de {{servicio}} ha sido cancelada exitosamente."'
        )
    elif confirmation_sent and ctype == "reschedule" and google_event_id:
        # Reagendamiento ejecutado exitosamente — verificado por google_event_id
        svc = context_dict.get("selected_service", "")
        slot = context_dict.get("selected_slot") or context_dict.get("datetime_preference", "")
        context_parts.append(
            f"La cita de {svc} ha sido reagendada para {slot}. Confirma con formato: "
            f'"Su cita de {{servicio}} ha sido reagendada para el {{día}} a las {{hora}}."'
        )
    elif confirmation_sent and google_event_id and (not ctype or ctype == "book"):
        # Booking nuevo ejecutado exitosamente — verificado por google_event_id en Calendar
        svc = context_dict.get("selected_service", "")
        slot = context_dict.get("selected_slot") or context_dict.get("datetime_preference", "")
        context_parts.append(
            f"Cita agendada exitosamente (Google Calendar ID: {google_event_id}): "
            f"{svc} el {slot}. Confirma con formato: "
            f'"Su cita de {{servicio}} ha sido agendada para el {{día}} a las {{hora}}."'
        )
    elif ctype == "cancel" and not confirmation_sent:
        # Esperando confirmación de cancelación
        svc = context_dict.get("selected_service", "la cita")
        lookup_done = context_dict.get("calendar_lookup_done", False)
        found = context_dict.get("calendar_appointment_found", False)
        if lookup_done and not found:
            context_parts.append(
                "IMPORTANTE: Se consultó Google Calendar y NO se encontró ninguna cita activa "
                "para este número de teléfono. Informa al usuario que no tienes citas agendadas "
                "a su nombre y ofrece agendar una nueva si lo desea."
            )
        else:
            dt = context_dict.get("datetime_preference", "")
            dt_info = f" del {dt}" if dt else ""
            context_parts.append(
                f"El usuario quiere CANCELAR su cita de {svc}{dt_info}. "
                "Pide confirmación explícita: '¿Confirma que desea cancelar su cita de {servicio}?'"
            )
    elif ctype == "reschedule" and not confirmation_sent:
        # Esperando nueva fecha para reagendar
        svc = context_dict.get("selected_service", "la cita")
        lookup_done = context_dict.get("calendar_lookup_done", False)
        found = context_dict.get("calendar_appointment_found", False)
        if lookup_done and not found:
            context_parts.append(
                "IMPORTANTE: Se consultó Google Calendar y NO se encontró ninguna cita activa "
                "para este número de teléfono. Informa al usuario que no tienes citas agendadas "
                "a su nombre y ofrece agendar una nueva si lo desea."
            )
        else:
            dt = context_dict.get("datetime_preference", "")
            dt_info = f" del {dt}" if dt else ""
            context_parts.append(
                f"El usuario quiere REAGENDAR su cita de {svc}{dt_info}. "
                "Pregunta por la nueva fecha y hora preferida."
            )

    error = context_dict.get("last_error")
    if error:
        context_parts.append(f"Error ocurrido: {error}. Sugiere llamar a la clínica.")
        # Si no hay appointment_id ni calendar credentials, guiar a la clínica
        if not appt_id and not context_dict.get("google_event_id"):
            context_parts.append(
                "No se encontró una cita activa para este usuario. "
                "Sugiere llamar directamente a la clínica para gestionar la cita."
            )

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

    # ✅ mensajes LLM
    from langchain_core.messages import SystemMessage

    history = state.get("messages", [])
    lm_messages = [SystemMessage(content=system_prompt)] + list(history)

    # ✅ tool binding
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
            response = await llm.ainvoke(lm_messages)

        return {
            "messages": [response],
            "_tool_iterations": iterations,
        }

    except Exception as e:
        logger.error("Error en node_generate_response_with_tools", error=str(e))

        from langchain_core.messages import AIMessage

        return {
            "messages": [
                AIMessage(content="Lo siento, hubo un error generando la respuesta.")
            ],
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
