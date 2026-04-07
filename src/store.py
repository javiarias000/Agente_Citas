"""
Store — persistencia asíncrona para el grafo LangGraph.

FIXES APLICADOS:
- [CRÍTICO] InMemoryStore.get_history retornaba [-1] en vez de [] → corregido
- [CRÍTICO] PostgresStore.get_agent_state usaba scalar_one=True pero luego
  accedía a row._mapping → scalar_one retorna el valor directo, no un Row.
  Corregido: ahora usa fetch=True y toma la primera fila.
- [CRÍTICO] PostgresStore.get_user_profile pasaba scalar_one=True y fetch=True
  juntos → scalar_one tenía prioridad y perdía todos los campos.
  Corregido: solo fetch=True.
- [MEDIO]  Prefijo "deyy_" centralizado en _session_key() para evitar
  inconsistencias si se llama con o sin prefijo.
- [MEDIO]  node_save_state ahora usa filter_persistent_state() de src/state.py
  para no persistir campos transitorios entre sesiones.
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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

logger = structlog.get_logger("langgraph.store")

TIMEZONE = ZoneInfo("America/Guayaquil")


# ═══════════════════════════════════════════════════════════
# BASE STORE (contrato)
# ═══════════════════════════════════════════════════════════


class BaseStore(ABC):
    """Contrato mínimo que todo store debe cumplir."""

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]: ...

    @abstractmethod
    async def add_message(
        self, phone: str, message: BaseMessage, project_id: Optional[uuid.UUID] = None
    ) -> None: ...

    @abstractmethod
    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None: ...

    @abstractmethod
    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def upsert_user_profile(
        self, phone: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]: ...


# ═══════════════════════════════════════════════════════════
# IN-MEMORY STORE (tests / dev)
# ═══════════════════════════════════════════════════════════


class InMemoryStore(BaseStore):
    """Store volátil para tests. Thread-safe con locks."""

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
        # FIX: retornaba [-1] (entero) en vez de [] (lista vacía)
        return list(self._history.get(phone, []))[-limit:]

    async def add_message(
        self, phone: str, message: BaseMessage, project_id: Optional[uuid.UUID] = None
    ) -> None:
        self._history.setdefault(phone, []).append(message)

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        return self._agent_states.get(phone)

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        self._agent_states[phone] = dict(state)

    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        return self._user_profiles.get(phone)

    async def upsert_user_profile(
        self, phone: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
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
    - langchain_memory  — historial de mensajes
    - agent_states      — estado persistente del grafo
    - user_profiles     — perfil de usuario
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        async with self._lock:
            if not self._initialized:
                self._initialized = True
                logger.info("PostgresStore inicializado")

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _session_key(phone: str) -> str:
        """
        FIX: centraliza el prefijo 'deyy_' en un solo lugar.
        Antes estaba duplicado en cada método, lo que causaba
        inconsistencias si se llamaba con el prefijo ya incluido.
        """
        if phone.startswith("deyy_"):
            return phone
        return f"deyy_{phone}"

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
        content = data.get("content", "")
        kwargs = data.get("additional_kwargs", {})
        if _type == "ai":
            return AIMessage(content=content, additional_kwargs=kwargs)
        return HumanMessage(content=content, additional_kwargs=kwargs)

    async def _execute(self, stmt, params=None, *, fetch=False, scalar_one=False):
        """
        Ejecuta una query SQL.

        Args:
            stmt: SQLAlchemy text() statement
            params: dict de parámetros
            fetch: si True, retorna fetchall() → List[Row]
            scalar_one: si True, retorna scalar_one_or_none() → valor directo

        NOTA: scalar_one y fetch son mutuamente excluyentes.
        scalar_one tiene prioridad si ambos son True.
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt, params or {})
            if scalar_one:
                return result.scalar_one_or_none()
            if fetch:
                return result.fetchall()
            return None

    # ── Historial ─────────────────────────────────────────

    async def get_history(self, phone: str, limit: int = 50) -> List[BaseMessage]:
        from sqlalchemy import text

        sid = self._session_key(phone)
        sql = text(
            "SELECT type, content, additional_kwargs "
            "FROM langchain_memory "
            "WHERE session_id = :sid "
            "ORDER BY created_at DESC LIMIT :lim"
        )
        rows = await self._execute(sql, {"sid": sid, "lim": limit}, fetch=True)
        if not rows:
            return []

        messages = []
        for row in reversed(rows):  # cronológico (más antiguo primero)
            data = dict(row._mapping)
            ak = data.get("additional_kwargs")
            if isinstance(ak, str):
                try:
                    data["additional_kwargs"] = json.loads(ak)
                except json.JSONDecodeError:
                    data["additional_kwargs"] = {}
            elif ak is None:
                data["additional_kwargs"] = {}
            messages.append(self._dict_to_msg(data))
        return messages

    async def add_message(
        self,
        phone: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        from sqlalchemy import text

        sid = self._session_key(phone)
        d = self._msg_to_dict(message)
        additional_kwargs_json = json.dumps(d["additional_kwargs"], default=str)

        sql = text(
            "INSERT INTO langchain_memory "
            "(session_id, project_id, type, content, additional_kwargs, created_at) "
            "VALUES (:sid, :pid, :type, :content, :ak, now())"
        )
        await self._execute(
            sql,
            {
                "sid": sid,
                "pid": project_id,
                "type": d["type"],
                "content": d["content"],
                "ak": additional_kwargs_json,
            },
            fetch=False,
        )

    # ── Estado del agente ─────────────────────────────────

    async def get_agent_state(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        FIX: antes usaba scalar_one=True y luego accedía a row._mapping,
        pero scalar_one retorna el valor directo (el JSON), no un Row.
        Ahora usa fetch=True y toma la primera fila correctamente.
        """
        from sqlalchemy import text

        sid = self._session_key(phone)
        sql = text(
            "SELECT state FROM agent_states "
            "WHERE session_id = :sid "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        rows = await self._execute(sql, {"sid": sid}, fetch=True)
        if not rows:
            return None

        raw = rows[0]._mapping.get("state")
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("agent_state no es JSON válido", sid=sid)
                return None
        if isinstance(raw, dict):
            return raw
        return None

    async def save_agent_state(self, phone: str, state: Dict[str, Any]) -> None:
        """
        Guarda el estado persistente.

        FIX: usa filter_persistent_state() para excluir campos transitorios
        (fechas, current_step, _extract_data_calls, etc.) que no deben
        restaurarse en sesiones futuras.
        """
        from sqlalchemy import text

        from src.state import filter_persistent_state

        sid = self._session_key(phone)
        persistent = filter_persistent_state(state)

        sql = text(
            "INSERT INTO agent_states (session_id, state, updated_at) "
            "VALUES (:sid, :data, now()) "
            "ON CONFLICT (session_id) DO UPDATE "
            "SET state = :data, updated_at = now()"
        )
        await self._execute(
            sql,
            {
                "sid": sid,
                "data": json.dumps(persistent, default=str),
            },
            fetch=False,
        )

        logger.debug("Estado guardado", sid=sid, fields=list(persistent.keys()))

    # ── Perfil de usuario ─────────────────────────────────

    async def get_user_profile(self, phone: str) -> Optional[Dict[str, Any]]:
        """
        FIX: antes pasaba scalar_one=True y fetch=True juntos.
        scalar_one tenía prioridad y solo retornaba la primera columna,
        perdiendo todos los demás campos del perfil.
        Ahora usa solo fetch=True y toma la primera fila completa.
        """
        from sqlalchemy import text

        sql = text("SELECT * FROM user_profiles WHERE phone_number = :p LIMIT 1")
        rows = await self._execute(sql, {"p": phone}, fetch=True)
        if not rows:
            return None

        d = dict(rows[0]._mapping)
        return self._normalize_profile_dict(d)

    async def upsert_user_profile(
        self, phone: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        from sqlalchemy import text

        if not updates:
            return await self.get_user_profile(phone) or {}

        cols = ["phone_number"] + list(updates.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in updates.keys())

        sql = text(
            f"INSERT INTO user_profiles ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (phone_number) DO UPDATE SET {update_set}, "
            f"last_seen = now() "
            f"RETURNING *"
        )
        params = {"phone_number": phone, **updates}
        rows = await self._execute(sql, params, fetch=True)
        if not rows:
            return {}
        return self._normalize_profile_dict(dict(rows[0]._mapping))

    @staticmethod
    def _normalize_profile_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        """Convierte UUIDs y datetimes a strings para serialización segura."""
        result = {}
        for k, v in d.items():
            if isinstance(v, uuid.UUID):
                result[k] = str(v)
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            else:
                result[k] = v
        return result
