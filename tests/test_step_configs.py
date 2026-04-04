#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests unitarios para step_configs
"""

import pytest
from agents.step_configs import (
    get_config_for_step,
    get_prompt_for_step,
    get_tools_for_step,
    get_required_fields,
    get_next_step,
    validate_transition,
    initialize_step_tools,
    STEP_CONFIGS
)


class TestStepConfigs:
    """Tests para configuraciones de estados"""

    def test_all_steps_have_config(self):
        """Test: todos los steps tienen configuración"""
        for step in ["reception", "info_collector", "scheduler", "resolution"]:
            assert step in STEP_CONFIGS

    def test_each_step_has_required_fields(self):
        """Test: cada step tiene prompt y tools list"""
        for step, config in STEP_CONFIGS.items():
            assert "prompt" in config
            assert "tools" in config
            assert "requires" in config
            assert "can_transition_to" in config

    def test_get_config_for_step(self):
        """Test: obtiene config correcta"""
        reception_config = get_config_for_step("reception")
        assert reception_config["name"] == "Recepción"
        assert "prompt" in reception_config

    def test_get_prompt_for_step(self):
        """Test: obtiene prompt correcto"""
        prompt = get_prompt_for_step("scheduler")
        assert prompt is not None
        # Verificar que el prompt template tenga variables
        input_vars = prompt.input_variables
        assert "selected_service" in input_vars

    def test_get_tools_for_step(self):
        """Test: obtiene lista de tools"""
        initialize_step_tools()  # Necesario para cargar STATE_MACHINE_TOOLS
        tools = get_tools_for_step("resolution")
        assert len(tools) > 0
        # Verificar que tiene herramientas clave
        tool_names = [t.name for t in tools]
        assert "cancelar_cita" in tool_names or "obtener_citas_cliente" in tool_names

    def test_get_required_fields(self):
        """Test: campos requeridos por step"""
        # Reception requiere intent para saber a dónde ir
        assert "intent" in get_required_fields("reception")

        # Info collector requires service y datetime para poder agendar
        required = get_required_fields("info_collector")
        assert "selected_service" in required
        assert "datetime_preference" in required

    def test_get_next_step_from_reception(self):
        """Test: siguiente step desde reception"""
        assert get_next_step("reception", intent="agendar") == "info_collector"
        assert get_next_step("reception", intent="consultar") == "scheduler"

    def test_validate_transition(self):
        """Test: validación de transiciones"""
        assert validate_transition("reception", "info_collector") is True
        assert validate_transition("info_collector", "reception") is True
        assert validate_transition("scheduler", "resolution") is True
        # Inválida
        assert validate_transition("reception", "invalid_step") is False

    def test_transitions_to_resolution_are_allowed(self):
        """Test: múltiples formas de llegar a resolution"""
        # Desde reception con intent cancelar/reagendar
        assert get_next_step("reception", intent="cancelar") == "resolution"
        assert get_next_step("reception", intent="reagendar") == "resolution"

        # Desde scheduler después de agendar
        assert validate_transition("scheduler", "resolution") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
