#!/usr/bin/env python3
"""
Configuración del Memory-Agent Integration.

Gestiona la creación del store vectorial (langgraph.store.BaseStore).
No crea backend híbrido; solo el store para memorias semánticas.
"""

from typing import Optional

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

# PostgreSQLStore está en langgraph.checkpoint.postgres en LangGraph 1.x
try:
    from langgraph.checkpoint.postgres import PostgreSQLStore
    HAS_POSTGRES_STORE = True
except ImportError:
    try:
        from langgraph.store.postgres import PostgreSQLStore
        HAS_POSTGRES_STORE = True
    except ImportError:
        HAS_POSTGRES_STORE = False
        PostgreSQLStore = None  # type: ignore

from core.config import get_settings


class MemoryAgentConfig:
    """Configuración del integrador de memoria vectorial."""

    def __init__(self):
        self.settings = get_settings()
        self.store: Optional[BaseStore] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Inicializa el store vectorial según configuración."""
        if self._initialized:
            return

        store_type = self.settings.MEMORY_AGENT_STORE_TYPE or "memory"

        if store_type == "postgres":
            if not HAS_POSTGRES_STORE or PostgreSQLStore is None:
                raise ImportError("PostgreSQLStore no disponible. Instala psycopg>=3.0.0 y langgraph>=1.0.0")

            store_url = self.settings.MEMORY_AGENT_STORE_URL or self.settings.DATABASE_URL
            if not store_url:
                raise ValueError("MEMORY_AGENT_STORE_URL o DATABASE_URL deben estar configurados")

            self.store = PostgreSQLStore(store_url)
            await self.store.setup()  # Crea tablas si no existen

        else:  # default: memory
            self.store = InMemoryStore()

        self._initialized = True
        print(f"✅ Memory-Agent vector store inicializado: {type(self.store).__name__}")

    def get_store(self) -> BaseStore:
        """Retorna el store vectorial inicializado."""
        if not self._initialized:
            raise RuntimeError("MemoryAgentConfig no inicializado. Llama a initialize() primero")
        return self.store


# Instancia global
_config: Optional[MemoryAgentConfig] = None


def get_memory_agent_config() -> MemoryAgentConfig:
    """Obtiene la instancia global de configuración."""
    global _config
    if _config is None:
        _config = MemoryAgentConfig()
    return _config
