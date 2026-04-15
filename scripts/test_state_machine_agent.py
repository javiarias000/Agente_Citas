#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test simple para StateMachineAgent con mocks.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import os

os.environ["OPENAI_API_KEY"] = "sk-test"

from agents.state_machine_agent import StateMachineAgent
from core.store import ArcadiumStore
from langchain_core.messages import AIMessage


async def test_state_machine_agent_import():
    """Verifica que la clase se importa correctamente."""
    print("✅ StateMachineAgent importado correctamente")


async def test_process_message_with_mocks():
    """Test process_message con store y grafo mockeados."""
    # Mock store
    mock_store = MagicMock(spec=ArcadiumStore)
    mock_store.get_history = AsyncMock(return_value=[])
    mock_store.get_agent_state = AsyncMock(return_value=None)
    mock_store.save_agent_state = AsyncMock()
    mock_store.get_user_profile = AsyncMock(return_value=None)
    mock_store.memory_manager = MagicMock()

    agent = StateMachineAgent(
        session_id="+1234567890",
        store=mock_store,
        project_id=None,
        verbose=False
    )

    # Mock del grafo
    with patch('agents.state_machine_agent.create_arcadium_graph') as mock_create:
        mock_graph = MagicMock()
        async def mock_ainvoke(state, config):
            # Simular que el grafo procesa y devuelve estado actualizado
            new_state = state.copy()
            # Usar AIMessage real
            ai_msg = AIMessage(content="Confirmado")
            new_state["messages"] = state["messages"] + [ai_msg]
            new_state["current_step"] = "resolution"
            new_state["conversation_turns"] = state.get("conversation_turns", 0) + 1
            return new_state
        mock_graph.ainvoke = mock_ainvoke
        mock_create.return_value = mock_graph

        await agent.initialize()
        assert agent._initialized, "Agente no inicializado"

        result = await agent.process_message("Hola, quiero una cita")
        assert result["status"] == "success", f"Status no success: {result.get('error')}"
        assert "response" in result, "Falta response"
        assert result["response"] == "Confirmado", f"Respuesta incorrecta: {result['response']}"
        print("✅ process_message funciona correctamente")


async def main():
    print("Iniciando tests de StateMachineAgent...\n")
    try:
        await test_state_machine_agent_import()
        await test_process_message_with_mocks()
        print("\n✅ Todos los tests pasaron")
    except Exception as e:
        print(f"\n❌ Test falló: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
