#!/usr/bin/env python3
"""
Tests para RouterAgent.
"""

import pytest
from unittest.mock import Mock

from agents.router_agent import RouterAgent


@pytest.fixture
def mock_store():
    store = Mock()
    return store


@pytest.fixture
def router(mock_store):
    return RouterAgent(
        session_id="test_router_123",
        store=mock_store,
        project_id=None,
        verbose=False
    )


@pytest.mark.asyncio
async def test_classify_intent_agendar(router):
    """Test clasificación para agendar."""
    assert router._classify_intent("Quiero agendar una cita") == "agendar"
    assert router._classify_intent("Necesito una reserva para mañana") == "agendar"
    assert router._classify_intent("Quiero un turno a las 10") == "agendar"
    assert router._classify_intent("Programar cita dental") == "agendar"


@pytest.mark.asyncio
async def test_classify_intent_reagendar(router):
    """Test clasificación para reagendar."""
    assert router._classify_intent("Quiero cambiar mi cita") == "reagendar"
    assert router._classify_intent("Necesito reprogramar") == "reagendar"
    assert router._classify_intent("Mover mi cita para otro día") == "reagendar"
    assert router._classify_intent("Modificar mi reserva") == "reagendar"


@pytest.mark.asyncio
async def test_classify_intent_cancelar(router):
    """Test clasificación para cancelar."""
    assert router._classify_intent("Cancelar cita") == "cancelar"
    assert router._classify_intent("Quiero eliminar mi reserva") == "cancelar"
    assert router._classify_intent("Anular mi cita") == "cancelar"
    assert router._classify_intent("Quitar la cita que tengo") == "cancelar"


@pytest.mark.asyncio
async def test_classify_intent_consultar(router):
    """Test clasificación para consultar."""
    assert router._classify_intent("¿Qué citas tengo?") == "consultar"
    assert router._classify_intent("Ver disponibilidad") == "consultar"
    assert router._classify_intent("¿Qué horas hay disponibles?") == "consultar"
    assert router._classify_intent("Consultar mi agenda") == "consultar"


@pytest.mark.asyncio
async def test_classify_intent_otro(router):
    """Test clasificación para otras intenciones."""
    assert router._classify_intent("Hola, cómo estás") == "otro"
    assert router._classify_intent("¿Qué tiempo hace?") == "otro"
    assert router._classify_intent("Gracias") == "otro"
    assert router._classify_intent("Buenos días") == "otro"


@pytest.mark.asyncio
async def test_process_message_delegates_to_appointment_agent(router):
    """Test que process_message delega a AppointmentAgent para intención 'agendar'."""
    # Este test requiere mocking más complejo del AppointmentAgent
    # Por ahora, probamos la clasificación básica
    message = "Quiero agendar una cita para mañana"
    intent = router._classify_intent(message)
    assert intent == "agendar"

    # Verificar que se crea el agente correcto
    agent = router._create_agent_for_intent(intent)
    assert agent is not None
    assert agent.__class__.__name__ == "AppointmentAgent"


@pytest.mark.asyncio
async def test_process_message_otro_returns_friendly_message(router):
    """Test que intenciones 'otro' retornan mensaje amigable."""
    # Simular que no hay agente para "otro"
    # Como _create_agent_for_intent devuelve None para "otro"
    response = await router.process_message("Hola, qué tal")
    assert "Lo siento" in response or "Lo que SÍ puedo hacer" in response
    assert "agendar" in response.lower()
    assert "consultar" in response.lower()


@pytest.mark.asyncio
async def test_initialize(router):
    """Test que initialize marca el agente como inicializado."""
    assert router._initialized is False
    await router.initialize()
    assert router._initialized is True
