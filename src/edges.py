"""
Funciones de routing (edges) del grafo.

Son funciones puras que leen el estado y retornan el nombre del siguiente nodo.
NUNCA llaman al LLM. NUNCA mutan el estado.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from src.state import get_missing_fields


def edge_after_route_intent(state: Dict[str, Any]) -> str:
    """
    Después de route_intent, decide a dónde ir.

    PRIORIDAD MÁXIMA: si estamos esperando selección/confirmación de un slot
    (awaiting_confirmation=True), el mensaje del usuario debe ir a detect_confirmation
    independientemente de su intent. Esto evita el bug donde el Turn 2 ("a las 10:00")
    vuelve al inicio del flujo (check_missing → check_availability) en lugar de
    procesar la selección del usuario y llamar a book_appointment.

    Excepción: si el usuario cambia explícitamente de intención a cancelar/reagendar,
    dejamos que el flujo de modificación tome precedencia.
    """
    intent = state.get("intent")
    awaiting = state.get("awaiting_confirmation", False)
    ctype = state.get("confirmation_type")

    # Si esperamos selección de slot o confirmación para agendar,
    # y el usuario no está cambiando a un flujo diferente (cancelar/reagendar)
    if awaiting and ctype in ("book", None) and intent not in ("cancelar", "reagendar"):
        return "detect_confirmation"

    # Si esperamos confirmación de cancel o reschedule
    if awaiting and ctype in ("cancel", "reschedule"):
        return "detect_confirmation"

    if not intent:
        return "extract_intent"  # Fallback LLM

    # Forcing tool: verificar Calendar antes de operar sobre citas
    if intent in ("agendar", "cancelar", "reagendar"):
        return "check_existing_appointment"

    if intent == "consultar":
        return "check_availability"

    return "generate_response"  # "otro" o no reconocido


def edge_after_check_missing(state: Dict[str, Any]) -> str:
    """
    Después de verificar campos faltantes.
    Si extract_data ya fue llamado y no mejoró los campos, ir a generate_response.
    """
    missing = state.get("missing_fields", [])
    has_service = state.get("selected_service")
    has_datetime = state.get("datetime_preference")
    extract_calls = state.get("_extract_data_calls", 0)

    if missing:
        # Si extract_data ya fue llamado y faltan campos, pedir más info
        # en vez de re-extraer (prevenir loop infinito)
        if extract_calls > 0:
            return "generate_response"
        return "extract_data"

    # Todos los datos presentes → consultar disponibilidad
    if has_service and has_datetime:
        return "check_availability"

    return "generate_response"


def edge_after_confirm(state: Dict[str, Any]) -> str:
    """
    Después de detect_confirmation.

    Para reagendar: si el usuario eligió slot nuevo → ejecutar reagendamiento.
    Para cancelar: si el usuario confirmó → ejecutar cancelación.
    """
    result = state.get("confirmation_result", "unknown")
    ctype = state.get("confirmation_type")
    selected_slot = state.get("selected_slot")

    if result == "yes":
        if ctype == "book":
            return "book_appointment"
        if ctype == "cancel":
            return "cancel_appointment"
        if ctype == "reschedule":
            # El usuario confirmó reagendar; si ya tiene slot → ejecutar.
            # Si no tiene slot, pedir nueva fecha en generate_response.
            if selected_slot:
                return "reschedule_appointment"
            return "generate_response"
        # Confirmación genérica (sin ctype) → intentar agendar
        return "book_appointment"

    if result == "slot_choice":
        if ctype == "reschedule" and selected_slot:
            # Usuario eligió el nuevo slot para reagendar → ejecutar
            return "reschedule_appointment"
        if ctype == "book" and selected_slot:
            # Usuario eligió slot para agendar → crear cita directamente
            # (slot ya fue validado por extract_slot_from_text, no necesita revalidar)
            return "book_appointment"
        if selected_slot:
            return "validate_slot"

    if result == "no":
        return "generate_response"  # "¿Qué fecha prefiere?"

    # "unknown" — no se entendió
    return "generate_response"


def edge_after_extract_data(state: Dict[str, Any]) -> str:
    """
    Después de extraer datos por LLM.
    """
    missing = state.get("missing_fields", [])

    # Si aún faltan campos y tenemos servicio+fecha, ir a disponibilidad
    has_service = state.get("selected_service")
    has_datetime = state.get("datetime_preference")

    if not missing and has_service and has_datetime:
        return "adjust_weekend"

    # Extraer datos pero aún incompleto → check missing de nuevo
    if has_service or has_datetime:
        return "check_missing"

    # Nada nuevo → pedir más info
    return "generate_response"


def edge_after_adjust_weekend(state: Dict[str, Any]) -> str:
    """
    Después de ajuste de fin de semana, verificar disponibilidad.
    """
    return "check_missing"


def edge_after_check_existing(state: Dict[str, Any]) -> str:
    """
    Después de node_check_existing_appointment (forcing tool).

    - Para intent "agendar":
        · Encontró cita  → generate_response (informar, ofrecer reagendar/cancelar)
        · No encontró    → check_missing (continuar flujo de agendamiento)

    - Para intent "cancelar" / "reagendar":
        · Encontró cita  → prepare_modification (tiene el event_id para operar)
        · No encontró    → generate_response (informar que no hay cita)
    """
    intent = state.get("intent", "")
    found = state.get("calendar_appointment_found", False)

    if intent == "agendar":
        return "generate_response" if found else "check_missing"

    # cancelar / reagendar
    return "prepare_modification" if found else "generate_response"


def edge_after_validate(state: Dict[str, Any]) -> str:
    """
    Después de validar slot elegido.
    """
    last_error = state.get("last_error")
    if last_error and not state.get("selected_slot"):
        return "generate_response"  # Slot inválido → pedir de nuevo
    return "generate_response"  # Pedir confirmación final


def edge_should_escalate(state: Dict[str, Any]) -> Literal["escalate_to_human", "continue"]:
    """
    Decide si hay que escalar a humano o continuar normalmente.
    """
    if state.get("should_escalate"):
        return "escalate_to_human"

    if state.get("errors_count", 0) >= 3:
        return "escalate_to_human"

    if state.get("conversation_turns", 0) >= 10:
        return "escalate_to_human"

    return "continue"
