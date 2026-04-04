# -*- coding: utf-8 -*-
"""
Componentes LangChain para Arcadium Automation
Factory pattern para crear LLMs, Memory, Embeddings, Vector Stores
"""

import os
from typing import Optional, Dict, Any, List
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.chat_message_histories import PostgresChatMessageHistory
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.messages import BaseMessage
from supabase import create_client
import structlog
from core.config import settings

logger = structlog.get_logger("langchain_components")


class LangChainComponentFactory:
    """
    Factory centralizado para crear componentes LangChain
    """

    @staticmethod
    def create_chat_model(
        model: str = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        streaming: bool = False
    ) -> ChatOpenAI:
        """
        Crea modelo de chat OpenAI

        Args:
            model: Modelo a usar (gpt-4, gpt-3.5-turbo, etc.)
            temperature: Temperatura (0-2)
            max_tokens: Máximo de tokens en respuesta
            streaming: Si usar streaming

        Returns:
            Instancia de ChatOpenAI
        """
        # Leer API key directamente del environment para reflejar cambios en tests
        api_key = os.getenv('OPENAI_API_KEY') or settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY no configurada")

        model = model or os.getenv('OPENAI_MODEL') or settings.OPENAI_MODEL or "gpt-4"

        logger.info(
            "Creando ChatOpenAI",
            model=model,
            temperature=temperature,
            streaming=streaming
        )

        return ChatOpenAI(
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            timeout=60,
            max_retries=3
        )

    @staticmethod
    def create_postgres_memory(
        session_id: str,
        table_name: str = None,
        connection_string: Optional[str] = None
    ) -> PostgresChatMessageHistory:
        """
        Crea memoria de conversación en PostgreSQL

        Args:
            session_id: ID de sesión único
            table_name: Nombre de tabla (default: langchain_memory)
            connection_string: Cadena de conexión PostgreSQL

        Returns:
            Instancia de PostgresChatMessageHistory
        """
        table_name = table_name or settings.POSTGRES_MEMORY_TABLE or "langchain_memory"

        # Usar DATABASE_URL de settings si está disponible
        db_url = connection_string or settings.DATABASE_URL
        if not db_url:
            raise ValueError("DATABASE_URL no configurada para Postgres memory")

        # Asegurar formato correcto para LangChain
        # LangChain espera: "postgresql://user:pass@host:port/db"
        if db_url.startswith("postgresql://"):
            pass
        elif db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        logger.info(
            "Creando PostgresChatMessageHistory",
            session_id=session_id,
            table_name=table_name
        )

        return PostgresChatMessageHistory(
            table_name=table_name,
            session_id=session_id,
            connection_string=db_url
        )

    @staticmethod
    def create_embeddings(
        model: str = "text-embedding-3-small"
    ) -> OpenAIEmbeddings:
        """
        Crea embeddings de OpenAI

        Args:
            model: Modelo de embeddings (text-embedding-3-small, text-embedding-3-large, etc.)

        Returns:
            Instancia de OpenAIEmbeddings
        """
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY no configurada")

        logger.info("Creando OpenAIEmbeddings", model=model)

        return OpenAIEmbeddings(
            api_key=api_key,
            model=model,
            timeout=60,
            max_retries=3
        )

    @staticmethod
    def create_supabase_vectorstore(
        table_name: str = "documents",
        embedder: Optional[OpenAIEmbeddings] = None,
        query_name: str = "match_documents"
    ) -> SupabaseVectorStore:
        """
        Crea vector store con Supabase (pgvector)

        Args:
            table_name: Nombre de tabla en Supabase
            embedder: Instancia de embeddings (se crea si no se provee)
            query_name: Nombre de la función RPC para búsqueda

        Returns:
            Instancia de SupabaseVectorStore
        """
        # Leer desde environment variables (preferir) o settings
        supabase_url = os.getenv('SUPABASE_URL') or getattr(settings, 'SUPABASE_URL', None)
        supabase_key = os.getenv('SUPABASE_ANON_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or getattr(settings, 'SUPABASE_ANON_KEY', None) or getattr(settings, 'SUPABASE_SERVICE_ROLE_KEY', None)

        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL y SUPABASE_KEY requeridos")

        if embedder is None:
            embedder = LangChainComponentFactory.create_embeddings()

        logger.info(
            "Creando SupabaseVectorStore",
            table_name=table_name,
            supabase_url=supabase_url
        )

        return SupabaseVectorStore(
            embedding=embedder,
            connection=create_client(supabase_url, supabase_key),
            table_name=table_name,
            query_name=query_name
        )

    @staticmethod
    def create_all_components(
        session_id: str,
        llm_model: str = "gpt-4",
        memory_table: str = "langchain_memory",
        vector_table: str = "documents"
    ) -> Dict[str, Any]:
        """
        Crea todos los componentes LangChain de una vez

        Args:
            session_id: ID de sesión para memoria
            llm_model: Modelo LLM
            memory_table: Tabla de memoria
            vector_table: Tabla de vector store

        Returns:
            Dict con todos los componentes: {
                'llm': ChatOpenAI,
                'embeddings': OpenAIEmbeddings,
                'memory': PostgresChatMessageHistory,
                'vectorstore': SupabaseVectorStore
            }
        """
        logger.info("Creando todos los componentes LangChain")

        return {
            "llm": LangChainComponentFactory.create_chat_model(model=llm_model),
            "embeddings": LangChainComponentFactory.create_embeddings(),
            "memory": LangChainComponentFactory.create_postgres_memory(
                session_id=session_id,
                table_name=memory_table
            ),
            "vectorstore": LangChainComponentFactory.create_supabase_vectorstore(
                table_name=vector_table
            )
        }


# Funciones de conveniencia
async def get_components_for_deyy_agent(phone: str) -> Dict[str, Any]:
    """
    obtén componentes para agente Deyy basado en teléfono

    Args:
        phone: Número telefónico como session_id

    Returns:
        Dict con componentes
    """
    session_id = f"deyy_{phone}"
    return LangChainComponentFactory.create_all_components(
        session_id=session_id,
        llm_model=settings.OPENAI_MODEL or "gpt-4",
        memory_table="langchain_memory_deyy"
    )
