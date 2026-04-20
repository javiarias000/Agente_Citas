"""
Módulo nodes refactorizado.

Estructura actual:
- cancel.py: node_cancel_appointment ✓ migrado
- reschedule.py: node_reschedule_appointment, node_prepare_modification ✓ migrado
- booking.py: node_book_appointment, node_detect_confirmation, node_validate_and_confirm ✓ migrado
- _helpers.py: funciones _privadas compartidas (próximo)
- flow.py: node_entry, node_route_intent, node_save_state (próximo)
- intent.py: node_extract_intent, node_extract_data (próximo)
- availability.py: check_availability, check_missing, check_existing_appointment, lookup_appointment (próximo)
- response.py: generate_response, generate_response_with_tools, get_appointment_history, execute_memory_tools (próximo)

Por compatibilidad, funciones aún en nodes_backup.py se re-exportan desde aquí.
"""

# Importar funciones ya migradas a módulos específicos
from src.nodes.cancel import node_cancel_appointment
from src.nodes.reschedule import node_reschedule_appointment, node_prepare_modification
from src.nodes.booking import (
    node_book_appointment,
    node_detect_confirmation,
    node_validate_and_confirm,
)

# Importar el resto desde nodes_backup.py (aún no migrado)
from src.nodes_backup import (
    # Helpers
    _resolve_calendar_service,
    _last_human_text,
    _safe_node,
    _normalize_phone,
    _phone_in_text,
    _name_in_text,
    _service_in_text,
    _parse_event_start,
    _event_to_dict,
    _extract_patient_name_from_description,
    _no_appointment_found,
    _compute_slots_available,
    _build_llm_context,
    _format_datetime_readable,
    _format_slots,
    # Flow
    node_entry,
    node_route_intent,
    node_save_state,
    # Intent
    node_extract_intent,
    node_extract_data,
    # Availability
    node_check_availability,
    node_match_closest_slot,
    node_check_missing,
    node_adjust_weekend,
    node_check_existing_appointment,
    node_lookup_appointment,
    # Response
    node_generate_response,
    node_generate_response_with_tools,
    node_get_appointment_history,
    node_execute_memory_tools,
    edge_after_generate_response,
)

__all__ = [
    # Helpers
    "_resolve_calendar_service",
    "_last_human_text",
    "_safe_node",
    "_normalize_phone",
    "_phone_in_text",
    "_name_in_text",
    "_service_in_text",
    "_parse_event_start",
    "_event_to_dict",
    "_extract_patient_name_from_description",
    "_no_appointment_found",
    "_compute_slots_available",
    "_build_llm_context",
    "_format_datetime_readable",
    "_format_slots",
    # Flow
    "node_entry",
    "node_route_intent",
    "node_save_state",
    # Intent
    "node_extract_intent",
    "node_extract_data",
    # Availability
    "node_check_availability",
    "node_match_closest_slot",
    "node_check_missing",
    "node_adjust_weekend",
    "node_check_existing_appointment",
    "node_lookup_appointment",
    # Booking
    "node_book_appointment",
    "node_detect_confirmation",
    "node_validate_and_confirm",
    # Reschedule
    "node_reschedule_appointment",
    "node_prepare_modification",
    # Cancel
    "node_cancel_appointment",
    # Response
    "node_generate_response",
    "node_generate_response_with_tools",
    "node_get_appointment_history",
    "node_execute_memory_tools",
    "edge_after_generate_response",
]
