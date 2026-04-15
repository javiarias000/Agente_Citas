#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abstracción Store-like para LangGraph.

Este módulo proporciona una interfaz compatible con futuras versiones de
langgraph.store, implementada sobre MemoryManager actual.

NOTA: langgraph-store no está disponible como paquete oficial (experimental).
Esta implementación sirve como capa de adaptación para facilitar la migración
futura cuando el paquete esté disponible.
"""

from typing import Any, Dict, List, Optional, Tuple, Protocol, runtime_checkable
from datetime import datetime
from abc import ABC, abstractmethod
import uuid

from langchain_core.messages import BaseMessage


# ============================================
# TIPOS
# ============================================

Namespace = Tuple[str, ...]  # Ej: ("history", "session_123")
Key = str  # Clave dentro del namespace
Value = Any  # Valor serializado (generalmente JSON-serializable)


@runtime_checkable
class StoreProtocol(Protocol):
    """
    Protocolo que define la interfaz de un Store de LangGraph.

    Esto imita la interfaz esperada de langgraph.store.Store cuando esté disponible.
    Ver: https://langchain-ai.github.io/langgraph/concepts/#store
    """

    async def get(self, namespace: Namespace, key: Key) -> Optional[Value]:
        """Obtiene un valor del store"""
        ...

    async def put(self, namespace: Namespace, key: Key, value: Value) -> None:
        """Guarda un valor en el store"""
        ...

    async def delete(self, namespace: Namespace, key: Key) -> None:
        """Elimina un valor del store"""
        ...

    async def list_keys(self, namespace: Namespace, suffix: Optional[str] = None) -> List[Key]:
        """Lista las claves en un namespace"""
        ...

    async def search(
        self,
        namespace: Namespace,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Busca valores en el store (para índices/cargas parciales)"""
        ...


# ============================================
# IMPLEMENTACIÓN CONCRETA: ArcadiumStore
# ============================================

class ArcadiumStore:
    """
    Store implementado sobre MemoryManager.

    Namespaces utilizados:
    - ("history", session_id) → List[BaseMessage] (conversación)
    - ("user_profile", phone_number) → UserProfile dict
    - ("agent_state", session_id) → SupportState dict
    - ("conversation", phone_number) → Conversation metadata dict
    """

    def __init__(self, memory_manager):
        """
        Inicializa el store.

        Args:
            memory_manager: Instancia de MemoryManager
        """
        self.memory_manager = memory_manager
        self._cache: Dict[Namespace, Dict[Key, Tuple[Value, datetime]]] = {}
        self._logger = None

    def _get_logger(self):
        """Lazy logger import"""
        if self._logger is None:
            import structlog
            self._logger = structlog.get_logger("store.arcadium")
        return self._logger

    # ========================================
    # HISTORY NAMESPACE
    # ========================================

    async def get_history(self, session_id: str, limit: Optional[int] = None) -> List[BaseMessage]:
        """
        Obtiene historial de mensajes para una sesión.

        Args:
            session_id: ID de sesión (teléfono o UUID)
            limit: Número máximo de mensajes a devolver (los más recientes). Si None, sin límite.

        Returns:
            Lista de mensajes de LangChain en orden cronológico (copia para evitar mutaciones)
        """
        # Usar namespace formal
        namespace = ("history", session_id)
        cache_key = ("messages", "full")  # key dummy para cache

        # Check cache first (solo si no hay límite, ya que el límite cambia el resultado)
        if limit is None and namespace in self._cache and cache_key in self._cache[namespace]:
            value, cached_at = self._cache[namespace][cache_key]
            # Cache TTL: 5 minutos
            from datetime import timedelta
            if datetime.utcnow() - cached_at < timedelta(minutes=5):
                self._get_logger().debug("History cache hit", session_id=session_id)
                # Return a copy to prevent mutation of cached list
                return list(value)

        # Load from storage
        history = await self.memory_manager.get_history(session_id, limit=limit)
        # Make a copy to prevent mutation of the internal storage list
        history_copy = list(history)

        # Cache result (store the copy) - solo cachear si no hay límite
        if limit is None:
            if namespace not in self._cache:
                self._cache[namespace] = {}
            from datetime import timedelta
            self._cache[namespace][cache_key] = (history_copy, datetime.utcnow())

        self._get_logger().debug(
            "History loaded from storage",
            session_id=session_id,
            count=len(history_copy)
        )
        return history_copy

    async def add_message(self, session_id: str, message: BaseMessage, project_id: Optional[uuid.UUID] = None) -> None:
        """
        Añade un mensaje al historial.

        Args:
            session_id: ID de sesión
            message: Mensaje de LangChain
            project_id: ID del proyecto (opcional)
        """
        await self.memory_manager.add_message(
            session_id=session_id,
            message=message,
            project_id=project_id,
        )

        # Invalidate cache
        namespace = ("history", session_id)
        if namespace in self._cache:
            self._cache[namespace].pop(("messages", "full"), None)

    async def clear_history(self, session_id: str) -> None:
        """
        Limpia historial de una sesión.

        Args:
            session_id: ID de sesión
        """
        await self.memory_manager.clear_session(session_id)

        # Invalidate cache
        namespace = ("history", session_id)
        if namespace in self._cache:
            del self._cache[namespace]

    # ========================================
    # USER PROFILE NAMESPACE
    # ========================================

    async def get_user_profile(self, phone_number: str, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """
        Obtiene el perfil de un usuario.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto

        Returns:
            Dict con datos del perfil o None
        """
        namespace = ("user_profile", phone_number)
        cache_key = ("profile", str(project_id))

        # Check cache
        if namespace in self._cache and cache_key in self._cache[namespace]:
            value, cached_at = self._cache[namespace][cache_key]
            from datetime import timedelta
            if datetime.utcnow() - cached_at < timedelta(minutes=10):
                return value

        # Load from storage
        profile = await self.memory_manager.get_user_profile(phone_number, project_id)

        if profile:
            # Convertir modelo SQLAlchemy a dict
            profile_dict = {
                "id": str(profile.id),
                "phone_number": profile.phone_number,
                "project_id": str(profile.project_id),
                "total_conversations": profile.total_conversations,
                "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
                "preferences": profile.preferences or {},
                "notes": profile.notes or "",
                "extracted_facts": dict(profile.extracted_facts) if profile.extracted_facts else {},
                "created_at": profile.created_at.isoformat() if profile.created_at else None,
                "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            }

            # Cache
            if namespace not in self._cache:
                self._cache[namespace] = {}
            from datetime import timedelta
            self._cache[namespace][cache_key] = (profile_dict, datetime.utcnow())

            return profile_dict

        return None

    async def save_user_profile(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        **updates
    ) -> Dict[str, Any]:
        """
        Guarda o actualiza un perfil de usuario.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto
            **updates: Campos a actualizar

        Returns:
            Dict con el perfil guardado
        """
        profile = await self.memory_manager.create_or_update_profile(
            phone_number=phone_number,
            project_id=project_id,
            **updates
        )

        # Convertir a dict: manejar tanto objetos modelo (PostgreSQL) como dicts (InMemory)
        if isinstance(profile, dict):
            # Backend InMemory devuelve dict plano
            profile_dict = {
                "id": profile.get("id", f"prof_{phone_number}_{project_id}"),
                "phone_number": phone_number,
                "project_id": str(project_id),
                "total_conversations": profile.get("total_conversations", 0),
                "last_seen": profile.get("last_seen"),
                "preferences": profile.get("preferences", {}),
                "notes": profile.get("notes", ""),
                "extracted_facts": profile.get("extracted_facts", {}),
                "created_at": profile.get("created_at"),
                "updated_at": profile.get("updated_at"),
            }
        else:
            # Backend PostgreSQL devuelve objeto modelo
            profile_dict = {
                "id": str(profile.id),
                "phone_number": profile.phone_number,
                "project_id": str(profile.project_id),
                "total_conversations": profile.total_conversations,
                "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
                "preferences": profile.preferences or {},
                "notes": profile.notes or "",
                "extracted_facts": dict(profile.extracted_facts) if profile.extracted_facts else {},
                "created_at": profile.created_at.isoformat() if profile.created_at else None,
                "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            }

        # Update cache
        namespace = ("user_profile", phone_number)
        if namespace not in self._cache:
            self._cache[namespace] = {}
        from datetime import timedelta
        self._cache[namespace][("profile", str(project_id))] = (profile_dict, datetime.utcnow())

        return profile_dict

    # ========================================
    # AGENT STATE NAMESPACE
    # ========================================

    async def get_agent_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """
        Obtiene el estado de la state machine.

        Args:
            session_id: ID de sesión
            project_id: ID del proyecto (opcional)

        Returns:
            Dict con el estado o None
        """
        namespace = ("agent_state", session_id)
        cache_key = ("state", str(project_id) if project_id else "global")

        # Check cache
        if namespace in self._cache and cache_key in self._cache[namespace]:
            value, cached_at = self._cache[namespace][cache_key]
            from datetime import timedelta
            if datetime.utcnow() - cached_at < timedelta(seconds=30):  # State cache es más corto
                self._get_logger().debug("Agent state cache hit", session_id=session_id)
                return value

        # Load from storage
        state = await self.memory_manager.get_state(session_id, project_id=project_id)

        # Cache
        if state:
            if namespace not in self._cache:
                self._cache[namespace] = {}
            from datetime import timedelta
            self._cache[namespace][cache_key] = (state, datetime.utcnow())

        return state

    async def save_agent_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None
    ) -> None:
        """
        Guarda el estado de la state machine.

        Args:
            session_id: ID de sesión
            state: Estado completo
            project_id: ID del proyecto (opcional)
        """
        await self.memory_manager.save_state(session_id, state, project_id=project_id)

        # Update cache
        namespace = ("agent_state", session_id)
        if namespace not in self._cache:
            self._cache[namespace] = {}
        from datetime import timedelta
        self._cache[namespace][("state", str(project_id) if project_id else "global")] = (state, datetime.utcnow())

    # ========================================
    # CONVERSATION METADATA NAMESPACE
    # ========================================

    async def get_conversation_metadata(self, phone_number: str, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """
        Obtiene metadatos de la conversación.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto

        Returns:
            Dict con metadatos o None
        """
        # Implementación deferred - por ahora retorna None
        # En Fase 3 se integrará con Conversation model
        return None

    async def save_conversation_metadata(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        **metadata
    ) -> None:
        """
        Guarda metadatos de conversación.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto
            **metadata: Campos de metadatos
        """
        # Implementación deferred - por ahora no-op
        pass

    # ========================================
    # MÉTODOS ADICIONALES (No en Protocolo Store)
    # ========================================

    def clear_cache(self) -> None:
        """Limpia el cache local"""
        self._cache.clear()
        self._get_logger().debug("Store cache cleared")

    def get_cache_stats(self) -> Dict[str, int]:
        """
        Obtiene estadísticas del cache.

        Returns:
            Dict con número de entradas por namespace
        """
        stats = {}
        for namespace, entries in self._cache.items():
            stats[str(namespace)] = len(entries)
        return stats

    # ========================================
    # DELEGACIÓN: Métodos no implementados -> memory_manager
    # ========================================

    def __getattr__(self, name: str) -> Any:
        """
        Delega cualquier método no definido en ArcadiumStore al memory_manager subyacente.

        Esto permite que DeyyAgent use store como si fuera MemoryManager, con compatibilidad total.
        """
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self.memory_manager, name)


# ============================================
# FUNCIONES DE UTILIDAD
# ============================================

def make_history_namespace(session_id: str) -> Namespace:
    """Crea namespace para historial"""
    return ("history", session_id)


def make_user_profile_namespace(phone_number: str) -> Namespace:
    """Crea namespace para perfil de usuario"""
    return ("user_profile", phone_number)


def make_agent_state_namespace(session_id: str) -> Namespace:
    """Crea namespace para estado de agente"""
    return ("agent_state", session_id)


def make_conversation_namespace(phone_number: str) -> Namespace:
    """Crea namespace para metadatos de conversación"""
    return ("conversation", phone_number)
