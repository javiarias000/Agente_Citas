# -*- coding: utf-8 -*-
"""
Tests para Agente Deyy
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import os

os.environ['OPENAI_API_KEY'] = 'sk-test-key'
os.environ['DATABASE_URL'] = 'postgresql+psycopg2://test:test@localhost/test'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
os.environ['SUPABASE_ANON_KEY'] = 'anon-key'

from agents.arcadium_agent import DeyyAgent, get_agent_response


class TestDeyyAgent:
    """Tests para DeyyAgent"""

    @patch('agents.arcadium_agent.LangChainComponentFactory')
    def test_agent_initialization(self, mock_factory):
        """Debe inicializar agente correctamente"""
        # Mocks
        mock_llm = Mock()
        mock_memory = Mock()
        mock_factory.create_chat_model.return_value = mock_llm
        mock_factory.create_postgres_memory.return_value = mock_memory

        agent = DeyyAgent(
            session_id="test_session",
            system_prompt="Eres un asistente de prueba",
            verbose=True
        )
        # Inicializar explícitamente para tests
        # Como NO llamamos a _initialize (que es privado), solo verificamos atributos
        assert agent.session_id == "test_session"
        assert agent.system_prompt == "Eres un asistente de prueba"

    @pytest.mark.asyncio
    async def test_agent_run_without_initialization(self):
        """Debe manejar agente no inicializado"""
        agent = DeyyAgent(session_id="test")
        # No inicializar
        result = await agent.run("Hola")
        assert result['status'] == 'error'
        assert 'Agente no disponible' in result.get('response', '')

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.AgentExecutor')
    async def test_agent_run_success(self, mock_executor_class):
        """Debe ejecutar agente exitosamente"""
        # Mock AgentExecutor
        mock_executor = AsyncMock()
        mock_executor.ainvoke.return_value = {
            'output': 'Respuesta del agente',
            'intermediate_steps': [],
            'finished': True
        }
        mock_executor_class.return_value = mock_executor

        agent = DeyyAgent(session_id="test_session")
        # Simular inicialización manual
        agent._initialized = True
        agent._agent_executor = mock_executor

        result = await agent.run("Hola, cómo estás?")

        assert result['status'] == 'success'
        assert result['response'] == 'Respuesta del agente'
        assert result['execution_time'] >= 0

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.AgentExecutor')
    async def test_agent_run_with_history(self, mock_executor_class):
        """Debe manejar historial de conversación"""
        mock_executor = AsyncMock()
        mock_executor.ainvoke.return_value = {
            'output': 'Hola! soy Deyy',
            'intermediate_steps': []
        }
        mock_executor_class.return_value = mock_executor

        agent = DeyyAgent(session_id="test")
        agent._initialized = True
        agent._agent_executor = mock_executor

        history = [
            {"role": "human", "content": "Hola"},
            {"role": "ai", "content": "Hola! ¿en qué puedo ayudar?"}
        ]
        result = await agent.run("Quiero ayuda", conversation_history=history)

        # Verificar que se llamó con history
        call_args = mock_executor.ainvoke.call_args[0][0]
        assert 'chat_history' in call_args
        assert call_args['chat_history'] == history

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.AgentExecutor')
    async def test_agent_run_with_tool_calls(self, mock_executor_class):
        """Debe extraer llamadas a herramientas"""
        mock_executor = AsyncMock()
        # Simular step con herramienta
        mock_action = Mock()
        mock_action.tool = 'think'
        mock_action.tool_input = {'thought': 'analizando...'}
        mock_observation = "He pensado profundamente"
        mock_executor.ainvoke.return_value = {
            'output': 'Respuesta final',
            'intermediate_steps': [(mock_action, mock_observation)]
        }
        mock_executor_class.return_value = mock_executor

        agent = DeyyAgent(session_id="test")
        agent._initialized = True
        agent._agent_executor = mock_executor

        result = await agent.run("Piensa sobre esto")

        assert len(result['tool_calls']) == 1
        assert result['tool_calls'][0]['tool'] == 'think'
        assert 'analizando...' in result['tool_calls'][0]['input']

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.AgentExecutor')
    async def test_agent_run_with_think_tool(self, mock_executor_class):
        """Debe extraer razonamiento de herramienta think"""
        mock_executor = AsyncMock()
        mock_action = Mock()
        mock_action.tool = 'think'
        mock_observation = "Razonamiento detallado: 1. ..., 2. ...."
        mock_executor.ainvoke.return_value = {
            'output': 'Basado en mi razonamiento, la respuesta es...',
            'intermediate_steps': [(mock_action, mock_observation)]
        }
        mock_executor_class.return_value = mock_executor

        agent = DeyyAgent(session_id="test")
        agent._initialized = True
        agent._agent_executor = mock_executor

        result = await agent.run("Razona esto")

        assert result['reasoning'] == "Razonamiento detallado: 1. ..., 2. ...."

    @pytest.mark.asyncio
    async def test_agent_reset(self):
        """Debe reiniciar memoria del agente"""
        agent = DeyyAgent(session_id="test")
        agent._memory = Mock()
        await agent.reset()
        agent._memory.clear.assert_called_once()


class TestGetAgentResponse:
    """Tests para función helper get_agent_response"""

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.DeyyAgent')
    async def test_get_agent_response_success(self, mock_agent_class):
        """Debe obtener respuesta del agente"""
        mock_agent = AsyncMock()
        mock_agent.run.return_value = {
            'status': 'success',
            'response': 'OK'
        }
        mock_agent_class.return_value = mock_agent

        result = await get_agent_response(
            phone="+1234567890",
            message="Hola"
        )
        assert result['status'] == 'success'

    @pytest.mark.asyncio
    @patch('agents.arcadium_agent.DeyyAgent')
    async def test_get_agent_response_error(self, mock_agent_class):
        """Debe manejar error en agente"""
        mock_agent = AsyncMock()
        mock_agent.run.side_effect = Exception("Error interno")
        mock_agent_class.return_value = mock_agent

        result = await get_agent_response(
            phone="+1234567890",
            message="Hola"
        )
        assert result['status'] == 'error'
        assert 'Agente no disponible' in result['response']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
