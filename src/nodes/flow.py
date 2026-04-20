"""flow — nodos de flujo principal."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

from langchain_core.messages import HumanMessage, RemoveMessage

from src.state import ArcadiumState, DIAS_ES, TIMEZONE, get_missing_fields
from src.nodes_backup import _last_human_text

logger = structlog.get_logger("langgraph.nodes.flow")

_CHECKPOINT_HISTORY_LIMIT = 9  # Keep last 9 + 1 new = 10 total


async def node_entry(
    state: ArcadiumState,
    *,
    store=None,
) -> Dict[str, Any]:
    """
    Primer nodo del grafo.
    - Calcula fechas con Python (nunca LLM)
    - El historial viene del checkpointer (PostgresSaver); se recorta a 10 msgs
    - Si el turno anterior completó una operación (confirmation_sent=True), limpia
      el contexto de booking para que la nueva conversación empiece sin residuos
    - Incrementa conversation_turns
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
        "_slots_checked": False,  # se activa solo cuando node_check_availability corre
        "_calendar_refreshed": True,  # indica que se debe refrescar info del calendario
    }

    # Obtener el mensaje nuevo desde _incoming_message (enviado por agent.py)
    incoming = state.get("_incoming_message", "")
    if not incoming:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
                incoming = msg.content
                break

    new_message = HumanMessage(content=incoming)

    # ── Mensajes: el checkpointer restaura el historial completo.
    # Recortamos a HISTORY_LIMIT usando RemoveMessage para evitar crecimiento ilimitado,
    # luego añadimos el mensaje nuevo del turno actual.
    existing_messages = list(state.get("messages", []))
    msgs_out: list = []
    if len(existing_messages) > _CHECKPOINT_HISTORY_LIMIT:
        to_trim = existing_messages[:-_CHECKPOINT_HISTORY_LIMIT]
        msgs_out.extend(RemoveMessage(id=m.id) for m in to_trim)
        existing_messages = existing_messages[-_CHECKPOINT_HISTORY_LIMIT:]
    msgs_out.append(new_message)
    updates["messages"] = msgs_out
    updates["_history_len"] = len(existing_messages)

    logger.info(
        "node_entry: historial desde checkpointer",
        history_len=len(existing_messages),
        phone=state.get("phone_number", ""),
    )

    # ── Limpiar contexto de booking si el turno anterior lo completó.
    # confirmation_sent=True indica que la operación fue ejecutada (cita creada/cancelada).
    # Sin este reset, selected_service/awaiting_confirmation/etc. del turno anterior
    # contaminarían el nuevo flujo.
    if state.get("confirmation_sent"):
        updates.update({
            "confirmation_sent": False,
            "awaiting_confirmation": False,
            "confirmation_type": None,
            "confirmation_result": None,
            "rebook_after_cancel": None,
            "intent": None,
            "selected_service": None,
            "service_duration": None,
            "datetime_preference": None,
            "datetime_adjusted": False,
            "available_slots": [],
            "selected_slot": None,
            "appointment_id": None,
            "google_event_id": None,
            "google_event_link": None,
            "errors_count": 0,
        })

    # ── patient_name: fallback desde user_profiles si el checkpointer no lo tiene.
    # Cubre el primer turno de una sesión nueva sin checkpoint previo.
    if not state.get("patient_name") and store and hasattr(store, "get_user_profile"):
        try:
            phone = state.get("phone_number", "")
            profile = await store.get_user_profile(phone)
            if profile and profile.get("patient_name"):
                updates["patient_name"] = profile["patient_name"]
                # Calcular is_new_patient basado en total_conversations
                total_convs = profile.get("total_conversations", 0)
                updates["is_new_patient"] = (total_convs == 0)
                # Cargar preferencias del paciente
                updates["patient_preferences"] = profile.get("preferences", {})
                logger.info(
                    "node_entry: perfil de usuario cargado",
                    phone=phone,
                    patient_name=profile["patient_name"],
                    is_new=updates["is_new_patient"],
                )
        except Exception:
            pass

    # Escalación por número de turns
    if updates["conversation_turns"] >= 10:
        updates["should_escalate"] = True

    # Recalcular missing_fields después de cualquier reset o cambio
    merged_state = {**state, **updates}
    from src.state import get_missing_fields
    updates["missing_fields"] = get_missing_fields(merged_state)

    return updates



async def node_route_intent(state: ArcadiumState) -> Dict[str, Any]:
    """
    Detecta intención por keywords (determinista).
    Si no hay match suficiente → marca para fallback LLM.

    FIX: Si el estado ya tiene un intent de la sesión en curso (flujo no terminado)
    y el mensaje actual no aporta un intent diferente (ej. solo responde una pregunta),
    conservar el intent existente para no romper el flujo multi-turno.
    """
    from src.intent_router import route_by_keywords

    text = _last_human_text(state)
    detected = route_by_keywords(text)

    # Si no detectamos intent nuevo pero ya hay uno del turno anterior, conservarlo.
    # Condición: flujo no completado (confirmation_sent=False) y hay missing_fields.
    existing_intent = state.get("intent")
    if (
        detected is None
        and existing_intent
        and existing_intent != "otro"
        and not state.get("confirmation_sent")
        and state.get("missing_fields")
    ):
        detected = existing_intent

    updates: Dict[str, Any] = {
        "intent": detected,
        "current_step": "route_intent_done",
    }

    # Limpiar bandera de refresco de calendario después de detectar intent
    if state.get("_calendar_refreshed"):
        updates["_calendar_refreshed"] = False

    logger.info(
        "node_route_intent: intent detectado",
        intent=detected,
        text_preview=text[:60],
    )

    return updates



async def node_save_state(
    state: ArcadiumState,
    *,
    store=None,
) -> Dict[str, Any]:
    """
    Persiste el estado actual en DB a través del store.
    Guarda mensajes nuevos y actualiza user_profiles.
    """
    # Guard: LangGraph inyecta su BatchedStore cuando el param se llama "store".
    # Si el store no tiene nuestros métodos custom, saltar silenciosamente.
    if not store or not hasattr(store, "save_agent_state"):
        return {}

    try:
        phone = state.get("phone_number", "")

        # FIX: usar filter_persistent_state para excluir campos transitorios
        # (fechas, current_step, _extract_data_calls, available_slots, etc.)
        # que no deben restaurarse en sesiones futuras.
        from src.state import filter_persistent_state

        await store.save_agent_state(phone, filter_persistent_state(state))

        # Mensajes persistidos por el checkpointer (PostgresSaver) — no duplicar aquí.

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


