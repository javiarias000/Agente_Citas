# -*- coding: utf-8 -*-
"""
Tests para herramientas personalizadas
"""

import asyncio
import pytest
from unittest.mock import Mock, patch
import os

os.environ.setdefault('OPENAI_API_KEY', 'sk-test-key')

from utils.tools import (
    PlanningTool,
    ThinkTool,
    KnowledgeBaseSearch,
    MCPGoogleCalendarTool,
    get_deyy_tools
)


class TestPlanningTool:
    """Tests para PlanningTool"""

    def test_planning_tool_run(self):
        """Debe generar plan estructurado"""
        tool = PlanningTool()
        result = tool._run(
            task="Implementar login en Python",
            constraints="Usar FastAPI, tests obligatorios",
            max_steps=5
        )
        assert result['status'] == 'success'
        assert 'plan' in result
        assert result['plan']['task'] == "Implementar login en Python"
        assert len(result['plan']['steps']) <= 5

    def test_planning_tool_empty_task(self):
        """Debe manejar tarea vacía"""
        tool = PlanningTool()
        result = tool._run(task="")
        assert result['status'] == 'success'
        assert result['plan']['task'] == ""


class TestThinkTool:
    """Tests para ThinkTool"""

    def test_think_tool_run(self):
        """Debe generar razonamiento estructurado"""
        tool = ThinkTool()
        result = tool._run(
            thought="¿Cuál es la mejor forma de autenticar usuarios?",
            context="API REST, 1000 usuarios/día",
            focus_areas=["seguridad", "performance", "usabilidad"]
        )
        assert isinstance(result, str)
        assert "RAZONAMIENTO ESTRUCTURADO" in result
        assert "¿Cuál es la mejor forma" in result

    def test_think_tool_without_context(self):
        """Debe funcionar sin contexto"""
        tool = ThinkTool()
        result = tool._run(thought="Analizar problema")
        assert "CONTEXTO:" in result
        assert "No especificado" in result


class TestKnowledgeBaseSearch:
    """Tests para KnowledgeBaseSearch"""

    def setup_method(self):
        """Setup para cada test"""
        self.mock_vectorstore = Mock()
        self.tool = KnowledgeBaseSearch(vectorstore=self.mock_vectorstore)

    def test_search_success(self):
        """Debe buscar en vectorstore y retornar documentos"""
        # Mock resultados
        mock_doc1 = Mock(page_content="Doc 1", metadata={"source": "a"})
        mock_doc2 = Mock(page_content="Doc 2", metadata={"source": "b"})
        self.mock_vectorstore.similarity_search_with_relevance_scores.return_value = [
            (mock_doc1, 0.9),
            (mock_doc2, 0.85)
        ]

        result = self.tool._run(
            query="autenticación",
            k=5,
            similarity_threshold=0.8
        )
        assert result['status'] == 'success'
        assert result['total_results'] == 2
        assert len(result['documents']) == 2
        self.mock_vectorstore.similarity_search_with_relevance_scores.assert_called_once()

    def test_search_with_threshold_filter(self):
        """Debe filtrar por umbral de similitud"""
        mock_doc1 = Mock(page_content="Doc 1", metadata={})
        mock_doc2 = Mock(page_content="Doc 2", metadata={})
        self.mock_vectorstore.similarity_search_with_relevance_scores.return_value = [
            (mock_doc1, 0.9),
            (mock_doc2, 0.6)  # Bajo umbral
        ]
        result = self.tool._run(query="test", k=5, similarity_threshold=0.7)
        assert result['total_results'] == 1  # Solo el de 0.9

    def test_search_no_vectorstore(self):
        """Debe manejar vectorstore no disponible"""
        tool = KnowledgeBaseSearch(vectorstore=None)
        result = tool._run(query="test")
        assert result['status'] == 'error'
        assert 'no disponible' in result['error']

    def test_search_exception(self):
        """Debe manejar excepciones"""
        self.mock_vectorstore.similarity_search_with_relevance_scores.side_effect = Exception("DB error")
        result = self.tool._run(query="test")
        assert result['status'] == 'error'
        assert 'DB error' in result['error']


class TestMCPGoogleCalendarTool:
    """Tests para MCPGoogleCalendarTool"""

    def test_mcp_tool_without_endpoint(self):
        """Debe fallar sin endpoint configurado"""
        tool = MCPGoogleCalendarTool(mcp_endpoint=None)
        result = tool._run(action="list")
        assert result['success'] is False
        assert 'no configurado' in result['error']

    def test_mcp_tool_with_endpoint(self):
        """Debe ejecutar acción (simulada)"""
        tool = MCPGoogleCalendarTool(mcp_endpoint="http://localhost:8080")
        result = tool._run(
            action="create",
            title="Reunión",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T11:00:00"
        )
        assert result['status'] == 'simulated'
        assert result['event']['title'] == "Reunión"

    def test_mcp_tool_async(self):
        """Versión async debe funcionar"""
        tool = MCPGoogleCalendarTool(mcp_endpoint="http://localhost:8080")
        # No implementado realmente pero debería no fallar
        result = asyncio.run(tool._arun(action="list"))
        assert result['status'] == 'simulated'


class TestGetDeyyTools:
    """Tests para factory get_deyy_tools"""

    @patch('utils.tools.LangChainComponentFactory')
    def test_get_deyy_tools(self, mock_factory):
        """Debe retornar lista de herramientas"""
        tools = get_deyy_tools()
        assert len(tools) == 4  # planning, think, knowledge, mcp
        tool_names = [t.name for t in tools]
        assert 'planificador_obligatorio' in tool_names
        assert 'think' in tool_names
        assert 'knowledge_base_search' in tool_names
        assert 'mcp_google_calendar' in tool_names

    @patch('utils.tools.LangChainComponentFactory')
    def test_get_deyy_tools_with_vectorstore(self, mock_factory):
        """Debe aceptar vectorstore personalizado"""
        mock_vs = Mock()
        tools = get_deyy_tools(vectorstore=mock_vs)
        # Verificar que KnowledgeBaseSearch usa el vectorstore
        kb_tool = next(t for t in tools if t.name == 'knowledge_base_search')
        assert kb_tool.vectorstore == mock_vs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
