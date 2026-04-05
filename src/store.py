"""
Store — persistencia asíncrona para el grafo LangGraph.

Este módulo reemplaza la dependencia del grafo en ArcadiumStore/MemoryManager.

Interfaces:
- BaseStore:  abstracta, define los 6 contratos que el grafo necesita.
- PostgresStore: implementación real sobre SQLAlchemy async.
- InMemoryStore: para tests y desarrollo rápido.

Todos los métodos retornan dict puros, nunca ORM models.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

logger = structlog.get_logger("langgraph.store")

TIMEZONE = ZoneInfo("America/Guayaquil")


# ═══════════════════════════════════════════════════════════
# BASE STORE (contrato)
# ═══════════════════════════════════════════════════════════

class BaseStore(ABC):
    """Contrato mínimo que todo store debe cumplir."""

    @abstractmethod
    async def initialize(self) -> None:
        ...

    @abstractmethod
    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]:
        ...

    @abstractmethod
    async def add_message(self, phone: str, message: BaseMessage) -> None:
        ...

    @abstractmethod
    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    async def save_agent_state(
        self, phone: str, state: Dict[str, Any]
    ) -> None:
        ...

    @abstractmethod
    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    async def upsert_user_profile(self, phone: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        ...


# ═══════════════════════════════════════════════════════════
# IN-MEMORY STORE (tests / dev)
# ═══════════════════════════════════════════════════════════

class InMemoryStore(BaseStore):
    """Store volátil para tests.  Thread-safe con locks."""

    def __init__(self) -> None:
        self._history: Dict[str, List[BaseMessage]] = {}
        self._agent_states: Dict[str, Dict[str, Any]] = {}
        self._user_profiles: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            self._history.clear()
            self._agent_states.clear()
            self._user_profiles.clear()

    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]:
        return self._history.get(phone, [-1])[-limit:]

    async def add_message(self, phone: str, message: BaseMessage) -> None:
        self._history.setdefault(phone, []).append(message)

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        return self._agent_states.get(phone)

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        self._agent_states[phone] = dict(state)

    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        return self._user_profiles.get(phone)

    async def upsert_user_profile(self, phone: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        profile = self._user_profiles.setdefault(phone, {})
        profile.update(updates)
        profile.setdefault("phone", phone)
        profile.setdefault("last_seen", datetime.now(TIMEZONE).isoformat())
        return dict(profile)


# ═══════════════════════════════════════════════════════════
# POSTGRES STORE (producción)
# ═══════════════════════════════════════════════════════════

class PostgresStore(BaseStore):
    """Store sobre PostgreSQL con SQLAlchemy async.

    Reutiliza las tablas existentes:
    - langchain_memory  — historial
    - agent_states      — estado del grafo
    - user_profiles     — perfil usuario
    """

    def __init__(self, engine):
        self._engine = engine
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        async with self._lock:
            if not self._initialized:
                logger.info("PostgresStore inicializado")
                self._initialized = True

    @staticmethod
    def _msg_to_dict(msg: BaseMessage) -> Dict[str, Any]:
        return {
            "type": msg.type,
            "content": msg.content,
            "additional_kwargs": msg.additional_kwargs,
        }

    @staticmethod
    def _dict_to_msg(data: Dict[str, Any]) -> BaseMessage:
        _type = data.get("type", "human")
        if _type == "ai":
            return AIMessage(**data)
        return HumanMessage(**data)

    async def _execute(self, stmt, *params, scalar_one=False):
        from sqlalchemy import text
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt, *params)
            if scalar_one:
                return result.scalar_one_or_none()
            return result.fetchall()

    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]:
        from sqlalchemy import text
        sql = text(
            "SELECT type, content, additional_kwargs "
            "FROM langchain_memory "
            "WHERE session_id = :sid "
            "ORDER BY created_at DESC LIMIT :lim"
        )
        rows = await self._execute(sql, {"sid": f"deyy_{phone}", "lim": limit})
        messages = []
        for row in reversed(rows):
            data = dict(row._mapping)
            # additional_kwargs viene como JSONB o string
            if isinstance(data.get("additional_kwargs"), str):
                data["additional_kwargs"] = json.loads(data["additional_kwargs"])
            messages.append(self._dict_to_msg(data))
        return messages

    async def add_message(self, phone: str, message: BaseMessage) -> None:
        from sqlalchemy import text
        d = self._msg_to_dict(message)
        # additional_kwargs debe ser serializable
        d["additional_kwargs"] = json.dumps(d["additional_kwargs"], default=str)
        sql = text(
            "INSERT INTO langchain_memory "
            "(session_id, type, content, additional_kwargs, created_at) "
            "VALUES (:sid, :type, :content, :ak::jsonb, now())"
        )
        await self._execute(sql, {
            "sid": f"deyy_{phone}",
            "type": d["type"],
            "content": d["content"],
            "ak": d["additional_kwargs"],
        })

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text
        sql = text(
            "SELECT state_data FROM agent_states "
            "WHERE session_id = :sid "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = await self._execute(sql, {"sid": f"deyy_{phone}"}, scalar_one=True)
        if row is None:
            return None
        data = dict(row._mapping)
        state = data["state_data"]
        if isinstance(state, str):
            state = json.loads(state)
        return state

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        from sqlalchemy import text
        sql = text(
            "INSERT INTO agent_states (session_id, state_data, updated_at) "
            "VALUES (:sid, :data::jsonb, now()) "
            "ON CONFLICT (session_id) DO UPDATE "
            "SET state_data = :data::jsonb, updated_at = now()"
        )
        await self._execute(sql, {
            "sid": f"deyy_{phone}",
            "data": json.dumps(state, default=str),
        })

    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text
        sql = text("SELECT * FROM user_profiles WHERE phone_number = :p")
        row = await self._execute(sql, {"p": phone}, scalar_one=True)
        if row is None:
            return None
        d = dict(row._mapping)
        # Convertir tipos complejos
        for k, v in d.items():
            if isinstance(v, uuid.UUID):
                d[k] = str(v)
            elif isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    async def upsert_user_profile(self, phone: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        from sqlalchemy import text
        # Construir UPSERT dinámico
        cols = ["phone_number"] + list(updates.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in updates.keys())
        sql = text(
            f"INSERT INTO user_profiles ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (phone_number) DO UPDATE SET {update_set} "
            f"RETURNING *"
        )
        params = {"phone_number": phone, **updates}
        row = await self._execute(sql, params, scalar_one=True)
        d = dict(row._mapping) if row else {}
        for k, v in d.items():
            if isinstance(v, uuid.UUID):
                d[k] = str(v)
            elif isinstance(v, datetime):
                d[k] = v.isoformat()
        return d
