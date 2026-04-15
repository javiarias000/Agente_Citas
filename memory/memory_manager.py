#!/usr/bin/env python3
"""
Gestor de memoria modular para agentes
Soporta: InMemory (dev) y PostgreSQL (prod) - Tabla única escalable

FIXES APLICADOS:
- [CRÍTICO] Race condition en initialize() → asyncio.Lock()
- [CRÍTICO] to_langchain_history() usaba asyncio.run() en contexto async → ahora es async
- [CRÍTICO] add_message acepta BaseMessage directamente (además de content+type)
- [MEDIO]   InMemoryStorage.get_state() ahora usa project_id como parte de la clave
"""

import asyncio
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from core.config import get_settings

logger = structlog.get_logger("memory.manager")


class BaseMemory(ABC):
    """Interfaz abstracta para backends de memoria"""

    @abstractmethod
    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        """Obtiene historial de mensajes para una sesión."""
        pass

    @abstractmethod
    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Añade un BaseMessage al historial."""
        pass

    @abstractmethod
    async def clear_session(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        """Limpia historial de una sesión"""
        pass

    @abstractmethod
    async def cleanup_expired(self, expiry_hours: int) -> int:
        """Limpia sesiones expiradas, retorna número de eliminadas"""
        pass

    @abstractmethod
    async def get_state(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        """Obtiene el estado de state machine para una sesión"""
        pass

    @abstractmethod
    async def save_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Guarda el estado de state machine para una sesión"""
        pass


class InMemoryStorage(BaseMemory):
    """
    Almacenamiento en memoria (solo desarrollo, un único proceso).

    IMPORTANTE: No compartido entre workers. Solo usar con un proceso único.
    """

    def __init__(self):
        self._sessions: Dict[str, List[BaseMessage]] = {}
        self._timestamps: Dict[str, datetime] = {}
        # FIX: clave compuesta (session_id, project_id) para evitar colisiones entre proyectos
        self._states: Dict[tuple, Dict[str, Any]] = {}
        self._profiles: Dict[tuple, Dict[str, Any]] = {}
        logger.info("InMemoryStorage inicializado")

    def _state_key(self, session_id: str, project_id: Optional[uuid.UUID]) -> tuple:
        """Clave compuesta para evitar colisiones multi-tenant en dev."""
        return (session_id, str(project_id) if project_id else "__none__")

    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._timestamps[session_id] = datetime.now(timezone.utc)
        history = self._sessions[session_id]
        if limit is not None:
            return list(history[-limit:])
        return list(history)

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(message)
        self._timestamps[session_id] = datetime.now(timezone.utc)

    async def clear_session(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        self._sessions.pop(session_id, None)
        self._timestamps.pop(session_id, None)
        # Limpiar estados de este session para todos los proyectos
        keys_to_remove = [k for k in self._states if k[0] == session_id]
        for k in keys_to_remove:
            self._states.pop(k, None)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        expired = [sid for sid, ts in self._timestamps.items() if ts < cutoff]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._timestamps.pop(sid, None)
            keys_to_remove = [k for k in self._states if k[0] == sid]
            for k in keys_to_remove:
                self._states.pop(k, None)
        logger.info("Sesiones limpiadas", expired_count=len(expired))
        return len(expired)

    # ============================================
    # State Machine Methods
    # ============================================

    async def get_state(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        """FIX: Ahora usa clave compuesta (session_id, project_id) para evitar colisiones."""
        key = self._state_key(session_id, project_id)
        return self._states.get(key)

    async def save_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """FIX: Ahora usa clave compuesta."""
        key = self._state_key(session_id, project_id)
        self._states[key] = state

    # ============================================
    # User Profile Methods
    # ============================================

    async def get_user_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        key = (phone_number, str(project_id) if project_id else "__none__")
        return self._profiles.get(key)

    async def create_or_update_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None, **updates
    ) -> Dict[str, Any]:
        key = (phone_number, str(project_id) if project_id else "__none__")
        if key not in self._profiles:
            self._profiles[key] = {}
        self._profiles[key].update(updates)
        return self._profiles[key]

    async def increment_user_conversation_count(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        key = (phone_number, str(project_id) if project_id else "__none__")
        if key not in self._profiles:
            self._profiles[key] = {"conversation_count": 0}
        self._profiles[key]["conversation_count"] = (
            self._profiles[key].get("conversation_count", 0) + 1
        )

    async def update_user_last_seen(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        key = (phone_number, str(project_id) if project_id else "__none__")
        if key not in self._profiles:
            self._profiles[key] = {}
        self._profiles[key]["last_seen"] = datetime.now(timezone.utc)


# Import perezoso para evitar dependencias circulares
def _get_postgres_storage():
    from memory.postgres_memory import PostgresStorage

    return PostgresStorage


class PostgreSQLMemory(BaseMemory):
    """
    Wrapper para almacenamiento PostgreSQL usando tabla única.
    """

    def __init__(self):
        self._backend = None
        self._lock = asyncio.Lock()  # FIX: lock para initialize() thread-safe
        logger.info("PostgreSQLMemory wrapper creado")

    async def initialize(self):
        """FIX: Inicialización thread-safe con asyncio.Lock."""
        async with self._lock:
            if self._backend is None:
                self._backend = _get_postgres_storage()()
                await self._backend.initialize()

    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        await self.initialize()
        return await self._backend.get_history(
            session_id, project_id=project_id, limit=limit
        )

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        await self.initialize()
        await self._backend.add_message(session_id, message, project_id=project_id)

    async def clear_session(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        await self.initialize()
        await self._backend.clear_session(session_id, project_id=project_id)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        await self.initialize()
        return await self._backend.cleanup_expired(expiry_hours)

    async def get_state(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        await self.initialize()
        return await self._backend.get_state(session_id, project_id=project_id)

    async def save_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        await self.initialize()
        await self._backend.save_state(session_id, state, project_id=project_id)


class MemoryManager:
    """
    Gestor principal de memoria.
    Selecciona backend basado en configuración.
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._backend: Optional[BaseMemory] = None
        self._initialized = False
        self._lock = asyncio.Lock()  # FIX: lock para race condition en initialize()

        logger.info(
            "MemoryManager creado", use_postgres=self.settings.USE_POSTGRES_FOR_MEMORY
        )

    async def initialize(self) -> None:
        """FIX: Inicialización thread-safe con asyncio.Lock."""
        async with self._lock:
            if self._initialized:
                return

            if self.settings.USE_POSTGRES_FOR_MEMORY:
                self._backend = PostgreSQLMemory()
                await self._backend.initialize()
                logger.info("Backend PostgreSQL para memoria", backend="postgres")
            else:
                self._backend = InMemoryStorage()
                logger.info("Backend InMemory para memoria", backend="memory")

            self._initialized = True

    async def _ensure_initialized(self) -> None:
        """Helper interno para garantizar inicialización antes de cada operación."""
        if not self._initialized:
            await self.initialize()

    # ============================================
    # HISTORIAL
    # ============================================

    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        await self._ensure_initialized()
        return await self._backend.get_history(session_id, project_id, limit=limit)

    async def add_message(
        self,
        session_id: str,
        message: Optional[BaseMessage] = None,
        *,
        content: Optional[str] = None,
        message_type: str = "human",
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """
        FIX: Acepta tanto BaseMessage directamente como content+message_type.

        Uso 1 (desde grafo/agentes):
            await manager.add_message(session_id, msg)  # msg es BaseMessage

        Uso 2 (legacy desde API/webhooks):
            await manager.add_message(session_id, content="Hola", message_type="human")
        """
        await self._ensure_initialized()

        if message is not None:
            # Modo 1: BaseMessage directo
            if not isinstance(message, BaseMessage):
                raise TypeError(
                    f"'message' debe ser una instancia de BaseMessage, "
                    f"recibido: {type(message).__name__}"
                )
            msg_obj = message
        elif content is not None:
            # Modo 2: content + type (legacy)
            if message_type == "human":
                msg_obj = HumanMessage(content=content)
            elif message_type == "ai":
                msg_obj = AIMessage(content=content)
            else:
                raise ValueError(f"Tipo de mensaje inválido: {message_type}")
        else:
            raise ValueError(
                "Debes proporcionar 'message' (BaseMessage) o 'content' (str)."
            )

        logger.debug(
            "Agregando mensaje a memoria",
            session_id=session_id,
            project_id=str(project_id) if project_id else None,
            type=type(msg_obj).__name__,
            content_length=len(msg_obj.content),
        )
        await self._backend.add_message(session_id, msg_obj, project_id)

    async def clear_session(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        await self._ensure_initialized()
        await self._backend.clear_session(session_id, project_id)

    async def cleanup_expired_sessions(self) -> int:
        await self._ensure_initialized()
        return await self._backend.cleanup_expired(
            expiry_hours=self.settings.SESSION_EXPIRY_HOURS
        )

    async def to_langchain_history(self, session_id: str) -> List[BaseMessage]:
        """
        FIX: Ahora es async (era síncrono con asyncio.run() que rompía en contexto async).

        Uso correcto:
            history = await memory_manager.to_langchain_history(session_id)
        """
        return await self.get_history(session_id)

    # ============================================
    # USER PROFILES
    # ============================================

    async def get_user_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Any]:
        await self._ensure_initialized()
        if hasattr(self._backend, "get_user_profile"):
            return await self._backend.get_user_profile(phone_number, project_id)
        return None

    async def create_or_update_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None, **updates
    ) -> Any:
        await self._ensure_initialized()
        if hasattr(self._backend, "create_or_update_profile"):
            return await self._backend.create_or_update_profile(
                phone_number, project_id, **updates
            )
        raise NotImplementedError("Backend no soporta gestión de perfiles")

    async def increment_user_conversation_count(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        await self._ensure_initialized()
        if hasattr(self._backend, "increment_user_conversation_count"):
            await self._backend.increment_user_conversation_count(
                phone_number, project_id
            )

    async def update_user_last_seen(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        await self._ensure_initialized()
        if hasattr(self._backend, "update_user_last_seen"):
            await self._backend.update_user_last_seen(phone_number, project_id)

    async def extract_and_save_facts_from_conversation(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        user_message: str,
        agent_response: str,
    ) -> None:
        await self._ensure_initialized()
        if hasattr(self._backend, "extract_and_save_facts_from_conversation"):
            await self._backend.extract_and_save_facts_from_conversation(
                phone_number, project_id, user_message, agent_response
            )

    # ============================================
    # STATE MACHINE
    # ============================================

    async def get_state(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_initialized()
        if hasattr(self._backend, "get_state"):
            return await self._backend.get_state(session_id, project_id=project_id)
        return None

    async def save_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        await self._ensure_initialized()
        if hasattr(self._backend, "save_state"):
            await self._backend.save_state(session_id, state, project_id=project_id)
        else:
            logger.warning(
                "Backend no soporta save_state", backend=type(self._backend).__name__
            )
