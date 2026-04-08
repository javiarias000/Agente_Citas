#!/usr/bin/env python3
"""
Backend de memoria híbrido: combina PostgreSQL secuencial + Store vectorial.

Este backend:
- Usa PostgreSQLMemory (langchain) para historial secuencial de mensajes
- Usa BaseStore (langgraph) para memorias semánticas vectoriales
- Proporciona una interfaz unificada para el agente
"""

import uuid
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# Importar backends existentes
from memory.memory_manager import InMemoryStorage, PostgreSQLMemory

logger = structlog.get_logger("memory_agent_backend")


class MemoryAgentBackend:
    """
    Backend híbrido que combina:
    - PostgreSQL secuencial (langchain_memory table) → historial de conversación
    - Store vectorial (langgraph.store) → memorias semánticas

    Atributos:
        settings: Configuración de la aplicación
        store: BaseStore para memorias vectoriales (inicializado en initialize())
        sequential_backend: InMemoryStorage o PostgreSQLMemory (historial secuencial)
    """

    def __init__(self, settings=None):
        from core.config import get_settings

        self.settings = settings or get_settings()
        self.store = None  # BaseStore (inicializado en initialize())
        self.sequential_backend = None  # Inicializado en initialize()
        self._initialized = False
        self._lock = None

        logger.info(
            "MemoryAgentBackend creado",
            store_type=self.settings.MEMORY_AGENT_STORE_TYPE,
        )

    async def initialize(self):
        """Inicializa el store vectorial y el backend secuencial."""
        import asyncio

        from langgraph.store.memory import InMemoryStore as LGInMemoryStore

        # Crear lock
        if self._lock is None:
            self._lock = asyncio.Lock()

        # 1. Inicializar store vectorial (memory-agent)
        from memory_agent_integration.config import get_memory_agent_config

        mem_config = get_memory_agent_config()
        await mem_config.initialize()
        self.store = mem_config.get_store()

        # 2. Inicializar backend secuencial (PostgreSQLMemory o InMemory)
        if self.settings.USE_POSTGRES_FOR_MEMORY:
            from memory.postgres_memory import PostgreSQLMemory as PGMemory
            self.sequential_backend = PGMemory()
        else:
            self.sequential_backend = InMemoryStorage()

        # Inicializar el backend secuencial si es necesario
        if hasattr(self.sequential_backend, 'initialize'):
            await self.sequential_backend.initialize()

        self._initialized = True
        logger.info(
            "MemoryAgentBackend inicializado",
            store=type(self.store).__name__,
            sequential=type(self.sequential_backend).__name__,
        )

    # ============ API para historial secuencial (compatible con BaseMemory) ============

    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        """Obtiene historial de mensajes (usando sequential_backend)."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.get_history(session_id, project_id, limit)

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Añade un mensaje al historial secuencial."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.add_message(session_id, message, project_id)

    # ============ API para memorias semánticas (BaseStore) ============

    async def search_memories(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        threshold: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """
        Busca memorias semánticas relevantes para un usuario.

        Args:
            user_id: ID del usuario (phone number)
            query: Texto para buscar similitud
            k: Número máximo de resultados
            threshold: Umbral de similitud (0-1)

        Returns:
            Lista de diccionarios con {content, context, score, memory_id}
        """
        if not self._initialized:
            await self.initialize()

        namespace = ("memories", user_id)

        # Usar store.asearch para búsqueda semántica
        # items: List[StoreItem] donde item.key, item.value, item.score
        items = await self.store.asearch(
            namespace,
            query=query,
            k=k,
            # filter={}  # opcional
        )

        # Filtrar por threshold y formatear resultados
        results = []
        for item in items:
            if item.score >= threshold:
                results.append({
                    "content": item.value.get("content", ""),
                    "context": item.value.get("context", ""),
                    "score": float(item.score),
                    "memory_id": item.key[2] if len(item.key) >= 3 else None,
                })

        return results

    async def save_memory(
        self,
        user_id: str,
        content: str,
        context: str = "",
        memory_id: Optional[str] = None,
    ) -> str:
        """
        Guarda una memoria semántica para un usuario.

        Returns:
            memory_id de la memoria guardada
        """
        if not self._initialized:
            await self.initialize()

        namespace = ("memories", user_id)

        if memory_id:
            # Actualizar memoria existente
            key = (*namespace, memory_id)
            await self.store.ainput(
                key,
                {
                    "content": content,
                    "context": context,
                    "user_id": user_id,
                    "updated_at": uuid.uuid1().hex,  # timestamp简化
                },
            )
            return memory_id
        else:
            # Crear nueva memoria
            memory_id = str(uuid.uuid4())
            key = (*namespace, memory_id)
            await self.store.aput(
                key,
                {
                    "content": content,
                    "context": context,
                    "user_id": user_id,
                    "created_at": uuid.uuid1().hex,
                },
            )
            return memory_id

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        """Elimina una memoria específica."""
        if not self._initialized:
            await self.initialize()

        namespace = ("memories", user_id)
        key = (*namespace, memory_id)
        try:
            await self.store.adelete(key)
            return True
        except Exception as e:
            logger.error("Error eliminando memoria", error=str(e))
            return False

    # ============ Estado de conversación ( State Machine ) ============

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        """Obtiene el estado guardado del agente (para StateMachine)."""
        if not self._initialized:
            await self.initialize()
        # Usar sequential_backend.get_state si existe
        if hasattr(self.sequential_backend, 'get_state'):
            return await self.sequential_backend.get_state(phone)
        return None

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        """Guarda el estado del agente."""
        if not self._initialized:
            await self.initialize()
        if hasattr(self.sequential_backend, 'save_state'):
            await self.sequential_backend.save_state(phone, state)
