# -*- coding: utf-8 -*-
"""
Tests para componentes LangChain
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock
import os

# Configurar variables de entorno para tests
os.environ['OPENAI_API_KEY'] = 'sk-test-key'
os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost/test'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
os.environ['SUPABASE_ANON_KEY'] = 'test-anon-key'

from utils.langchain_components import LangChainComponentFactory


class TestLangChainComponentFactory:
    """Tests para el factory de componentes LangChain"""

    @patch.dict(os.environ, {}, clear=True)
    @patch('utils.langchain_components.settings')
    def test_create_chat_model_without_api_key(self, mock_settings):
        """Debe fallar sin OPENAI_API_KEY"""
        mock_settings.OPENAI_API_KEY = None
        with pytest.raises(ValueError, match="OPENAI_API_KEY no configurada"):
            LangChainComponentFactory.create_chat_model()

    @patch('utils.langchain_components.ChatOpenAI')
    def test_create_chat_model_success(self, mock_chat):
        """Debe crear ChatOpenAI correctamente"""
        os.environ['OPENAI_API_KEY'] = 'sk-test'
        model = LangChainComponentFactory.create_chat_model(
            model="gpt-3.5-turbo",
            temperature=0.5
        )
        assert model is not None
        mock_chat.assert_called_once()
        call_kwargs = mock_chat.call_args[1]
        assert call_kwargs['model'] == 'gpt-3.5-turbo'
        assert call_kwargs['temperature'] == 0.5

    @patch('utils.langchain_components.OpenAIEmbeddings')
    def test_create_embeddings(self, mock_embeddings):
        """Debe crear embeddings"""
        os.environ['OPENAI_API_KEY'] = 'sk-test'
        emb = LangChainComponentFactory.create_embeddings()
        mock_embeddings.assert_called_once()

    @patch('utils.langchain_components.PostgresChatMessageHistory')
    def test_create_postgres_memory(self, mock_memory):
        """Debe crear memoria PostgreSQL"""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost/test'
        memory = LangChainComponentFactory.create_postgres_memory(
            session_id="test_session",
            table_name="test_table"
        )
        mock_memory.assert_called_once()
        args, kwargs = mock_memory.call_args
        assert kwargs['session_id'] == "test_session"
        assert kwargs['table_name'] == "test_table"

    @patch('utils.langchain_components.SupabaseVectorStore')
    @patch('utils.langchain_components.create_client')
    @patch('utils.langchain_components.OpenAIEmbeddings')
    def test_create_supabase_vectorstore(self, mock_emb, mock_client, mock_store):
        """Debe crear vector store Supabase"""
        os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
        os.environ['SUPABASE_ANON_KEY'] = 'anon-key'
        os.environ['OPENAI_API_KEY'] = 'sk-test'

        vs = LangChainComponentFactory.create_supabase_vectorstore()
        mock_client.assert_called_once()
        mock_store.assert_called_once()

    @patch('utils.langchain_components.LangChainComponentFactory.create_chat_model')
    @patch('utils.langchain_components.LangChainComponentFactory.create_embeddings')
    @patch('utils.langchain_components.LangChainComponentFactory.create_postgres_memory')
    @patch('utils.langchain_components.LangChainComponentFactory.create_supabase_vectorstore')
    def test_create_all_components(self, mock_vs, mock_mem, mock_emb, mock_llm):
        """Debe crear todos los componentes"""
        os.environ['OPENAI_API_KEY'] = 'sk-test'
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost/test'
        os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
        os.environ['SUPABASE_ANON_KEY'] = 'anon-key'

        components = LangChainComponentFactory.create_all_components(
            session_id="test_session"
        )
        assert 'llm' in components
        assert 'embeddings' in components
        assert 'memory' in components
        assert 'vectorstore' in components
        mock_llm.assert_called_once()
        mock_emb.assert_called_once()
        mock_mem.assert_called_once()
        mock_vs.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
