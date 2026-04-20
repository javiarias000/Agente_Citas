"""
Redis cache para slots de disponibilidad en Google Calendar.

Caching strategy:
  - Key: slots:{calendar_id}:{YYYY-MM-DD}:{duration_minutes}
  - TTL: 1800s (30 min)
  - Invalidation: cuando se crea, cancela o reagenda un evento
"""

import json
from datetime import datetime
from typing import List, Optional

import structlog

logger = structlog.get_logger("calendar.cache")

SLOTS_TTL = 1800  # 30 minutos


class CalendarCache:
    """Cache de slots usando redis.asyncio."""

    def __init__(self, redis_client):
        self._r = redis_client

    def _key(self, calendar_id: str, date: datetime, duration: int) -> str:
        """Genera la clave Redis para un conjunto de slots."""
        return f"slots:{calendar_id}:{date.strftime('%Y-%m-%d')}:{duration}"

    async def get_slots(
        self,
        calendar_id: str,
        date: datetime,
        duration: int,
    ) -> Optional[List[str]]:
        """Obtiene slots cacheados. Retorna None si no hay cache o Redis no está disponible."""
        if not self._r:
            return None

        try:
            raw = await self._r.get(self._key(calendar_id, date, duration))
            if raw:
                logger.info(
                    "cache hit",
                    calendar_id=calendar_id,
                    date=date.date(),
                    duration=duration,
                )
                return json.loads(raw)
        except Exception as e:
            logger.warning("cache get error", error=str(e))

        return None

    async def set_slots(
        self,
        calendar_id: str,
        date: datetime,
        duration: int,
        slots: List[str],
    ):
        """Almacena slots en cache con TTL."""
        if not self._r:
            return

        try:
            await self._r.setex(
                self._key(calendar_id, date, duration),
                SLOTS_TTL,
                json.dumps(slots),
            )
            logger.info(
                "cache set",
                calendar_id=calendar_id,
                date=date.date(),
                duration=duration,
                count=len(slots),
            )
        except Exception as e:
            logger.warning("cache set error", error=str(e))

    async def invalidate_day(self, calendar_id: str, date: datetime):
        """Borra todos los slots cacheados de un día (todos los durations)."""
        if not self._r:
            return

        try:
            pattern = f"slots:{calendar_id}:{date.strftime('%Y-%m-%d')}:*"
            count = 0
            keys_to_delete = []
            async for key in self._r.scan_iter(pattern):
                keys_to_delete.append(key)

            for key in keys_to_delete:
                await self._r.delete(key)
                count += 1

            logger.info(
                "cache invalidated",
                calendar_id=calendar_id,
                date=date.date(),
                keys_deleted=count,
            )
        except Exception as e:
            logger.warning("cache invalidate error", error=str(e))
