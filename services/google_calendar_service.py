#!/usr/bin/env python3
"""
Google Calendar service usando langchain-google-community.

Autenticación via refresh token (GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET en .env). Las llamadas al API son síncronas corridas
en un executor para no bloquear el event loop de FastAPI.

Requiere:
    pip install "langchain-google-community[calendar]"
"""

import os
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import structlog
from dotenv import load_dotenv

load_dotenv()

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
from langchain_google_community.calendar.utils import build_calendar_service

from core.config import get_settings

logger = structlog.get_logger("google_calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _build_credentials() -> Credentials:
    """
    Construye credenciales OAuth2 desde las variables de entorno.

    Requiere GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET.
    El access token se obtiene automáticamente haciendo refresh.
    """
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        raise ValueError(
            "Faltan credenciales Google. Configura GOOGLE_REFRESH_TOKEN, "
            "GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en .env"
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


class GoogleCalendarService:
    """
    Servicio de Google Calendar construido sobre langchain-google-community.

    - Auth: refresh token desde variables de entorno (.env)
    - API resource: build_calendar_service de langchain_google_community
    - Async: todas las llamadas sync corren en run_in_executor (no bloquean)
    - Interface pública: idéntica al servicio anterior (nodos no cambian)
    """

    def __init__(
        self,
        calendar_id: str,
        timezone: str = "America/Guayaquil",
    ):
        self.calendar_id = calendar_id
        self.timezone = timezone
        self._local_tz = ZoneInfo(timezone)
        self._service = None

        logger.info(
            "GoogleCalendarService inicializado",
            calendar_id=calendar_id,
            timezone=timezone,
        )

    def _get_service(self):
        """Lazy init del servicio Google Calendar API."""
        if self._service is None:
            creds = _build_credentials()
            self._service = build_calendar_service(credentials=creds)
            logger.info("Google Calendar API service listo")
        return self._service

    # ─── Helpers de timezone ───────────────────────────────────────────────

    def to_utc(self, local_dt: datetime) -> datetime:
        if local_dt.tzinfo is None:
            local_dt = local_dt.replace(tzinfo=self._local_tz)
        return local_dt.astimezone(ZoneInfo("UTC"))

    def from_utc(self, utc_dt: datetime) -> datetime:
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=ZoneInfo("UTC"))
        return utc_dt.astimezone(self._local_tz)

    # ─── Operaciones de calendario ─────────────────────────────────────────

    async def list_events(
        self,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 250,
    ) -> List[Dict[str, Any]]:
        """Lista eventos en un rango de fechas."""

        def _sync():
            start_utc = self.to_utc(start_date)
            end_utc = self.to_utc(end_date)
            result = (
                self._get_service()
                .events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start_utc.isoformat(),
                    timeMax=end_utc.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = result.get("items", [])
            logger.info("Eventos listados", count=len(events))
            return events

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def search_events_by_query(
        self,
        q: str,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Busca eventos con texto libre (título, descripción, etc.)."""

        def _sync():
            start_utc = self.to_utc(start_date)
            end_utc = self.to_utc(end_date)
            result = (
                self._get_service()
                .events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start_utc.isoformat(),
                    timeMax=end_utc.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                    q=q,
                )
                .execute()
            )
            events = result.get("items", [])
            logger.info("Búsqueda por query", q=q[:50], count=len(events))
            return events

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def create_event(
        self,
        title: str,
        start: datetime = None,
        end: datetime = None,
        description: str = "",
        attendees: Optional[List[str]] = None,
        location: str = "",
        reminder_minutes: int = 30,
        # Legacy param names (compatibility)
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> tuple[str, str]:
        """Crea evento en Google Calendar. Retorna (event_id, html_link)."""
        # Soportar ambos nombres de parámetros
        start_dt = start or start_time
        end_dt = end or end_time

        def _sync():
            start_utc = self.to_utc(start_dt)
            end_utc = self.to_utc(end_dt)
            event = {
                "summary": title,
                "description": description,
                "location": location,
                "start": {
                    "dateTime": start_utc.isoformat(),
                    "timeZone": self.timezone,
                },
                "end": {
                    "dateTime": end_utc.isoformat(),
                    "timeZone": self.timezone,
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": reminder_minutes}],
                },
            }
            if attendees:
                event["attendees"] = [{"email": e} for e in attendees]

            created = (
                self._get_service()
                .events()
                .insert(
                    calendarId=self.calendar_id,
                    body=event,
                    sendNotifications=True,
                )
                .execute()
            )
            event_id = created.get("id", "")
            html_link = created.get("htmlLink", "")

            logger.info(
                "Evento creado",
                event_id=event_id,
                title=title,
                start=start_utc.isoformat(),
            )
            return (event_id, html_link)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Actualiza un evento existente."""

        def _sync():
            svc = self._get_service()
            event = svc.events().get(calendarId=self.calendar_id, eventId=event_id).execute()

            if title is not None:
                event["summary"] = title
            if description is not None:
                event["description"] = description
            if attendees is not None:
                event["attendees"] = [{"email": e} for e in attendees]
            if start_time is not None and end_time is not None:
                start_utc = self.to_utc(start_time)
                end_utc = self.to_utc(end_time)
                event["start"] = {"dateTime": start_utc.isoformat(), "timeZone": self.timezone}
                event["end"] = {"dateTime": end_utc.isoformat(), "timeZone": self.timezone}

            updated = (
                svc.events()
                .update(calendarId=self.calendar_id, eventId=event_id, body=event)
                .execute()
            )
            logger.info("Evento actualizado", event_id=event_id)
            return updated

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def delete_event(self, event_id: str) -> bool:
        """Elimina un evento. Retorna True si se eliminó (o ya no existía)."""

        def _sync():
            try:
                self._get_service().events().delete(
                    calendarId=self.calendar_id,
                    eventId=event_id,
                    sendNotifications=True,
                ).execute()
                logger.info("Evento eliminado", event_id=event_id)
                return True
            except HttpError as e:
                if e.resp.status == 410:
                    logger.warning("Evento ya no existía", event_id=event_id)
                    return True
                logger.error("Error eliminando evento", event_id=event_id, error=str(e))
                raise

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un evento por ID. Retorna None si no existe."""

        def _sync():
            try:
                return (
                    self._get_service()
                    .events()
                    .get(calendarId=self.calendar_id, eventId=event_id)
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 404:
                    return None
                raise

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)

    async def get_available_slots(
        self,
        date,
        duration_minutes: int = 60,
        start_hour: int = 9,
        end_hour: int = 18,
    ) -> List[Dict[str, Any]]:
        """
        Retorna slots libres del día. Consulta eventos existentes y
        excluye los bloques ocupados.
        """
        if isinstance(date, datetime):
            date_only = date.date()
        else:
            date_only = date

        day_start = datetime.combine(date_only, time(hour=start_hour), tzinfo=self._local_tz)
        day_end = datetime.combine(date_only, time(hour=end_hour), tzinfo=self._local_tz)

        events = await self.list_events(day_start, day_end)

        busy_slots: set = set()
        for event in events:
            start_str = event["start"].get("dateTime")
            end_str = event["end"].get("dateTime")
            if not start_str or not end_str:
                continue
            event_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            event_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            current_busy = self.from_utc(event_start).replace(second=0, microsecond=0)
            end_local = self.from_utc(event_end)
            while current_busy < end_local:
                busy_slots.add(current_busy)
                current_busy += timedelta(minutes=duration_minutes)

        available_slots = []
        current = day_start
        # Validar que el slot COMPLETO cabe dentro del horario de atención
        while current + timedelta(minutes=duration_minutes) <= day_end:
            if current not in busy_slots:
                available_slots.append(
                    {
                        "start": current,
                        "end": current + timedelta(minutes=duration_minutes),
                        "available": True,
                    }
                )
            current += timedelta(minutes=duration_minutes)

        logger.info(
            "Slots calculados",
            date=date_only.isoformat(),
            available=len(available_slots),
            busy=len(busy_slots),
        )
        return available_slots

    async def check_availability(self, start_time: datetime, end_time: datetime) -> bool:
        """Verifica si un slot específico está libre (sin solapamiento)."""
        date = start_time.date()
        day_start = datetime.combine(date, time(0), tzinfo=self._local_tz)
        day_end = datetime.combine(date, time(23, 59), tzinfo=self._local_tz)

        events = await self.list_events(day_start, day_end)

        slot_start_utc = self.to_utc(start_time)
        slot_end_utc = self.to_utc(end_time)

        for event in events:
            es = event["start"].get("dateTime")
            ee = event["end"].get("dateTime")
            if not es or not ee:
                continue
            event_start = datetime.fromisoformat(es.replace("Z", "+00:00"))
            event_end = datetime.fromisoformat(ee.replace("Z", "+00:00"))
            if slot_start_utc < event_end and slot_end_utc > event_start:
                return False

        return True

    async def watch_calendar(self, webhook_url: str, channel_id: str) -> Dict[str, Any]:
        """
        Step 7: Habilita notificaciones push para cambios en el calendario.

        Args:
            webhook_url: URL donde Google enviará notificaciones (ej: https://example.com/webhook/google-calendar)
            channel_id: ID único del canal (ej: uuid del Proyecto)

        Returns:
            Dict con resource_id para renovaciones futuras
        """
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._watch_calendar_sync,
                webhook_url,
                channel_id,
            )
            return result
        except Exception as e:
            logger.error("Error en watch_calendar", error=str(e))
            return {}

    def _watch_calendar_sync(self, webhook_url: str, channel_id: str) -> Dict[str, Any]:
        """Wrapper síncrono para watch_calendar."""
        try:
            service = self.service
            body = {
                "id": channel_id,
                "type": "webhook",
                "address": webhook_url,
            }
            result = service.calendars().watch(
                calendarId=self.calendar_id,
                body=body,
            ).execute()
            logger.info(
                "Webhook registrado en Google Calendar",
                channel_id=channel_id,
                resource_id=result.get("resourceId"),
            )
            return result
        except Exception as e:
            logger.error("Error registrando webhook", error=str(e))
            return {}

    async def stop_watching_calendar(self, channel_id: str, resource_id: str) -> None:
        """
        Step 7: Detiene notificaciones push para un canal.

        Args:
            channel_id: ID del canal
            resource_id: Resource ID retornado por watch_calendar
        """
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._stop_watching_sync,
                channel_id,
                resource_id,
            )
        except Exception as e:
            logger.error("Error deteniendo webhook", error=str(e))

    def _stop_watching_sync(self, channel_id: str, resource_id: str) -> None:
        """Wrapper síncrono para stop_watching_calendar."""
        try:
            service = self.service
            service.channels().stop(
                body={
                    "id": channel_id,
                    "resourceId": resource_id,
                }
            ).execute()
            logger.info("Webhook detenido", channel_id=channel_id)
        except Exception as e:
            logger.error("Error ejecutando stop", error=str(e))


# ─── Factories ────────────────────────────────────────────────────────────────

def get_calendar_service_for_odontologist(odontologist_email: str) -> GoogleCalendarService:
    """Crea servicio para un odontólogo específico (su email = calendar ID)."""
    settings = get_settings()
    return GoogleCalendarService(
        calendar_id=odontologist_email,
        timezone=settings.GOOGLE_CALENDAR_TIMEZONE,
    )


def get_default_calendar_service() -> GoogleCalendarService:
    """Crea servicio con el calendar ID por defecto del .env."""
    settings = get_settings()
    return GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_DEFAULT_ID,
        timezone=settings.GOOGLE_CALENDAR_TIMEZONE,
    )
