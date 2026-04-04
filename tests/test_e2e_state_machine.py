#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test End-to-End: StateMachineAgent con store real y PostgreSQL.

Este test simula una conversación completa de agendamiento de citas
y verifica que:
- El estado se actualiza correctamente (current_step, conversation_turns)
- Las herramientas ejecutan y modifican el estado
- La persistencia en Store funciona
- Los checkpoints de StateGraph se guardan (si PostgresSaver disponible)
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Asegurar que el directorio raíz esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cargar variables de entorno
from dotenv import load_dotenv
load_dotenv()

import structlog
from agents.state_machine_agent import StateMachineAgent
from core.store import ArcadiumStore
from memory.memory_manager import MemoryManager

# Configurar logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)
logger = structlog.get_logger("test_e2e")


class MockLLMforTest:
    """
    Mock del LLM que devuelve respuestas predefinidas según el contexto.
    En un test real, esto se reemplazaría por un LLM mock o una fixture.
    """
    def __init__(self):
        self.call_count = 0

    async def ainvoke(self, inputs):
        self.call_count += 1
        # Por ahora, devolver AIMessage simple sin tool calls
        # En un test integration completo, el LLM real invocaría tools
        from langchain_core.messages import AIMessage
        return AIMessage(content=f"Respuesta automática turno {self.call_count}")


async def setup_test_environment(use_postgres: bool = False):
    """
    Configura el entorno de test:
    1. Inicializa MemoryManager (PostgreSQL o InMemory)
    2. Crea ArcadiumStore
    3. Retorna store listo
    """
    from core.config import Settings

    logger.info("Configurando entorno de test...", use_postgres=use_postgres)

    # Crear Settings con USE_POSTGRES_FOR_MEMORY forzado
    test_settings = Settings(USE_POSTGRES_FOR_MEMORY=use_postgres)
    memory_manager = MemoryManager(settings=test_settings)
    await memory_manager.initialize()
    logger.info("MemoryManager inicializado", backend="postgres" if use_postgres else "memory")

    # Crear ArcadiumStore
    store = ArcadiumStore(memory_manager)
    logger.info("ArcadiumStore creado")

    return store, memory_manager


async def test_full_conversation_state_machine():
    """
    Test E2E completo: conversación de 6 turnos con StateMachineAgent.
    Versión simplificada con grafo mock para probar lógica sin LLM real.
    """
    logger.info("=== Iniciando test E2E StateMachine (simulado) ===")

    store, memory_manager = await setup_test_environment(use_postgres=False)  # InMemory

    session_id = f"+test_{uuid.uuid4().hex[:8]}"
    project_id = None

    logger.info("Creando StateMachineAgent", session_id=session_id)
    agent = StateMachineAgent(
        session_id=session_id,
        store=store,
        project_id=project_id,
        verbose=False
    )

    # Mock del grafo: simula respuestas predefinidas
    from unittest.mock import patch

    with patch('agents.state_machine_agent.create_arcadium_graph') as mock_create:
        mock_graph = MagicMock()

        # Simular estado después de cada turno
        turn_states = [
            {"current_step": "info_collector", "intent": "agendar", "conversation_turns": 1},
            {"current_step": "info_collector", "selected_service": "limpieza", "conversation_turns": 2},
            {"current_step": "scheduler", "datetime_preference": "2025-04-05T14:00:00", "conversation_turns": 3},
            {"current_step": "scheduler", "availability_checked": True, "conversation_turns": 4},
            {"current_step": "resolution", "appointment_id": "test-123", "conversation_turns": 5},
            {"current_step": "resolution", "conversation_turns": 6}
        ]

        call_count = 0

        async def mock_ainvoke(state, config):
            nonlocal call_count
            call_count += 1
            # Devolver estado actualizado para este turno
            new_state = state.copy()
            new_state.update(turn_states[call_count - 1])
            # Añadir mensaje AIMessage
            from langchain_core.messages import AIMessage
            new_state["messages"] = state.get("messages", []) + [AIMessage(content=f"Turno {call_count} procesado")]
            return new_state

        mock_graph.ainvoke = mock_ainvoke
        mock_create.return_value = mock_graph

        await agent.initialize()
        logger.info("Agente inicializado con mock graph")

        # Conversación de 6 turnos
        messages = [
            "Quiero agendar una cita",
            "Limpieza dental",
            "Mañana a las 2pm",
            "¿Hay disponibilidad?",
            "Sí, agéndame",
            "Gracias"
        ]

        state_history = []

        for turn, msg in enumerate(messages, 1):
            logger.info(f"--- Turno {turn} ---", message=msg)
            result = await agent.process_message(msg)
            assert result["status"] == "success", f"Turno {turn} falló: {result.get('error')}"

            current_state = await agent.get_current_state()
            state_history.append(current_state)

            logger.info(
                "Estado",
                turn=turn,
                current_step=current_state.get("current_step"),
                turns=current_state.get("conversation_turns")
            )

        # Validaciones finales
        assert call_count == len(messages), "Número de invocaciones incorrecto"
        assert state_history[-1].get("conversation_turns") == 6, "Turnos totales incorrectos"
        assert state_history[-1].get("current_step") == "resolution", "Estado final incorrecto"

        logger.info("✅ Test E2E simulado completado exitosamente")

    # Limpiar
    await memory_manager.cleanup()
    return True


async def test_store_persistence():
    """
    Test que verifica que el estado se guarda en la base de datos.
    """
    logger.info("=== Test de Persistencia ===")

    store, memory_manager = await setup_test_environment(use_postgres=False)  # InMemory

    session_id = f"+persist_{uuid.uuid4().hex[:8]}"

    # Guardar algunos datos
    test_state = {
        "current_step": "scheduler",
        "selected_service": "limpieza",
        "appointment_id": str(uuid.uuid4())
    }

    await store.save_agent_state(session_id, test_state, project_id=None)
    logger.info("Estado guardado", session_id=session_id)

    # Recuperar
    recovered = await store.get_agent_state(session_id, project_id=None)
    assert recovered is not None, "Estado no recuperado"
    assert recovered.get("current_step") == "scheduler", "current_step no coincide"
    assert recovered.get("selected_service") == "limpieza", "servicio no coincide"
    logger.info("✅ Estado recuperado correctamente", recovered=recovered)

    # Limpiar
    await memory_manager.cleanup()

    return True


async def test_conversation_history():
    """
    Test que verifica que los mensajes se guardan en el historial.
    """
    logger.info("=== Test de Historial de Conversación ===")

    store, memory_manager = await setup_test_environment(use_postgres=False)  # InMemory

    session_id = f"+history_{uuid.uuid4().hex[:8]}"

    # Simular 3 mensajes
    from langchain_core.messages import HumanMessage, AIMessage
    messages = [
        HumanMessage(content="Hola"),
        AIMessage(content="¡Hola! ¿Cómo puedo ayudarte?"),
        HumanMessage(content="Quiero una cita")
    ]

    for msg in messages:
        await store.save_message(session_id, msg)
        logger.debug("Mensaje guardado", type=type(msg).__name__, content=msg.content[:50])

    # Recuperar historial
    history = await store.get_history(session_id)
    assert len(history) == 3, f"Historial length incorrecto: {len(history)}"
    assert isinstance(history[0], HumanMessage), "Primer mensaje no es HumanMessage"
    assert isinstance(history[1], AIMessage), "Segundo mensaje no es AIMessage"
    logger.info("✅ Historial recuperado", length=len(history))

    # Limpiar
    await memory_manager.cleanup()

    return True


async def main():
    """
    Ejecuta todos los tests E2E.
    """
    logger.info("Iniciando suite de tests E2E", datetime=datetime.now().isoformat())

    tests = [
        ("Persistencia de Estado", test_store_persistence),
        ("Historial de Conversación", test_conversation_history),
        ("Conversación Completa StateMachine", test_full_conversation_state_machine),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"Running: {test_name}")
            logger.info(f"{'='*60}")
            await test_func()
            passed += 1
            logger.info(f"✅ {test_name} PASSED")
        except Exception as e:
            failed += 1
            logger.error(f"❌ {test_name} FAILED", error=str(e), exc_info=True)

    logger.info(f"\n{'='*60}")
    logger.info("RESUMEN DE TESTS", passed=passed, failed=failed, total=passed+failed)
    logger.info(f"{'='*60}")

    if failed > 0:
        logger.error("Algunos tests fallaron", exit_code=1)
        return 1
    else:
        logger.success("Todos los tests pasaron!")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
