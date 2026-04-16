"""
Nodos del grafo V2 (arquitectura ReAct).

Filosofía:
  - 1 LLM call por turno (node_react_loop)
  - Todas las operaciones de Calendar/DB son tools que el LLM invoca
  - Confirmaciones de éxito son deterministas (sin LLM)
  - Prompt corto y claro — sin "truth gates" de 400 líneas
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import structlog
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage, HumanMessage

from src.state import ArcadiumState, TRANSIENT_FIELDS
from src.schemas_v2 import extract_state_updates

logger = structlog.get_logger("langgraph.v2")

TIMEZONE = ZoneInfo("America/Guayaquil")
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MAX_TOOL_ITERATIONS = 6
MAX_HISTORY_MESSAGES = 10


def _sanitize_message_history(messages: List) -> List:
    """Remueve AIMessages con tool_calls huérfanos (sin ToolMessage responses)."""
    sanitized = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            expected_ids = {tc["id"] for tc in msg.tool_calls}
            j = i + 1
            found_ids = set()
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                found_ids.add(messages[j].tool_call_id)
                j += 1
            if expected_ids <= found_ids:
                sanitized.extend(messages[i:j])
                i = j
            else:
                logger.warning("node_react_loop: AIMessage con tool_calls huérfanos — descartado")
                i = j
        else:
            sanitized.append(msg)
            i += 1
    return sanitized


# ══════════════════════════════════════════════════════════════════════════════
# NODO 1: ENTRY
# ══════════════════════════════════════════════════════════════════════════════

async def node_entry_v2(state: ArcadiumState, *, store=None) -> Dict[str, Any]:
    """
    Inicializa el turno:
    - Carga historial desde el store
    - Calcula fechas de Ecuador
    - Restaura campos persistentes del estado anterior
    - Inyecta el mensaje entrante al historial
    """
    phone = state.get("phone_number", "")
    incoming = state.get("_incoming_message", "")

    updates: Dict[str, Any] = {
        "_tool_iterations": 0,
        "pending_tool_calls": [],
    }

    # ── Fechas de Ecuador (siempre recalcular) ────────────────────────────────
    now_ec = datetime.now(TIMEZONE)
    tomorrow = now_ec + timedelta(days=1)
    updates.update({
        "fecha_hoy": now_ec.strftime("%Y-%m-%d"),
        "hora_actual": now_ec.strftime("%H:%M"),
        "dia_semana_hoy": DIAS_ES[now_ec.weekday()],
        "manana_fecha": tomorrow.strftime("%Y-%m-%d"),
        "manana_dia": DIAS_ES[tomorrow.weekday()],
    })

    # ── Historial desde store ─────────────────────────────────────────────────
    history: List = []
    if store and phone and hasattr(store, "get_history"):
        try:
            history = await store.get_history(phone) or []
            history = history[-MAX_HISTORY_MESSAGES:]
        except Exception as e:
            logger.warning("node_entry_v2: error cargando historial", error=str(e))

    # ── Restaurar estado previo ────────────────────────────────────────────────
    if store and phone and hasattr(store, "get_agent_state"):
        try:
            prev = await store.get_agent_state(phone) or {}
            for field in [
                "patient_name", "conversation_turns", "awaiting_confirmation",
                "confirmation_type", "errors_count",
            ]:
                if field in prev and prev[field] is not None:
                    updates[field] = prev[field]

            # Campos transientes solo si hay flujo en progreso
            if prev.get("awaiting_confirmation"):
                for field in [
                    "selected_service", "service_duration", "intent",
                    "datetime_preference", "available_slots", "selected_slot",
                    "appointment_id", "google_event_id", "google_event_link",
                    "confirmation_type",
                ]:
                    if field in prev and prev[field] is not None:
                        updates[field] = prev[field]
        except Exception as e:
            logger.warning("node_entry_v2: error restaurando estado", error=str(e))

    # ── Mensaje entrante → HumanMessage ──────────────────────────────────────
    messages = list(history)
    if incoming:
        messages.append(HumanMessage(content=incoming))

    updates["messages"] = messages
    updates["_history_len"] = len(history)
    updates["conversation_turns"] = updates.get("conversation_turns", 0) + 1

    logger.info(
        "node_entry_v2",
        phone=phone,
        history_len=len(history),
        turns=updates["conversation_turns"],
    )

    return updates


# ══════════════════════════════════════════════════════════════════════════════
# NODO 2: REACT LOOP (única llamada LLM)
# ══════════════════════════════════════════════════════════════════════════════

def _build_system_prompt_v2(state: ArcadiumState) -> str:
    """
    System prompt compacto (~50 líneas vs. ~400 del V1).
    El estado es la fuente de verdad; el prompt solo define persona + protocolo.
    """
    fecha = state.get("fecha_hoy", "")
    hora = state.get("hora_actual", "")
    dia = state.get("dia_semana_hoy", "")
    manana_fecha = state.get("manana_fecha", "")
    manana_dia = state.get("manana_dia", "")
    phone = state.get("phone_number", "")
    patient_name = state.get("patient_name") or "el paciente"
    awaiting = state.get("awaiting_confirmation", False)
    conf_type = state.get("confirmation_type")
    available_slots = state.get("available_slots", [])
    google_event_id = state.get("google_event_id")
    existing_appts = state.get("existing_appointments", [])

    # Bloque de perfil del paciente (siempre incluido si existe)
    semantic = state.get("semantic_memory_context", "")
    patient_block = f"\n{semantic}\n" if semantic else ""

    # Bloque de estado de flujo activo
    flow_block = ""
    if awaiting and conf_type == "book" and available_slots:
        slots_display = "\n".join(
            f"  • {s}" for s in available_slots[:5]
        )
        flow_block = (
            f"\n⚠️ FLUJO EN PROGRESO: Ya mostraste estos slots al paciente:\n"
            f"{slots_display}\n"
            f"El mensaje actual ES SU RESPUESTA. Si dice 'sí' o elige una hora → "
            f"llama book_appointment. Si dice 'no' o pide otra fecha → "
            f"llama check_availability con la nueva fecha."
        )
    elif awaiting and conf_type == "cancel" and state.get("existing_appointments"):
        appts = state.get("existing_appointments", [])
        appts_display = "\n".join([f"  • {a.get('summary')} ({a.get('start')})" for a in appts])
        flow_block = (
            f"\n⚠️ FLUJO EN PROGRESO: Encontraste estas citas:\n{appts_display}\n"
            f"Si el paciente confirma cancelar → llama cancel_appointment(event_id, phone_number). "
            f"Si quiere reagendar → llama check_availability + reschedule_appointment."
        )
    elif awaiting and conf_type == "reschedule" and state.get("existing_appointments"):
        appt = state.get("existing_appointments", [])[0]
        flow_block = (
            f"\n⚠️ FLUJO EN PROGRESO: El paciente quiere reagendar la cita: "
            f"{appt.get('summary','')} — {appt.get('start','')}. "
            f"event_id: {appt.get('event_id','')}\n"
            f"Llama check_availability para mostrar nuevos slots. "
            f"Cuando confirme → reschedule_appointment."
        )
    elif google_event_id and state.get("confirmation_sent"):
        # Esto no debería llegar al LLM — node_format_response lo intercepta
        flow_block = (
            f"\n✅ La operación ya fue ejecutada (event_id: {google_event_id}). "
            f"Confirma al paciente con los datos del sistema."
        )

    # Bloque de soporte total (evitar alucinaciones de incapacidad)
    support_block = """
    SOPORTE TOTAL:
    • PUEDES cancelar citas usando cancel_appointment.
    • PUEDES reagendar citas usando reschedule_appointment.
    • NUNCA digas "no puedo cancelar" o "llame a la clínica para cancelar".
    • Si tienes la tool, tienes la capacidad.
    """

    return f"""Eres Deyy, recepcionista de Arcadium Rehabilitación Oral (Ecuador).
Tono: cálido, formal (usted), conciso. Máx 2-3 líneas. Máx 2 emojis del set: 😊👋📅✅❌🦷⏰📞
{support_block}

TIEMPO ACTUAL (Ecuador, UTC-5):
  Hoy: {dia} {fecha} — Hora: {hora}
  Mañana: {manana_dia} {manana_fecha}
  NUNCA uses UTC ni inventes fechas. Usa siempre las fechas de arriba.

DATOS DEL PACIENTE:
  Teléfono sesión: {phone}
  Nombre conocido: {patient_name}
{patient_block}
PROTOCOLO DE HERRAMIENTAS:

AGENDAR cita:
  1. check_availability(date_iso, service) → obtiene slots reales del calendario
  2. Muestra máx 4 slots al paciente (formato: "lunes 14/04 a las 10:00")
  3. Paciente confirma uno → book_appointment(slot_iso, service, patient_name, phone_number)
  4. NUNCA llames book_appointment sin confirmación explícita del paciente.

CANCELAR cita:
  1. lookup_appointments(phone_number) → obtiene event_id real
  2. Confirma con el paciente cuál cita quiere cancelar
  3. Paciente confirma → cancel_appointment(event_id, phone_number)

REAGENDAR cita:
  1. lookup_appointments(phone_number) → event_id actual
  2. check_availability → nuevos slots
  3. Paciente confirma nuevo slot → reschedule_appointment(...)

MEMORIA:
  - Llama save_patient_memory SILENCIOSAMENTE cuando el paciente revele:
    alergias, condiciones médicas, preferencias permanentes, tratamientos activos.
  - type='user' para datos permanentes del perfil.
  - type='feedback' para preferencias y correcciones.
  - type='project' para tratamientos en curso.
  - type='reference' para IDs de citas creadas.
  - SIEMPRE pasa phone_number={phone} a save_patient_memory.
{flow_block}
REGLAS CRÍTICAS:
  • Si confirmation_sent=True (en el contexto) → la operación YA se ejecutó.
    Solo confirma el resultado al paciente.
  • Si una tool devuelve error → informa brevemente y sugiere llamar 📞.
  • Si el paciente da un dato importante → guárdalo con save_patient_memory.
  • NUNCA inventes event_ids, horarios, ni datos que no vengan de las tools."""


async def node_react_loop(
    state: ArcadiumState,
    *,
    llm_with_tools,
) -> Dict[str, Any]:
    """
    Única llamada LLM del turno. El LLM decide:
    - Qué tools invocar (si alguna)
    - Cuál es la respuesta final (si no necesita tools)
    """
    iterations = state.get("_tool_iterations", 0)

    # Construir mensajes
    system_prompt = _build_system_prompt_v2(state)

    # Sanitizar historial: remover AIMessages con tool_calls huérfanos
    raw_history = list(state.get("messages", []))
    MAX_HISTORY = 12
    if len(raw_history) > MAX_HISTORY:
        raw_history = raw_history[-MAX_HISTORY:]

    sanitized = _sanitize_message_history(raw_history)
    lm_messages = [SystemMessage(content=system_prompt)] + sanitized

    try:
        response = await llm_with_tools.ainvoke(lm_messages)
    except Exception as e:
        logger.error("node_react_loop: error LLM", error=str(e))
        return {
            "messages": [AIMessage(
                content="Lo siento, hubo un error técnico. Por favor llame a la clínica. 📞"
            )],
            "pending_tool_calls": [],
            "last_error": str(e),
            "should_escalate": True,
        }

    tool_calls = getattr(response, "tool_calls", []) or []

    logger.info(
        "node_react_loop",
        iteration=iterations,
        tool_calls=[tc.get("name") if isinstance(tc, dict) else tc.name
                    for tc in tool_calls],
        has_text=bool(response.content),
    )

    if tool_calls:
        return {
            "messages": [response],
            "pending_tool_calls": tool_calls,
            "_tool_iterations": iterations + 1,
        }
    else:
        return {
            "messages": [response],
            "pending_tool_calls": [],
            "_llm_response_text": response.content,
        }


# ══════════════════════════════════════════════════════════════════════════════
# NODO 3: EXECUTE TOOLS
# ══════════════════════════════════════════════════════════════════════════════

async def node_execute_tools(
    state: ArcadiumState,
    *,
    tool_map: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ejecuta todos los tool calls pendientes y retorna ToolMessages + state updates.
    Centraliza la lógica que antes estaba en 15+ nodos separados.
    """
    pending = state.get("pending_tool_calls", [])
    if not pending:
        return {}

    tool_messages = []
    combined_state_updates: Dict[str, Any] = {}

    for tc in pending:
        tool_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        tool_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
        tool_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)

        tool_fn = tool_map.get(tool_name)
        if not tool_fn:
            logger.warning("node_execute_tools: tool desconocido", tool_name=tool_name)
            if tool_id:
                tool_messages.append(ToolMessage(
                    content=json.dumps({"error": f"Tool '{tool_name}' no encontrado"}),
                    tool_call_id=tool_id,
                ))
            continue

        try:
            result = await tool_fn.ainvoke(tool_args)

            # Serializar resultado
            if hasattr(result, "model_dump_json"):
                result_json = result.model_dump_json()
            else:
                result_json = json.dumps(str(result))

            # Extraer actualizaciones de estado
            state_updates = extract_state_updates(tool_name, result)
            combined_state_updates.update(state_updates)

            # Flag de tipo de operación para formato de respuesta determinista
            if getattr(result, "success", False):
                if tool_name == "book_appointment":
                    combined_state_updates["_operation_success_type"] = "book"
                elif tool_name == "cancel_appointment":
                    combined_state_updates["_operation_success_type"] = "cancel"
                elif tool_name == "reschedule_appointment":
                    combined_state_updates["_operation_success_type"] = "reschedule"

            logger.info(
                "node_execute_tools: tool ejecutado",
                tool=tool_name,
                success=getattr(result, "success", True),
            )

            if tool_id:
                tool_messages.append(ToolMessage(
                    content=result_json,
                    tool_call_id=tool_id,
                ))

        except Exception as e:
            logger.error("node_execute_tools: error", tool=tool_name, error=str(e))
            if tool_id:
                tool_messages.append(ToolMessage(
                    content=json.dumps({"error": str(e)}),
                    tool_call_id=tool_id,
                ))

    return {
        "messages": tool_messages,
        "pending_tool_calls": [],
        **combined_state_updates,
    }


# ══════════════════════════════════════════════════════════════════════════════
# NODO 4: FORMAT RESPONSE (determinista)
# ══════════════════════════════════════════════════════════════════════════════

def _format_slot_for_confirmation(slot_iso: str) -> str:
    """Convierte slot ISO a display: 'lunes 14/04 a las 10:00'."""
    try:
        dt = datetime.fromisoformat(slot_iso)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        dia = DIAS_ES[dt.weekday()]
        return f"{dia} {dt.strftime('%d/%m a las %H:%M')}"
    except Exception:
        return slot_iso


def _build_confirmation_response(
    operation_type: str,
    service: str = "",
    slot_iso: str = "",
    patient_name: str = "",
) -> str:
    """Template unificado para todas las confirmaciones de operación."""
    if operation_type == "cancel":
        return "Su cita ha sido cancelada exitosamente. ¿Hay algo más en lo que pueda ayudarle? 😊"
    elif operation_type == "book":
        slot_display = _format_slot_for_confirmation(slot_iso)
        nombre = f", {patient_name}" if patient_name and patient_name.lower() != "paciente" else ""
        return (
            f"✅ Listo{nombre}. Su cita de {service} queda agendada para el "
            f"{slot_display}. ¡Le esperamos! 😊"
        )
    elif operation_type == "reschedule":
        slot_display = _format_slot_for_confirmation(slot_iso)
        return (
            f"✅ Su cita de {service} ha sido reagendada para el {slot_display}. "
            f"¡Hasta pronto! 😊"
        )
    return "Operación completada. 😊"


async def node_format_response(state: ArcadiumState) -> Dict[str, Any]:
    """
    Intercepción determinista para estados de éxito conocidos.
    Si _operation_success_type está set → usar template determinista.
    Si no → pasar respuesta del LLM tal cual.
    """
    op_type = state.get("_operation_success_type")

    if op_type:
        service = state.get("selected_service", "la consulta")
        slot = state.get("selected_slot") or state.get("datetime_preference", "")
        patient = state.get("patient_name", "")
        text = _build_confirmation_response(op_type, service=service, slot_iso=slot, patient_name=patient)
        logger.info("node_format_response: respuesta determinista", operation=op_type)
        return {
            "messages": [AIMessage(content=text)],
            "_final_response": text,
        }

    # Sin operación exitosa → usar la respuesta del LLM
    llm_text = state.get("_llm_response_text", "")
    if llm_text:
        return {"_final_response": llm_text}

    # Fallback: buscar último AIMessage
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return {"_final_response": msg.content}

    return {"_final_response": "Lo siento, no pude procesar su mensaje. 📞"}


# ══════════════════════════════════════════════════════════════════════════════
# NODO 5: SAVE STATE
# ══════════════════════════════════════════════════════════════════════════════

async def node_save_state_v2(state: ArcadiumState, *, store=None) -> Dict[str, Any]:
    """
    Persiste los campos no transientes del estado.
    Guarda el último mensaje del agente en el historial.
    """
    if not store or not hasattr(store, "save_agent_state"):
        return {}

    phone = state.get("phone_number", "")
    if not phone:
        return {}

    try:
        # Guardar historial
        final_response = state.get("_final_response", "")
        if final_response:
            incoming = state.get("_incoming_message", "")
            if incoming:
                await store.add_message(phone, HumanMessage(content=incoming))
            await store.add_message(phone, AIMessage(content=final_response))

        # Persistir estado (excluir campos transientes)
        persistent = {
            k: v for k, v in state.items()
            if k not in TRANSIENT_FIELDS and not k.startswith("_")
        }
        # También excluir campos V2 específicos
        for skip in ("pending_tool_calls", "_llm_response_text", "_final_response"):
            persistent.pop(skip, None)

        await store.save_agent_state(phone, persistent)

        logger.info(
            "node_save_state_v2",
            phone=phone,
            fields=len(persistent),
        )

    except Exception as e:
        logger.error("node_save_state_v2: error", error=str(e))

    return {}
