"""
Módulo nodes completamente refactorizado.

Estructura:
✓ cancel.py: node_cancel_appointment
✓ reschedule.py: node_reschedule_appointment, node_prepare_modification
✓ booking.py: node_book_appointment, node_detect_confirmation, node_validate_and_confirm
✓ availability.py: check_availability, check_missing, check_existing_appointment, lookup_appointment, match_closest_slot, adjust_weekend
✓ flow.py: node_entry, node_route_intent, node_save_state
✓ intent.py: node_extract_intent, node_extract_data
✓ response.py: generate_response, generate_response_with_tools, get_appointment_history, execute_memory_tools, edge_after_generate_response
- _helpers.py: funciones _privadas compartidas (stub, reutiliza nodes_backup.py)

Todos los módulos importan helpers desde src.nodes_backup directamente para evitar
dependencias circulares. Una vez que _helpers.py esté completamente migrado,
los imports pueden cambiar a ese módulo.
"""

from __future__ import annotations

# Importar desde módulos específicos
from src.nodes.cancel import node_cancel_appointment
from src.nodes.reschedule import node_reschedule_appointment, node_prepare_modification
from src.nodes.booking import (
    node_book_appointment,
    node_detect_confirmation,
    node_validate_and_confirm,
)
from src.nodes.availability import (
    node_check_availability,
    node_match_closest_slot,
    node_check_missing,
    node_adjust_weekend,
    node_check_existing_appointment,
    node_lookup_appointment,
)
from src.nodes.flow import (
    node_entry,
    node_route_intent,
    node_save_state,
)
from src.nodes.intent import (
    node_extract_intent,
    node_extract_data,
)
from src.nodes.response import (
    node_generate_response,
    node_generate_response_with_tools,
    node_get_appointment_history,
    node_execute_memory_tools,
    edge_after_generate_response,
)

# Importar helpers desde nodes_backup (aún no completamente migrado a _helpers.py)
from src.nodes_backup import (
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
    _GENERATE_RESPONSE_SYSTEM_WITH_TOOLS,
)

__all__ = [
    # Booking
    "node_book_appointment",
    "node_detect_confirmation",
    "node_validate_and_confirm",
    # Reschedule
    "node_reschedule_appointment",
    "node_prepare_modification",
    # Cancel
    "node_cancel_appointment",
    # Availability
    "node_check_availability",
    "node_match_closest_slot",
    "node_check_missing",
    "node_adjust_weekend",
    "node_check_existing_appointment",
    "node_lookup_appointment",
    # Flow
    "node_entry",
    "node_route_intent",
    "node_save_state",
    # Intent
    "node_extract_intent",
    "node_extract_data",
    # Response
    "node_generate_response",
    "node_generate_response_with_tools",
    "node_get_appointment_history",
    "node_execute_memory_tools",
    "edge_after_generate_response",
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
    "_GENERATE_RESPONSE_SYSTEM_WITH_TOOLS",
]
