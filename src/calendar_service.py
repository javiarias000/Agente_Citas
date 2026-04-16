"""
Google Calendar adapter — async wrapper sobre el servicio existente.

Este módulo NO reemplaza el GoogleCalendarService existente.
Solo lo envuelve para la interfaz que los nodos del grafo necesitan.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from zoneinfo import ZoneInfo

logger = structlog.get_logger("langgraph.calendar")
TIMEZONE = ZoneInfo("America/Guayaquil")
BUSINESS_START = 9
BUSINESS_END = 18


class GoogleCalendarService:
    """Adapter async que envuelve el servicio Google Calendar existente."""

    def __init__(self, calendar_service, db_service=None):
        """
        Args:
            calendar_service: instancia del GoogleCalendarService existente
                              (de services/google_calendar_service.py)
            db_service: AppointmentService opcional para verificar DB local también
        """
        self._svc = calendar_service
        self._db = db_service

    async def get_available_slots(
        self,
        date: datetime,
        duration_minutes: int = 60,
    ) -> List[str]:
        """Retorna lista de ISO strings de slots disponibles para esa fecha."""
        try:
            slots = await self._svc.get_available_slots(
                date=date,
                duration_minutes=duration_minutes,
                start_hour=BUSINESS_START,
                end_hour=BUSINESS_END,
            )
            # Convertir a lista de ISO strings
            iso_slots = []
            for slot in slots:
                start = slot.get("start", slot) if isinstance(slot, dict) else slot
                if isinstance(start, datetime):
                    iso_slots.append(start.isoformat())
                elif isinstance(start, str):
                    iso_slots.append(start)
            print(f"[calendar] slots raw={len(slots)} iso={len(iso_slots)} date={date.date()}")
            logger.info("Slots disponibles", date=date.date().isoformat(), count=len(iso_slots))
            return iso_slots
        except Exception as e:
            import traceback
            print(f"[calendar] ERROR get_available_slots: {e}\n{traceback.format_exc()}")
            logger.error("Error obteniendo slots", error=str(e))
            return []

    async def search_events_by_query(
        self,
        q: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Busca eventos en Calendar usando texto libre (q=).

        Args:
            q: Texto a buscar (nombre paciente, teléfono, etc.)
            start_date: Inicio del rango (default: ahora)
            end_date: Fin del rango (default: 60 días desde ahora)
            max_results: Máximo eventos

        Returns:
            Lista de eventos que coincidan
        """
        now = datetime.now(TIMEZONE)
        start = start_date or now
        end = end_date or (now + timedelta(days=60))
        try:
            return await self._svc.search_events_by_query(
                q=q,
                start_date=start,
                end_date=end,
                max_results=max_results,
            )
        except Exception as e:
            logger.error("Error en search_events_by_query", q=q, error=str(e))
            return []

    async def create_event(
        self,
        start: datetime,
        end: datetime,
        title: str,
        description: str = "",
    ) -> tuple[str, str]:
        """
        Crea evento en Google Calendar.

        Returns:
            (event_id, html_link)
        """
        event = await self._svc.create_event(
            title=title,
            start_time=start,
            end_time=end,
            description=description,
        )
        event_id = event.get("id", "")
        html_link = event.get("htmlLink", "")
        logger.info("Evento creado", event_id=event_id, link=html_link)
        return event_id, html_link

    async def delete_event(self, event_id: str) -> bool:
        success = await self._svc.delete_event(event_id)
        logger.info("Evento eliminado", event_id=event_id, success=success)
        return bool(success)

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        event = await self._svc.update_event(
            event_id=event_id,
            title=title,
            start_time=start,
            end_time=end,
        )
        logger.info("Evento actualizado", event_id=event_id)
        return event


def weekend_adjust(dt: datetime) -> tuple[datetime, bool]:
    """Si cae en fin de semana, avanza al lunes. Retorna (fecha, ajustado)."""
    if dt.weekday() >= 5:
        days = 7 - dt.weekday()  # sábado→2, domingo→1
        return dt + timedelta(days=days), True
    return dt, False
