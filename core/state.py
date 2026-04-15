# -*- coding: utf-8 -*-
"""
Gestión de estado y persistencia
Mantiene estado de conversaciones y procesamiento
"""

import json
import pickle
from typing import Any, Callable, Dict, Optional, List
from datetime import datetime, timedelta
from pathlib import Path
import asyncio
from core.exceptions import StateError


class StateStorage:
    """Interfaz base para almacenamiento de estado"""

    async def save(self, key: str, value: Any) -> bool:
        raise NotImplementedError

    async def load(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    async def keys(self, pattern: str = "*") -> List[str]:
        raise NotImplementedError


class MemoryStorage(StateStorage):
    """Almacenamiento en memoria (volátil)"""

    def __init__(self, ttl_seconds: int = 3600):
        self._store: Dict[str, tuple] = {}  # (value, expires_at)
        self.ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def save(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        async with self._lock:
            expires_at = None
            if ttl:
                expires_at = datetime.utcnow() + timedelta(seconds=ttl)
            elif self.ttl:
                expires_at = datetime.utcnow() + timedelta(seconds=self.ttl)
            self._store[key] = (value, expires_at)
            return True

    async def load(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key not in self._store:
                return None
            value, expires_at = self._store[key]
            if expires_at and datetime.utcnow() > expires_at:
                del self._store[key]
                return None
            return value

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    async def exists(self, key: str) -> bool:
        async with self._lock:
            if key not in self._store:
                return False
            _, expires_at = self._store[key]
            if expires_at and datetime.utcnow() > expires_at:
                del self._store[key]
                return False
            return True

    async def keys(self, pattern: str = "*") -> List[str]:
        async with self._lock:
            import fnmatch
            keys = list(self._store.keys())
            if pattern == "*":
                return keys
            return [k for k in keys if fnmatch.fnmatch(k, pattern)]


class RedisStorage(StateStorage):
    """Almacenamiento en Redis"""

    def __init__(self, url: str, password: Optional[str] = None, db: int = 0):
        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(url, password=password, db=db, decode_responses=False)
        except ImportError:
            raise StateError("Redis no instalado. pip install redis")
        self._lock = asyncio.Lock()

    async def save(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        async with self._lock:
            try:
                serialized = pickle.dumps(value)
                await self._redis.set(key, serialized, ex=ttl)
                return True
            except Exception as e:
                raise StateError(f"Error guardando en Redis: {e}")

    async def load(self, key: str) -> Optional[Any]:
        async with self._lock:
            try:
                data = await self._redis.get(key)
                if data is None:
                    return None
                return pickle.loads(data)
            except Exception as e:
                raise StateError(f"Error cargando de Redis: {e}")

    async def delete(self, key: str) -> bool:
        async with self._lock:
            try:
                result = await self._redis.delete(key)
                return result > 0
            except Exception as e:
                raise StateError(f"Error eliminando de Redis: {e}")

    async def exists(self, key: str) -> bool:
        async with self._lock:
            try:
                result = await self._redis.exists(key)
                return result > 0
            except Exception as e:
                raise StateError(f"Error verificando en Redis: {e}")

    async def keys(self, pattern: str = "*") -> List[str]:
        async with self._lock:
            try:
                keys = await self._redis.keys(pattern)
                return [k.decode() if isinstance(k, bytes) else k for k in keys]
            except Exception as e:
                raise StateError(f"Error listando claves Redis: {e}")


class SQLiteStorage(StateStorage):
    """Almacenamiento en SQLite"""

    def __init__(self, db_path: str = "/home/jav/arcadium_automation/data/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Inicializa base de datos"""
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON state(expires_at)")
            conn.commit()

    async def save(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        import sqlite3
        expires_at = None
        if ttl:
            expires_at = datetime.utcnow() + timedelta(seconds=ttl)

        serialized = pickle.dumps(value)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO state (key, value, expires_at)
                    VALUES (?, ?, ?)
                """, (key, serialized, expires_at))
                conn.commit()
            return True
        except Exception as e:
            raise StateError(f"Error guardando en SQLite: {e}")

    async def load(self, key: str) -> Optional[Any]:
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT value, expires_at FROM state
                    WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)
                """, (key, datetime.utcnow()))
                row = cursor.fetchone()
                if row:
                    return pickle.loads(row[0])
                return None
        except Exception as e:
            raise StateError(f"Error cargando de SQLite: {e}")

    async def delete(self, key: str) -> bool:
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM state WHERE key = ?", (key,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            raise StateError(f"Error eliminando de SQLite: {e}")

    async def exists(self, key: str) -> bool:
        return await self.load(key) is not None

    async def keys(self, pattern: str = "*") -> List[str]:
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT key FROM state WHERE key GLOB ?", (pattern,))
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            raise StateError(f"Error listando claves SQLite: {e}")


class StateManager:
    """Gestor centralizado de estado"""

    def __init__(self, storage: StateStorage):
        self.storage = storage
        self._local_cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}

    async def set(self, key: str, value: Any, ttl: Optional[int] = None, cache: bool = True) -> bool:
        """Guarda estado"""
        success = await self.storage.save(key, value, ttl=ttl)
        if success and cache:
            self._local_cache[key] = value
            if ttl:
                self._cache_ttl[key] = datetime.utcnow() + timedelta(seconds=ttl)
        return success

    async def get(self, key: str, default: Any = None, use_cache: bool = True) -> Any:
        """Obtiene estado"""
        # Verificar caché local
        if use_cache and key in self._local_cache:
            if key in self._cache_ttl:
                if datetime.utcnow() > self._cache_ttl[key]:
                    self._local_cache.pop(key, None)
                    self._cache_ttl.pop(key, None)
                else:
                    return self._local_cache[key]
            else:
                return self._local_cache[key]

        # Cargar desde storage
        value = await self.storage.load(key)
        if value is not None and use_cache:
            self._local_cache[key] = value
        return value if value is not None else default

    async def delete(self, key: str) -> bool:
        """Elimina estado"""
        self._local_cache.pop(key, None)
        self._cache_ttl.pop(key, None)
        return await self.storage.delete(key)

    async def exists(self, key: str) -> bool:
        return await self.storage.exists(key)

    async def clear_cache(self) -> None:
        """Limpia caché local"""
        self._local_cache.clear()
        self._cache_ttl.clear()

    async def keys(self, pattern: str = "*") -> List[str]:
        """Lista claves disponibles en el storage"""
        return await self.storage.keys(pattern)

    async def get_or_create(
        self,
        key: str,
        factory: Callable,
        ttl: Optional[int] = None,
        **factory_kwargs
    ) -> Any:
        """Obtiene o crea valor usando factory"""
        value = await self.get(key)
        if value is None:
            # Soporte para factories sincrónicas y asincrónicas
            if asyncio.iscoroutinefunction(factory):
                value = await factory(**factory_kwargs)
            else:
                value = factory(**factory_kwargs)
            await self.set(key, value, ttl=ttl)
        return value


# Claves comunes para Arcadium
class StateKeys:
    """Claves de estado estándar"""

    @staticmethod
    def conversation(phone: str) -> str:
        return f"conversation:{phone}"

    @staticmethod
    def processing(conversation_id: str) -> str:
        return f"processing:{conversation_id}"

    @staticmethod
    def transcription(phone: str) -> str:
        return f"transcription:{phone}"

    @staticmethod
    def last_webhook(phone: str) -> str:
        return f"last_webhook:{phone}"

    @staticmethod
    def metrics(chain_name: str) -> str:
        return f"metrics:{chain_name}"
