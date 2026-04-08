#!/usr/bin/env python3
"""
Backend de memoria híbrido: combina historial secuencial + store vectorial semántico.

Implementa la interfaz BaseStore de src/store.py como drop-in replacement de
PostgresStore, agregando búsqueda semántica a través del vector store de LangGraph.

Arquitectura:
- sequential_backend (src/store.PostgresStore o InMemoryStore)
    → historial de conversación (langchain_memory)
    → estado persistente del agente (agent_states)
    → perfil de usuario (user_profiles)
- store (langgraph.store.BaseStore)
    → memorias semánticas vectoriales (facts, preferencias, datos del paciente)
"""

import uuid
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import BaseMessage

logger = structlog.get_logger("memory_agent_backend")


class MemoryAgentBackend:
    """
    Backend híbrido que combina:
    - PostgresStore/InMemoryStore (src/store.py) → historial + estado + perfil
    - LangGraph BaseStore vectorial               → memorias semánticas

    Es un drop-in replacement del PostgresStore: implementa la misma interfaz
    y puede pasarse directamente a ArcadiumAgent y compile_graph.

    Args:
        settings:  Configuración de la aplicación (get_settings()).
        engine:    SQLAlchemy async engine. Requerido si USE_POSTGRES_FOR_MEMORY=True.
    """

    def __init__(self, settings=None, engine=None):
        from core.config import get_settings

        self.settings = settings or get_settings()
        self._engine = engine
        self.store = None               # LangGraph BaseStore (vector store)
        self.sequential_backend = None  # src/store.BaseStore (historial + estado)
        self._initialized = False
        self._lock = None

        logger.info(
            "MemoryAgentBackend creado",
            store_type=self.settings.MEMORY_AGENT_STORE_TYPE,
            use_postgres=self.settings.USE_POSTGRES_FOR_MEMORY,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Inicialización
    # ─────────────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Inicializa el sequential backend y el vector store."""
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            if self._initialized:
                return

            # 1. Sequential backend (historial + estado + perfil)
            if self.settings.USE_POSTGRES_FOR_MEMORY:
                if self._engine is None:
                    raise RuntimeError(
                        "MemoryAgentBackend requiere 'engine' cuando USE_POSTGRES_FOR_MEMORY=True"
                    )
                from src.store import PostgresStore
                self.sequential_backend = PostgresStore(self._engine)
            else:
                from src.store import InMemoryStore
                self.sequential_backend = InMemoryStore()

            await self.sequential_backend.initialize()

            # 2. Vector store (memorias semánticas) — siempre InMemoryStore por defecto.
            #    Se reemplaza por PostgreSQLStore si MEMORY_AGENT_STORE_TYPE=postgres.
            from memory_agent_integration.config import get_memory_agent_config
            mem_cfg = get_memory_agent_config()
            await mem_cfg.initialize()
            self.store = mem_cfg.get_store()

            self._initialized = True
            logger.info(
                "MemoryAgentBackend inicializado",
                sequential=type(self.sequential_backend).__name__,
                vector_store=type(self.store).__name__,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Historial de conversación (delega a sequential_backend)
    # ─────────────────────────────────────────────────────────────────────────

    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]:
        """Obtiene los últimos `limit` mensajes del historial."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.get_history(phone, limit)

    async def add_message(
        self,
        phone: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Agrega un mensaje al historial."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.add_message(phone, message, project_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Estado del agente (delega a sequential_backend)
    # ─────────────────────────────────────────────────────────────────────────

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        """Recupera el estado persistido del agente para una sesión."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.get_agent_state(phone)

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        """Persiste el estado del agente."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.save_agent_state(phone, state)

    # ─────────────────────────────────────────────────────────────────────────
    # Perfil de usuario (delega a sequential_backend)
    # ─────────────────────────────────────────────────────────────────────────

    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        """Recupera el perfil del usuario."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.get_user_profile(phone)

    async def upsert_user_profile(
        self, phone: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Crea o actualiza el perfil del usuario."""
        if not self._initialized:
            await self.initialize()
        return await self.sequential_backend.upsert_user_profile(phone, updates)

    # ─────────────────────────────────────────────────────────────────────────
    # Memorias semánticas (vector store)
    # ─────────────────────────────────────────────────────────────────────────

    async def search_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.7,
        project_id: Optional[uuid.UUID] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca memorias semánticas relevantes para un usuario.

        Args:
            user_id:    Número de teléfono del usuario (phone).
            query:      Texto de búsqueda por similitud.
            limit:      Número máximo de resultados.
            threshold:  Umbral de similitud mínima (0–1).
            project_id: Ignorado por ahora (para compatibilidad futura).

        Returns:
            Lista de dicts con: key, content, context, score, memory_id
        """
        if not self._initialized:
            await self.initialize()

        if self.store is None:
            return []

        namespace = ("memories", user_id)

        try:
            items = await self.store.asearch(namespace, query=query, k=limit)
        except Exception as e:
            logger.warning("Error buscando memorias semánticas", user_id=user_id, error=str(e))
            return []

        results = []
        for item in items:
            score = float(getattr(item, "score", 0.0))
            if score < threshold:
                continue
            memory_id = item.key[2] if len(item.key) >= 3 else str(item.key)
            results.append({
                "key": memory_id,           # compatible con agent._get_semantic_context
                "content": item.value.get("content", ""),
                "context": item.value.get("context", ""),
                "score": score,
                "memory_id": memory_id,
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
        Guarda una memoria semántica para el usuario.

        Returns:
            memory_id de la memoria guardada/actualizada.
        """
        if not self._initialized:
            await self.initialize()

        namespace = ("memories", user_id)

        if memory_id:
            await self.store.aput(
                (*namespace, memory_id),
                {"content": content, "context": context, "user_id": user_id},
            )
            return memory_id

        new_id = str(uuid.uuid4())
        await self.store.aput(
            (*namespace, new_id),
            {"content": content, "context": context, "user_id": user_id},
        )
        return new_id

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        """Elimina una memoria específica del usuario."""
        if not self._initialized:
            await self.initialize()

        try:
            await self.store.adelete(("memories", user_id, memory_id))
            return True
        except Exception as e:
            logger.error("Error eliminando memoria", user_id=user_id, error=str(e))
            return False
