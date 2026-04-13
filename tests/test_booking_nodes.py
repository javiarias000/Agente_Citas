"""
Tests unitarios del flujo de agendamiento V2.

Cubre:
  - detect_confirmation con keywords extendidos
  - extract_state_updates para check_availability y book_appointment
  - _build_system_prompt_v2: inyección de flow_block
  - edge_after_react y edge_after_execute_tools
  - edge_after_interceptor
  - node_confirmation_interceptor: los 6 casos de decisión
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.intent_router import detect_confirmation, extract_slot_from_text
from src.edges_v2 import edge_after_react, edge_after_execute_tools, edge_after_interceptor
from src.schemas_v2 import (
    extract_state_updates,
    CheckAvailabilityResult,
    BookAppointmentResult,
    SlotInfo,
    CancelAppointmentResult,
    LookupAppointmentsResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. detect_confirmation — keywords extendidos
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectConfirmation:
    """Verifica todos los keywords YES/NO/slot_choice/unknown."""

    # Keywords originales
    def test_si_minuscula(self):
        assert detect_confirmation("si") == "yes"

    def test_si_con_tilde(self):
        assert detect_confirmation("sí") == "yes"

    def test_claro(self):
        assert detect_confirmation("claro") == "yes"

    def test_dale(self):
        assert detect_confirmation("dale") == "yes"

    def test_ok(self):
        assert detect_confirmation("ok") == "yes"

    def test_perfecto(self):
        assert detect_confirmation("perfecto") == "yes"

    def test_de_una(self):
        assert detect_confirmation("de una") == "yes"

    def test_hagale(self):
        assert detect_confirmation("hagale") == "yes"

    # Keywords extendidos (nuevos)
    def test_listo(self):
        assert detect_confirmation("listo") == "yes"

    def test_de_acuerdo(self):
        assert detect_confirmation("de acuerdo") == "yes"

    def test_acepto(self):
        assert detect_confirmation("acepto") == "yes"

    def test_esta_bien(self):
        assert detect_confirmation("está bien") == "yes"

    def test_me_parece_bien(self):
        assert detect_confirmation("me parece bien") == "yes"

    def test_adelante(self):
        assert detect_confirmation("adelante") == "yes"

    def test_cuando_gusten(self):
        assert detect_confirmation("cuando gusten") == "yes"

    def test_confirma(self):
        assert detect_confirmation("confirma") == "yes"

    # Frases combinadas con keywords YES
    def test_claro_que_si(self):
        assert detect_confirmation("claro que sí") == "yes"

    def test_si_por_favor(self):
        assert detect_confirmation("sí por favor") == "yes"

    def test_ok_adelante(self):
        assert detect_confirmation("ok adelante") == "yes"

    # NO keywords
    def test_no(self):
        assert detect_confirmation("no") == "no"

    def test_mejor_no(self):
        assert detect_confirmation("mejor no") == "no"

    def test_no_gracias(self):
        assert detect_confirmation("no gracias") == "no"

    def test_en_otro_momento(self):
        assert detect_confirmation("en otro momento") == "no"

    # slot_choice
    def test_hora_con_colon(self):
        assert detect_confirmation("a las 10:00") == "slot_choice"

    def test_hora_sin_colon(self):
        assert detect_confirmation("a las 10") == "slot_choice"

    def test_hora_sola(self):
        assert detect_confirmation("10:30") == "slot_choice"

    # unknown
    def test_frase_ambigua(self):
        result = detect_confirmation("quizás más tarde")
        assert result == "unknown"

    def test_vacio(self):
        assert detect_confirmation("") == "unknown"

    # Asegurar que "no" no matchea dentro de "nombre"
    def test_no_matchea_dentro_de_palabra(self):
        # "nombre" contiene "no" pero no debe matchear como NO
        result = detect_confirmation("mi nombre es Juan")
        # Debe ser unknown (no matchea confirmación)
        assert result != "no"


# ══════════════════════════════════════════════════════════════════════════════
# 2. extract_state_updates — invariantes de estado
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractStateUpdates:

    def test_check_availability_success_sets_awaiting(self):
        result = CheckAvailabilityResult(
            success=True,
            slots=[
                SlotInfo(iso="2026-04-14T10:00:00", display="lunes 10:00"),
                SlotInfo(iso="2026-04-14T11:00:00", display="lunes 11:00"),
            ],
            duration_minutes=30,
        )
        updates = extract_state_updates("check_availability", result)

        assert updates["awaiting_confirmation"] is True
        assert updates["confirmation_type"] == "book"
        assert len(updates["available_slots"]) == 2
        assert "2026-04-14T10:00:00" in updates["available_slots"]

    def test_check_availability_no_slots_no_awaiting(self):
        result = CheckAvailabilityResult(
            success=False,
            slots=[],
            error="No hay slots disponibles",
        )
        updates = extract_state_updates("check_availability", result)

        assert updates.get("available_slots") == []
        assert "awaiting_confirmation" not in updates  # No debe setear True

    def test_book_appointment_success_invariant(self):
        """INVARIANTE: confirmation_sent=True solo si event_id existe."""
        result = BookAppointmentResult(
            success=True,
            event_id="evt_abc123",
            event_link="https://calendar.google.com/evt_abc123",
            slot_iso="2026-04-14T10:00:00",
            service="limpieza",
            patient_name="Ana Torres",
        )
        updates = extract_state_updates("book_appointment", result)

        assert updates["confirmation_sent"] is True
        assert updates["google_event_id"] == "evt_abc123"
        assert updates["awaiting_confirmation"] is False
        assert updates["confirmation_type"] is None
        assert updates["available_slots"] == []

    def test_book_appointment_failure_no_confirmation(self):
        """Si falla, confirmation_sent NO debe setearse."""
        result = BookAppointmentResult(
            success=False,
            event_id=None,
            error="Error en Calendar API",
        )
        updates = extract_state_updates("book_appointment", result)

        assert "confirmation_sent" not in updates
        assert updates.get("last_error") == "Error en Calendar API"

    def test_cancel_success_clears_event_id(self):
        result = CancelAppointmentResult(success=True, event_id="evt_xyz")
        updates = extract_state_updates("cancel_appointment", result)

        assert updates["confirmation_sent"] is True
        assert updates["awaiting_confirmation"] is False
        assert updates["google_event_id"] is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Edges V2
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgesV2:

    def test_edge_after_react_with_tool_calls(self):
        state = {"pending_tool_calls": [{"name": "check_availability", "args": {}}]}
        assert edge_after_react(state) == "execute_tools"

    def test_edge_after_react_no_tool_calls(self):
        state = {"pending_tool_calls": []}
        assert edge_after_react(state) == "format_response"

    def test_edge_after_react_missing_key(self):
        assert edge_after_react({}) == "format_response"

    def test_edge_after_execute_tools_under_limit(self):
        state = {"_tool_iterations": 3}
        assert edge_after_execute_tools(state) == "react_loop"

    def test_edge_after_execute_tools_at_limit(self):
        state = {"_tool_iterations": 6}
        assert edge_after_execute_tools(state) == "format_response"

    def test_edge_after_execute_tools_over_limit(self):
        state = {"_tool_iterations": 10}
        assert edge_after_execute_tools(state) == "format_response"

    def test_edge_after_interceptor_with_pending(self):
        state = {"pending_tool_calls": [{"name": "book_appointment"}]}
        assert edge_after_interceptor(state) == "execute_tools"

    def test_edge_after_interceptor_no_pending(self):
        assert edge_after_interceptor({}) == "react_loop"
        assert edge_after_interceptor({"pending_tool_calls": []}) == "react_loop"


# ══════════════════════════════════════════════════════════════════════════════
# 4. node_confirmation_interceptor — los 6 casos de decisión
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmationInterceptor:

    @pytest.fixture
    def base_state(self):
        """Estado base con awaiting_confirmation=True y 1 slot."""
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "book",
            "available_slots": ["2026-04-14T10:00:00"],
            "selected_service": "limpieza",
            "patient_name": "Ana Torres",
            "phone_number": "+593900000001",
            "messages": [],
            "_incoming_message": "",
        }

    def _with_message(self, state: dict, text: str) -> dict:
        from langchain_core.messages import HumanMessage
        s = dict(state)
        s["messages"] = [HumanMessage(content=text)]
        return s

    @pytest.mark.asyncio
    async def test_no_awaiting_passes_through(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["awaiting_confirmation"] = False
        result = await node_confirmation_interceptor(self._with_message(state, "sí"))
        assert result == {}

    @pytest.mark.asyncio
    async def test_yes_single_slot_injects_booking(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = self._with_message(base_state, "sí")
        result = await node_confirmation_interceptor(state)

        assert "pending_tool_calls" in result
        assert len(result["pending_tool_calls"]) == 1
        tc = result["pending_tool_calls"][0]
        assert tc["name"] == "book_appointment"
        assert tc["args"]["slot_iso"] == "2026-04-14T10:00:00"
        assert tc["args"]["service"] == "limpieza"
        assert tc["args"]["patient_name"] == "Ana Torres"

    @pytest.mark.asyncio
    async def test_yes_multiple_slots_passes_to_llm(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["available_slots"] = [
            "2026-04-14T10:00:00",
            "2026-04-14T11:00:00",
            "2026-04-14T14:00:00",
        ]
        result = await node_confirmation_interceptor(self._with_message(state, "sí"))
        # Con múltiples slots y "sí" genérico → pasa al LLM
        assert result == {}

    @pytest.mark.asyncio
    async def test_time_pattern_matching_slot(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["available_slots"] = [
            "2026-04-14T09:00:00",
            "2026-04-14T10:00:00",
            "2026-04-14T11:00:00",
        ]
        result = await node_confirmation_interceptor(self._with_message(state, "a las 10"))

        assert "pending_tool_calls" in result
        tc = result["pending_tool_calls"][0]
        assert tc["name"] == "book_appointment"
        assert "T10:00" in tc["args"]["slot_iso"]

    @pytest.mark.asyncio
    async def test_no_clears_awaiting(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        result = await node_confirmation_interceptor(self._with_message(base_state, "no"))

        assert result["awaiting_confirmation"] is False
        assert result["confirmation_type"] is None
        assert "pending_tool_calls" not in result

    @pytest.mark.asyncio
    async def test_cancel_yes_injects_cancel(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["confirmation_type"] = "cancel"
        state["google_event_id"] = "evt_test_123"
        result = await node_confirmation_interceptor(self._with_message(state, "sí, cancela"))

        assert "pending_tool_calls" in result
        tc = result["pending_tool_calls"][0]
        assert tc["name"] == "cancel_appointment"
        assert tc["args"]["event_id"] == "evt_test_123"

    @pytest.mark.asyncio
    async def test_cancel_no_event_id_passes_to_llm(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["confirmation_type"] = "cancel"
        state["google_event_id"] = None
        result = await node_confirmation_interceptor(self._with_message(state, "sí"))
        # Sin event_id, no puede cancelar determinísticamente
        assert result == {}

    @pytest.mark.asyncio
    async def test_reschedule_passes_to_llm(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        state = dict(base_state)
        state["confirmation_type"] = "reschedule"
        result = await node_confirmation_interceptor(self._with_message(state, "sí"))
        # Reschedule es complejo — siempre pasa al LLM
        assert result == {}

    @pytest.mark.asyncio
    async def test_unknown_text_passes_to_llm(self, base_state):
        from src.confirmation_interceptor import node_confirmation_interceptor
        # Texto ambiguo que no contiene keywords YES/NO ni patrón de hora
        result = await node_confirmation_interceptor(
            self._with_message(base_state, "quizás mañana podría ser")
        )
        assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# 5. _build_system_prompt_v2 — inyección de flow_block
# ══════════════════════════════════════════════════════════════════════════════

class TestSystemPromptV2:

    def test_flow_block_injected_when_awaiting_book(self):
        from src.nodes_v2 import _build_system_prompt_v2
        state = {
            "awaiting_confirmation": True,
            "confirmation_type": "book",
            "available_slots": [
                "2026-04-17T10:00:00",
                "2026-04-17T11:00:00",
            ],
            "phone_number": "+593900000001",
            "patient_name": "Ana Torres",
            "fecha_hoy": "2026-04-13",
            "hora_actual": "09:00",
            "dia_semana_hoy": "lunes",
            "manana_fecha": "2026-04-14",
            "manana_dia": "martes",
        }
        prompt = _build_system_prompt_v2(state)

        assert "FLUJO EN PROGRESO" in prompt
        assert "book_appointment" in prompt
        assert "T10:00" in prompt or "10:00" in prompt

    def test_no_flow_block_when_not_awaiting(self):
        from src.nodes_v2 import _build_system_prompt_v2
        state = {
            "awaiting_confirmation": False,
            "phone_number": "+593900000001",
            "fecha_hoy": "2026-04-13",
            "hora_actual": "09:00",
            "dia_semana_hoy": "lunes",
            "manana_fecha": "2026-04-14",
            "manana_dia": "martes",
        }
        prompt = _build_system_prompt_v2(state)

        assert "FLUJO EN PROGRESO" not in prompt

    def test_confirmation_sent_block(self):
        from src.nodes_v2 import _build_system_prompt_v2
        state = {
            "awaiting_confirmation": False,
            "google_event_id": "evt_123",
            "confirmation_sent": True,
            "phone_number": "+593900000001",
            "fecha_hoy": "2026-04-13",
            "hora_actual": "09:00",
            "dia_semana_hoy": "lunes",
            "manana_fecha": "2026-04-14",
            "manana_dia": "martes",
        }
        prompt = _build_system_prompt_v2(state)
        # Debe indicar que la operación ya fue ejecutada
        assert "evt_123" in prompt or "operación ya fue ejecutada" in prompt


# ══════════════════════════════════════════════════════════════════════════════
# 6. extract_slot_from_text — parsing de slots
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractSlotFromText:

    SLOTS = [
        "2026-04-14T09:00:00",
        "2026-04-14T10:00:00",
        "2026-04-14T11:00:00",
        "2026-04-14T14:00:00",
        "2026-04-14T15:30:00",
    ]

    def test_colon_format(self):
        slot = extract_slot_from_text("10:00", self.SLOTS)
        assert slot == "2026-04-14T10:00:00"

    def test_a_las_format(self):
        slot = extract_slot_from_text("a las 10", self.SLOTS)
        assert slot == "2026-04-14T10:00:00"

    def test_las_format(self):
        slot = extract_slot_from_text("las 11", self.SLOTS)
        assert slot == "2026-04-14T11:00:00"

    def test_pm_adjustment(self):
        slot = extract_slot_from_text("a las 2 de la tarde", self.SLOTS)
        assert slot == "2026-04-14T14:00:00"

    def test_no_match_returns_none(self):
        slot = extract_slot_from_text("a las 7", self.SLOTS)
        # 7 AM no está en slots ni como PM útil (19:00 tampoco en slots)
        assert slot is None

    def test_morning_format(self):
        slot = extract_slot_from_text("9 de la mañana", self.SLOTS)
        assert slot == "2026-04-14T09:00:00"
