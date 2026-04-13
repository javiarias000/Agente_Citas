"""
Interceptor determinista de confirmaciones para el grafo V2.

Antes de pasar al LLM, verifica si el usuario está confirmando una operación
pendiente (awaiting_confirmation=True). Si la respuesta es clara (sí/no/hora),
actúa directamente sin llamar al LLM.

Casos deterministas:
  - awaiting "book" + YES + 1 slot → inyecta book_appointment
  - awaiting "book" + tiempo exacto en slots → inyecta book_appointment
  - awaiting "cancel" + YES + google_event_id conocido → inyecta cancel_appointment
  - awaiting cualquiera + NO → limpia estado, pasa al LLM

Casos que pasan al LLM:
  - awaiting "book" + YES + múltiples slots (ambiguo cuál eligió)
  - awaiting "book" + tiempo que no matchea ningún slot
  - Sin awaiting_confirmation
  - Cualquier caso ambiguo
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import AIMessage

from src.intent_router import detect_confirmation, extract_slot_from_text
from src.state import ArcadiumState

logger = structlog.get_logger("langgraph.v2.interceptor")


def _make_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Construye un tool_call dict compatible con LangChain AIMessage.tool_calls."""
    return {
        "name": name,
        "args": args,
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "tool_call",
    }


def _get_last_human_text(state: ArcadiumState) -> str:
    """Extrae el texto del último mensaje humano."""
    from langchain_core.messages import HumanMessage
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and msg.content:
            return str(msg.content).strip()
    return state.get("_incoming_message", "")


def _inject_book_appointment(state: ArcadiumState, slot_iso: str) -> Dict[str, Any]:
    """
    Inyecta una llamada a book_appointment directamente en el estado,
    salteando el LLM. Produce un AIMessage con tool_calls para que
    node_execute_tools pueda enlazar los ToolMessages correctamente.
    """
    service = state.get("selected_service") or "consulta"
    patient_name = state.get("patient_name") or "Paciente"
    phone = state.get("phone_number", "")

    tc = _make_tool_call("book_appointment", {
        "slot_iso": slot_iso,
        "service": service,
        "patient_name": patient_name,
        "phone_number": phone,
    })

    # AIMessage con tool_calls para que el historial sea consistente
    ai_msg = AIMessage(
        content="",
        tool_calls=[tc],
    )

    logger.info(
        "confirmation_interceptor: booking determinista",
        slot=slot_iso,
        service=service,
        patient=patient_name,
    )

    return {
        "messages": [ai_msg],
        "pending_tool_calls": [tc],
        "_tool_iterations": 1,
        "selected_slot": slot_iso,
    }


def _inject_cancel_appointment(state: ArcadiumState, event_id: str) -> Dict[str, Any]:
    """
    Inyecta una llamada a cancel_appointment directamente en el estado.
    """
    phone = state.get("phone_number", "")

    tc = _make_tool_call("cancel_appointment", {
        "event_id": event_id,
        "phone_number": phone,
    })

    ai_msg = AIMessage(
        content="",
        tool_calls=[tc],
    )

    logger.info(
        "confirmation_interceptor: cancel determinista",
        event_id=event_id,
    )

    return {
        "messages": [ai_msg],
        "pending_tool_calls": [tc],
        "_tool_iterations": 1,
    }


async def node_confirmation_interceptor(state: ArcadiumState) -> Dict[str, Any]:
    """
    Nodo determinista (sin LLM). Se ejecuta entre entry_v2 y react_loop.

    Si hay confirmación pendiente clara → inyecta el tool_call directamente.
    Si no → retorna {} para que el grafo pase al LLM normalmente.
    """
    awaiting = state.get("awaiting_confirmation", False)
    if not awaiting:
        return {}

    conf_type = state.get("confirmation_type")
    last_text = _get_last_human_text(state)
    if not last_text:
        return {}

    result = detect_confirmation(last_text)
    available_slots: List[str] = state.get("available_slots", [])

    # ── Flujo: AGENDAR ────────────────────────────────────────────────────────
    if conf_type == "book":
        if result == "no":
            logger.info("confirmation_interceptor: usuario rechazó booking")
            return {
                "awaiting_confirmation": False,
                "confirmation_type": None,
            }

        if result == "yes":
            if len(available_slots) == 1:
                # Un solo slot disponible — "sí" confirma ese slot
                return _inject_book_appointment(state, available_slots[0])
            else:
                # Múltiples slots — "sí" es ambiguo, el LLM pregunta cuál
                logger.info(
                    "confirmation_interceptor: 'sí' con múltiples slots — pasa al LLM",
                    slots_count=len(available_slots),
                )
                return {}

        if result == "slot_choice":
            # Usuario eligió una hora específica
            selected = extract_slot_from_text(last_text, available_slots)
            if selected:
                return _inject_book_appointment(state, selected)
            else:
                logger.info(
                    "confirmation_interceptor: slot_choice sin match exacto — pasa al LLM",
                    text=last_text,
                    slots=available_slots,
                )
                return {}

        # result == "unknown" — deja al LLM manejar
        return {}

    # ── Flujo: CANCELAR ───────────────────────────────────────────────────────
    if conf_type == "cancel":
        google_event_id = state.get("google_event_id")

        if result == "no":
            logger.info("confirmation_interceptor: usuario rechazó cancelación")
            return {
                "awaiting_confirmation": False,
                "confirmation_type": None,
            }

        if result == "yes" and google_event_id:
            return _inject_cancel_appointment(state, google_event_id)

        # Sin event_id o respuesta ambigua — LLM maneja
        return {}

    # ── Flujo: REAGENDAR u otro ───────────────────────────────────────────────
    # Deja al LLM — reschedule requiere 2 tools (lookup + reschedule), más complejo
    return {}
