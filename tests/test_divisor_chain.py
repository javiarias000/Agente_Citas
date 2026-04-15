# -*- coding: utf-8 -*-
"""
Tests para DivisorChain
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import os

os.environ['OPENAI_API_KEY'] = 'sk-test-key'

from chains.divisor_chain import DivisorChain, dividir_mensaje, MessagePart


class TestDivisorChain:
    """Tests para DivisorChain"""

    @patch('chains.divisor_chain.ChatOpenAI')
    def test_init(self, mock_chat):
        """Debe inicializar cadena"""
        chain = DivisorChain()
        assert chain.llm is not None
        assert chain.prompt is not None
        assert chain.chain is not None

    @patch('chains.divisor_chain.ChatOpenAI')
    def test_init_custom_model(self, mock_chat):
        """Debe usar modelo personalizado"""
        # Parchear settings para que OPENAI_API_KEY sea la del environment (que ya seteamos)
        from core.config import settings as real_settings
        original_key = real_settings.OPENAI_API_KEY
        real_settings.OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'sk-test-key')
        try:
            chain = DivisorChain(model="gpt-3.5-turbo", temperature=0.5)
            mock_chat.assert_called_with(
                api_key=os.environ.get('OPENAI_API_KEY'),
                model="gpt-3.5-turbo",
                temperature=0.5,
                timeout=60,
                max_retries=3
            )
        finally:
            real_settings.OPENAI_API_KEY = original_key

    @pytest.mark.asyncio
    @patch('chains.divisor_chain.ChatOpenAI')
    async def test_process_single_empty(self, mock_chat):
        """Debe manejar mensaje vacío"""
        chain = DivisorChain()
        result = await chain.process_single("")
        assert result == []

    @pytest.mark.asyncio
    @patch('chains.divisor_chain.ChatOpenAI')
    async def test_process_single_simple(self, mock_chat):
        """Debe procesar mensaje simple sin saltar"""
        # Mock LLM para devolver JSON válido
        mock_llm_instance = AsyncMock()
        # El chain: prompt | llm | parser, así que llm debe devolver un string JSON
        mock_llm_instance.ainvoke.return_value = {
            "parte": "Hola, ¿cómo estás?",
            "categoria": "pregunta",
            "prioridad": 2,
            "razonamiento": "Es una pregunta amistosa"
        }
        mock_chat.return_value = mock_llm_instance

        chain = DivisorChain()
        # Necesitamos parchear el parser para que devuelva MessagePart
        # En realidad, el parser convierte dict a MessagePart. Como ainvoke devuelve dict, parser funciona.
        result = await chain.process_single("Hola, ¿cómo estás?")
        assert len(result) >= 1
        assert isinstance(result[0], MessagePart)

    @pytest.mark.asyncio
    @patch('chains.divisor_chain.ChatOpenAI')
    async def test_process_single_error_fallback(self, mock_chat):
        """Debe usar fallback si LLM falla"""
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.side_effect = Exception("API error")
        mock_chat.return_value = mock_llm_instance

        chain = DivisorChain()
        result = await chain.process_single("Esto es una prueba de error")
        # Fallback debería devolver al menos 1 parte
        assert len(result) >= 1
        assert isinstance(result[0], MessagePart)

    def test_fallback_split_with_paragraphs(self):
        """Fallback debe dividir por párrafos"""
        chain = DivisorChain()
        text = "Primer párrafo.\n\nSegundo párrafo.\n\nTercer párrafo."
        parts = chain._fallback_split(text)
        # Esperamos 3 partes
        assert len(parts) >= 2  # Al menos 2

    def test_validate_result_empty(self):
        """Validación debe fallar con partes vacías"""
        chain = DivisorChain()
        validation = chain.validate_result([])
        assert validation['valid'] is False
        assert validation['quality_score'] == 0.0

    def test_validate_result_good(self):
        """Validación debe aprobar partes buenas"""
        chain = DivisorChain()
        parts = [
            MessagePart(parte="Test parte 1", categoria="pregunta", prioridad=2, razonamiento="Test"),
            MessagePart(parte="Test parte 2", categoria="comando", prioridad=1, razonamiento="Test")
        ]
        validation = chain.validate_result(parts)
        assert validation['valid'] is True
        assert validation['total_parts'] == 2
        assert validation['avg_priority'] == 1.5
        assert 'pregunta' in validation['category_distribution']
        assert 'comando' in validation['category_distribution']


class TestDividirMensaje:
    """Tests para función helper dividir_mensaje"""

    @pytest.mark.asyncio
    @patch('chains.divisor_chain.DivisorChain.process_single')
    async def test_dividir_mensaje(self, mock_process):
        """Debe dividir mensaje y devolver estructura"""
        mock_process.return_value = [
            MessagePart(parte="Parte 1", categoria="pregunta", prioridad=2, razonamiento="..."),
            MessagePart(parte="Parte 2", categoria="informacion", prioridad=3, razonamiento="...")
        ]
        result = await dividir_mensaje("Mensaje de prueba")
        assert 'mensaje_original' in result
        assert 'total_partes' in result
        assert result['total_partes'] == 2
        assert 'partes' in result
        assert 'validacion' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
