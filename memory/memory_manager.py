#!/usr/bin/env python3
"""
Gestor de memoria modular para agentes
Soporta: InMemory (dev) y PostgreSQL (prod) - Tabla única escalable
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from abc import ABC, abstractmethod
import structlog

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    SystemMessage
)

from core.config import get_settings

logger = structlog.get_logger("memory.manager")


class BaseMemory(ABC):
    """Interfaz abstracta para backends de memoria"""

    @abstractmethod
    async def get_history(self, session_id: str) -> List[BaseMessage]:
        """Obtiene historial de mensajes para una sesión"""
        pass

    @abstractmethod
    async def add_message(self, session_id: str, message: BaseMessage) -> None:
        """Añade un mensaje al historial"""
        pass

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """Limpia historial de una sesión"""
        pass

    @abstractmethod
    async def cleanup_expired(self, expiry_hours: int) -> int:
        """Limpia sesiones expiradas, retorna número de eliminadas"""
        pass


class InMemoryStorage(BaseMemory):
    """Almacenamiento en memoria (solo desarrollo)"""

    def __init__(self):
        self._sessions: Dict[str, List[BaseMessage]] = {}
        self._timestamps: Dict[str, datetime] = {}
        logger.info("InMemoryStorage inicializado")

    async def get_history(self, session_id: str) -> List[BaseMessage]:
        """Obtiene historial, creando vacío si no existe"""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._timestamps[session_id] = datetime.now(timezone.utc)
        return self._sessions[session_id]

    async def add_message(self, session_id: str, message: BaseMessage) -> None:
        """Añade mensaje y actualiza timestamp"""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(message)
        self._timestamps[session_id] = datetime.now(timezone.utc)

    async def clear_session(self, session_id: str) -> None:
        """Elimina sesión"""
        self._sessions.pop(session_id, None)
        self._timestamps.pop(session_id, None)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """Elimina sesiones expiradas"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        expired = [
            sid for sid, ts in self._timestamps.items()
            if ts < cutoff
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._timestamps.pop(sid, None)
        logger.info("Sesiones limpiadas", expired_count=len(expired))
        return len(expired)


# Import perezoso para evitar dependencias circulares
def _get_postgres_storage():
    """Importa la clase de almacenamiento PostgreSQL solo cuando se necesita"""
    from memory.postgres_memory import PostgresStorage
    return PostgresStorage


class PostgreSQLMemory(BaseMemory):
    """
    Wrapper para almacenamiento PostgreSQL usando tabla única.
    Escalable: todas las sesiones en la misma tabla.
    """

    def __init__(self):
        self._backend = None  # Se inicializa en initialize()
        logger.info("PostgreSQLMemory wrapper creado")

    async def initialize(self):
        """Inicializa el backend"""
        if self._backend is None:
            self._backend = _get_postgres_storage()()
            await self._backend.initialize()

    async def get_history(self, session_id: str) -> List[BaseMessage]:
        """Obtiene historial desde PostgreSQL"""
        await self.initialize()
        return await self._backend.get_history(session_id)

    async def add_message(self, session_id: str, message: BaseMessage) -> None:
        """Añade mensaje a PostgreSQL"""
        await self.initialize()
        await self._backend.add_message(session_id, message)

    async def clear_session(self, session_id: str) -> None:
        """Limpia sesión en PostgreSQL"""
        await self.initialize()
        await self._backend.clear_session(session_id)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """Limpia sesiones expiradas"""
        await self.initialize()
        return await self._backend.cleanup_expired(expiry_hours)


class MemoryManager:
    """
    Gestor principal de memoria
    Selecciona backend basado en configuración
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._backend: Optional[BaseMemory] = None
        self._initialized = False

        logger.info("MemoryManager creado", use_postgres=self.settings.USE_POSTGRES_FOR_MEMORY)

    async def initialize(self) -> None:
        """Inicializa el backend de memoria"""
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

    async def get_history(self, session_id: str) -> List[BaseMessage]:
        """Obtiene historial de conversación"""
        if not self._initialized:
            await self.initialize()
        return await self._backend.get_history(session_id)

    async def add_message(
        self,
        session_id: str,
        content: str,
        message_type: str = "human"
    ) -> None:
        """
        Añade mensaje al historial

        Args:
            session_id: ID de sesión (teléfono, conversation_id)
            content: Contenido del mensaje
            message_type: "human" o "ai"
        """
        if message_type == "human":
            message = HumanMessage(content=content)
        elif message_type == "ai":
            message = AIMessage(content=content)
        else:
            raise ValueError(f"Tipo de mensaje inválido: {message_type}")

        await self._backend.add_message(session_id, message)

    async def clear_session(self, session_id: str) -> None:
        """Limpia historial de sesión"""
        if not self._initialized:
            await self.initialize()
        await self._backend.clear_session(session_id)

    async def cleanup_expired_sessions(self) -> int:
        """Limpia sesiones expiradas según configuración"""
        if not self._initialized:
            await self.initialize()
        return await self._backend.cleanup_expired(
            expiry_hours=self.settings.SESSION_EXPIRY_HOURS
        )

    def to_langchain_history(self, session_id: str) -> List[BaseMessage]:
        """
        Convierte historial a formato compatible con LangChain
        Método síncrono para uso directo en agentes
        """
        import asyncio
        return asyncio.run(self.get_history(session_id))
