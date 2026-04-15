#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests unitarios para SupportState y funciones auxiliares.
"""

import pytest
from agents.support_state import (
    SupportState,
    SupportStep,
    Intent,
    ServiceType,
    create_initial_state,
    is_valid_state,
    get_required_fields_for_step,
    can_transition_from,
    DURATION_BY_SERVICE,
    get_service_duration,
    is_complete_for_step
)
from agents.step_configs import get_next_step


class TestSupportStateSchema:
    """Tests para el schema SupportState"""

    def test_create_initial_state_reception(self):
        """Test: estado inicial por defecto es reception"""
        state = create_initial_state()
        assert state["current_step"] == "reception"

    def test_create_initial_state_custom(self):
        """Test: estado inicial con step personalizado"""
        state = create_initial_state(step="info_collector")
        assert state["current_step"] == "info_collector"

    def test_is_valid_state_valid_steps(self):
        """Test: steps válidos"""
        for step in ["reception", "info_collector", "scheduler", "resolution"]:
            state = {"current_step": step}
            assert is_valid_state(state) is True

    def test_is_valid_state_invalid_step(self):
        """Test: step inválido"""
        state = {"current_step": "invalid"}
        assert is_valid_state(state) is False

    def test_get_required_fields_for_step(self):
        """Test: campos requeridos por step"""
        # Reception requires intent para saber a dónde ir
        assert get_required_fields_for_step("reception") == ["intent"]

        # Info collector requires service y datetime para poder transitar
        required = get_required_fields_for_step("info_collector")
        assert "selected_service" in required
        assert "datetime_preference" in required

        # Scheduler requires at least selected_service
        assert get_required_fields_for_step("scheduler") == ["selected_service"]

    def test_can_transition_from(self):
        """Test: transiciones válidas"""
        # Reception puede ir a info_collector, scheduler, resolution
        assert can_transition_from("reception", "info_collector") is True
        assert can_transition_from("reception", "scheduler") is True
        assert can_transition_from("reception", "resolution") is True

        # Info collector puede ir a scheduler o reception
        assert can_transition_from("info_collector", "scheduler") is True
        assert can_transition_from("info_collector", "reception") is True

        # Scheduler puede ir a resolution o info_collector
        assert can_transition_from("scheduler", "resolution") is True
        assert can_transition_from("scheduler", "info_collector") is True

        # Resolution puede ir a info_collector o reception
        assert can_transition_from("resolution", "info_collector") is True
        assert can_transition_from("resolution", "reception") is True

        # Transiciones inválidas
        assert can_transition_from("info_collector", "resolution") is False
        assert can_transition_from("scheduler", "reception") is False

    def test_get_next_step_from_reception(self):
        """Test: next_step map desde reception"""
        assert get_next_step("reception", "agendar") == "info_collector"
        assert get_next_step("reception", "consultar") == "scheduler"
        assert get_next_step("reception", "cancelar") == "resolution"
        assert get_next_step("reception", "reagendar") == "resolution"
        assert get_next_step("reception", "otro") == "reception"


class TestServiceDuration:
    """Tests para duraciones de servicios"""

    def test_duration_by_service_exists(self):
        """Test: todos los servicios definidos tienen duración"""
        from agents.support_state import ServiceType
        for service in ServiceType.__args__:
            assert service in DURATION_BY_SERVICE

    def test_get_service_duration(self):
        """Test: obtiene duración correcta"""
        assert get_service_duration("consulta") == 30
        assert get_service_duration("limpieza") == 45
        assert get_service_duration("endodoncia") == 60

    def test_get_service_duration_unknown(self):
        """Test: servicio desconocido devuelve default"""
        assert get_service_duration("servicio_inexistente") == 30


class TestCompleteForStep:
    """Tests para is_complete_for_step"""

    def test_info_collector_needs_service_and_date(self):
        """Test: info_collector requiere servicio y fecha"""
        incomplete = {"selected_service": "limpieza"}
        assert is_complete_for_step("info_collector", incomplete) is False

        complete = {
            "selected_service": "limpieza",
            "datetime_preference": "2025-12-25T14:00"
        }
        assert is_complete_for_step("info_collector", complete) is True

    def test_scheduler_needs_service(self):
        """Test: scheduler requiere al menos selected_service"""
        # Sin servicio no está completo
        state = {"datetime_preference": "2025-12-25T10:00"}
        assert is_complete_for_step("scheduler", state) is False

        # Con servicio está completo (aunque no tenga appointment_id)
        state = {"selected_service": "limpieza"}
        assert is_complete_for_step("scheduler", state) is True