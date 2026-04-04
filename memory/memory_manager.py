#!/usr/bin/env python3
"""
Gestor de memoria modular para agentes
Soporta: InMemory (dev) y PostgreSQL (prod) - Tabla única escalable
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from abc import ABC, abstractmethod
import uuid
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

    @abstractmethod
    async def get_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """Obtiene el estado de state machine para una sesión"""
        pass

    @abstractmethod
    async def save_state(self, session_id: str, state: Dict[str, Any], project_id: Optional[uuid.UUID] = None) -> None:
        """Guarda el estado de state machine para una sesión"""
        pass


class InMemoryStorage(BaseMemory):
    """Almacenamiento en memoria (solo desarrollo)"""

    def __init__(self):
        self._sessions: Dict[str, List[BaseMessage]] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._states: Dict[str, Dict[str, Any]] = {}  # Para state machine
        # Perfiles: clave = (phone_number, project_id)
        self._profiles: Dict[tuple, Dict[str, Any]] = {}
        logger.info("InMemoryStorage inicializado")

    async def get_history(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> List[BaseMessage]:
        """Obtiene historial, creando vacío si no existe"""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._timestamps[session_id] = datetime.now(timezone.utc)
        return self._sessions[session_id]

    async def add_message(self, session_id: str, message: BaseMessage, project_id: Optional[uuid.UUID] = None) -> None:
        """Añade mensaje y actualiza timestamp"""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(message)
        self._timestamps[session_id] = datetime.now(timezone.utc)

    async def clear_session(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> None:
        """Elimina sesión"""
        self._sessions.pop(session_id, None)
        self._timestamps.pop(session_id, None)
        self._states.pop(session_id, None)

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
            self._states.pop(sid, None)
        logger.info("Sesiones limpiadas", expired_count=len(expired))
        return len(expired)

    # ============================================
    # State Machine Methods
    # ============================================

    async def get_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """Obtiene estado de state machine"""
        return self._states.get(session_id)

    async def save_state(self, session_id: str, state: Dict[str, Any], project_id: Optional[uuid.UUID] = None) -> None:
        """Guarda estado de state machine"""
        self._states[session_id] = state

    # ============================================
    # User Profile Methods
    # ============================================

    async def get_user_profile(self, phone_number: str, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Obtiene perfil de usuario"""
        key = (phone_number, project_id)
        return self._profiles.get(key)

    async def create_or_update_profile(self, phone_number: str, project_id: uuid.UUID, **updates) -> Dict[str, Any]:
        """Crea o actualiza perfil"""
        key = (phone_number, project_id)
        if key not in self._profiles:
            self._profiles[key] = {}
        self._profiles[key].update(updates)
        return self._profiles[key]

    async def increment_user_conversation_count(self, phone_number: str, project_id: uuid.UUID) -> None:
        """Incrementa contador de conversaciones"""
        key = (phone_number, project_id)
        if key not in self._profiles:
            self._profiles[key] = {"conversation_count": 0}
        self._profiles[key]["conversation_count"] = self._profiles[key].get("conversation_count", 0) + 1

    async def update_user_last_seen(self, phone_number: str, project_id: uuid.UUID) -> None:
        """Actualiza última vez visto"""
        from datetime import datetime, timezone
        key = (phone_number, project_id)
        if key not in self._profiles:
            self._profiles[key] = {}
        self._profiles[key]["last_seen"] = datetime.now(timezone.utc)


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

    async def get_history(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> List[BaseMessage]:
        """Obtiene historial desde PostgreSQL"""
        await self.initialize()
        return await self._backend.get_history(session_id, project_id=project_id)

    async def add_message(self, session_id: str, message: BaseMessage, project_id: Optional[uuid.UUID] = None) -> None:
        """Añade mensaje a PostgreSQL"""
        await self.initialize()
        await self._backend.add_message(session_id, message, project_id=project_id)

    async def clear_session(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> None:
        """Limpia sesión en PostgreSQL"""
        await self.initialize()
        await self._backend.clear_session(session_id, project_id=project_id)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """Limpia sesiones expiradas"""
        await self.initialize()
        return await self._backend.cleanup_expired(expiry_hours)

    # ============================================
    # State Machine Delegation
    # ============================================

    async def get_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """Obtiene estado desde backend"""
        await self.initialize()
        return await self._backend.get_state(session_id, project_id=project_id)

    async def save_state(self, session_id: str, state: Dict[str, Any], project_id: Optional[uuid.UUID] = None) -> None:
        """Guarda estado en backend"""
        await self.initialize()
        await self._backend.save_state(session_id, state, project_id=project_id)


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

    async def get_history(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> List[BaseMessage]:
        """Obtiene historial de conversación"""
        if not self._initialized:
            await self.initialize()
        return await self._backend.get_history(session_id, project_id)

    async def add_message(
        self,
        session_id: str,
        content: str,
        message_type: str = "human",
        project_id: Optional[uuid.UUID] = None
    ) -> None:
        """
        Añade mensaje al historial

        Args:
            session_id: ID de sesión (teléfono, conversation_id)
            content: Contenido del mensaje
            message_type: "human" o "ai"
            project_id: ID del proyecto (para multi-tenant)
        """
        if message_type == "human":
            message = HumanMessage(content=content)
        elif message_type == "ai":
            message = AIMessage(content=content)
        else:
            raise ValueError(f"Tipo de mensaje inválido: {message_type}")

        logger.info(
            "Agregando mensaje a memoria",
            session_id=session_id,
            project_id=str(project_id) if project_id else None,
            type=message_type,
            content_length=len(content)
        )
        await self._backend.add_message(session_id, message, project_id)

    async def clear_session(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> None:
        """Limpia historial de sesión"""
        if not self._initialized:
            await self.initialize()
        await self._backend.clear_session(session_id, project_id)

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

    # ============================================
    # USER PROFILES (delegado al backend si soporta)
    # ============================================

    async def get_user_profile(self, phone_number: str, project_id: uuid.UUID) -> Optional[Any]:
        """
        Obtiene el perfil de un usuario (si el backend lo soporta).

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto

        Returns:
            UserProfile o None
        """
        if not self._initialized:
            await self.initialize()
        if hasattr(self._backend, 'get_user_profile'):
            return await self._backend.get_user_profile(phone_number, project_id)
        return None

    async def create_or_update_profile(self, phone_number: str, project_id: uuid.UUID, **updates) -> Any:
        """
        Crea o actualiza perfil de usuario.
        """
        if not self._initialized:
            await self.initialize()
        if hasattr(self._backend, 'create_or_update_profile'):
            return await self._backend.create_or_update_profile(phone_number, project_id, **updates)
        raise NotImplementedError("Backend no soporta gestión de perfiles")

    async def increment_user_conversation_count(self, phone_number: str, project_id: uuid.UUID) -> None:
        """Incrementa contador de conversaciones del usuario."""
        if not self._initialized:
            await self.initialize()
        if hasattr(self._backend, 'increment_user_conversation_count'):
            await self._backend.increment_user_conversation_count(phone_number, project_id)
        # Si no soporta, ignorar silenciosamente

    async def update_user_last_seen(self, phone_number: str, project_id: uuid.UUID) -> None:
        """Actualiza last_seen del usuario."""
        if not self._initialized:
            await self.initialize()
        if hasattr(self._backend, 'update_user_last_seen'):
            await self._backend.update_user_last_seen(phone_number, project_id)

    async def extract_and_save_facts_from_conversation(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        user_message: str,
        agent_response: str
    ) -> None:
        """Extrae hechos de la conversación y los guarda en el perfil."""
        if not self._initialized:
            await self.initialize()
        if hasattr(self._backend, 'extract_and_save_facts_from_conversation'):
            await self._backend.extract_and_save_facts_from_conversation(
                phone_number, project_id, user_message, agent_response
            )

    # ============================================
    # STATE MACHINE (SupportState)
    # ============================================

    async def get_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """
        Obtiene el estado de SupportState desde el backend.

        Args:
            session_id: ID de sesión
            project_id: ID del proyecto (opcional, para multi-tenant)

        Returns:
            Dict con estado o None si no existe
        """
        if not self._initialized:
            await self.initialize()

        # InMemory no usa project_id, PostgreSQL sí
        if hasattr(self._backend, 'get_state'):
            return await self._backend.get_state(session_id, project_id=project_id)

        return None

    async def save_state(self, session_id: str, state: Dict[str, Any], project_id: Optional[uuid.UUID] = None) -> None:
        """
        Guarda el estado de SupportState en el backend.

        Args:
            session_id: ID de sesión
            state: Estado completo (dict)
            project_id: ID del proyecto (opcional)
        """
        if not self._initialized:
            await self.initialize()

        if hasattr(self._backend, 'save_state'):
            await self._backend.save_state(session_id, state, project_id=project_id)
        else:
            logger.warning("Backend no soporta save_state", backend=type(self._backend).__name__)
