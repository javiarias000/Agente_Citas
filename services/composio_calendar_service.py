#!/usr/bin/env python3
"""
Google Calendar service via Composio Tool Router (v3 API).

Usa composio-client v1.33+ con session.execute() para llamar directamente
los tools de Google Calendar sin depender de MCP ni de credenciales OAuth locales.

Interface pública idéntica a GoogleCalendarService para que los nodos no cambien.

Variables de entorno requeridas:
    COMPOSIO_API_KEY   - API key de Composio (app.composio.dev/settings)
    COMPOSIO_USER_ID   - User ID asociado a la conexión de Google Calendar

Setup inicial (una vez):
    python scripts/setup_composio.py
"""

import os
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger("composio_calendar")


class ComposioCalendarService:
    """
    Servicio de Google Calendar usando Composio Tool Router v3.

    Llama los tools de Google Calendar via session.execute() — sin credenciales
    OAuth locales, sin MCP polling, sin refresh tokens.

    Interface pública idéntica a GoogleCalendarService:
        create_event(title, start_time, end_time, description, ...) → dict con 'id' y 'htmlLink'
        delete_event(event_id) → bool
        list_events(start_date, end_date, max_results) → list[dict]
        search_events_by_query(q, start_date, end_date) → list[dict]
        get_available_slots(date, duration_minutes, ...) → list[dict]
        check_availability(start_time, end_time) → bool
        update_event(event_id, title, start_time, end_time, ...) → dict
    """

    def __init__(
        self,
        calendar_id: str = "primary",
        timezone: str = "America/Guayaquil",
    ):
        self.calendar_id = calendar_id
        self.timezone = timezone
        self._local_tz = ZoneInfo(timezone)
        self._client = None
        self._session_id: Optional[str] = None
        self._connected_account_id: Optional[str] = None
        self._initialized = False

        logger.info(
            "ComposioCalendarService instanciado (pendiente initialize())",
            calendar_id=calendar_id,
            timezone=timezone,
        )

    # ─── Inicialización ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """
        Conecta con Composio, descubre la conexión activa de Google Calendar
        y crea una sesión Tool Router lista para ejecutar tools.
        Se llama una sola vez desde _init_langgraph del orchestrator.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._initialize_sync)

    def _initialize_sync(self) -> None:
        api_key = os.getenv("COMPOSIO_API_KEY")
        user_id = os.getenv("COMPOSIO_USER_ID")

        if not api_key:
            raise ValueError("COMPOSIO_API_KEY no configurado en .env")
        if not user_id:
            raise ValueError("COMPOSIO_USER_ID no configurado en .env")

        try:
            from composio_client import Composio
        except ImportError as e:
            raise ImportError(
                "composio-client no instalado. Ejecuta: pip install composio-langchain"
            ) from e

        self._client = Composio(api_key=api_key)
        self._user_id = user_id

        # Descubrir connected_account_id activo para googlecalendar
        conn_resp = self._client.connected_accounts.list(
            toolkit_slugs=["googlecalendar"],
            user_ids=[user_id],
            statuses=["ACTIVE"],
        )
        if not conn_resp.items:
            raise RuntimeError(
                f"No hay conexión activa de Google Calendar para user_id='{user_id}'. "
                "Ejecuta: python scripts/setup_composio.py"
            )
        self._connected_account_id = conn_resp.items[0].id
        logger.info(
            "Conexión Google Calendar encontrada",
            connected_account_id=self._connected_account_id,
            user_id=user_id,
        )

        # Crear sesión Tool Router
        self._session_id = self._create_session()
        self._initialized = True
        logger.info(
            "ComposioCalendarService inicializado",
            session_id=self._session_id,
            connected_account_id=self._connected_account_id,
        )

    def _create_session(self) -> str:
        session = self._client.tool_router.session.create(
            user_id=self._user_id,
            toolkits={"enable": ["googlecalendar"]},
            connected_accounts={"googlecalendar": self._connected_account_id},
        )
        return session.session_id

    # ─── Ejecución de tools ────────────────────────────────────────────────────

    def _execute(self, tool_slug: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Llama session.execute() con retry automático si la sesión expiró.
        Retorna el dict `data` del resultado.
        """
        from composio_client import BadRequestError

        try:
            result = self._client.tool_router.session.execute(
                self._session_id,
                tool_slug=tool_slug,
                arguments=arguments,
            )
        except BadRequestError as e:
            err_str = str(e)
            # Sesión expirada o inválida → recrear y reintentar
            if "session" in err_str.lower() or "not found" in err_str.lower():
                logger.warning("Sesión expirada, recreando...", error=err_str[:100])
                self._session_id = self._create_session()
                result = self._client.tool_router.session.execute(
                    self._session_id,
                    tool_slug=tool_slug,
                    arguments=arguments,
                )
            else:
                raise

        data = result.model_dump() if hasattr(result, "model_dump") else {}
        if data.get("error"):
            logger.error("Composio tool error", tool=tool_slug, error=data["error"])
        return data.get("data") or {}

    async def _execute_async(self, tool_slug: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._execute, tool_slug, arguments)

    # ─── Helpers ───────────────────────────────────────────────────────────────

    def _dt_to_local_naive(self, dt: datetime) -> datetime:
        """Convierte a timezone local y quita tzinfo para el API de Composio."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._local_tz)
        return dt.astimezone(self._local_tz).replace(tzinfo=None)

    def _to_utc_iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._local_tz)
        return dt.astimezone(ZoneInfo("UTC")).isoformat()

    # ─── Operaciones públicas ──────────────────────────────────────────────────

    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: str = "",
        attendees: Optional[List[str]] = None,
        location: str = "",
        reminder_minutes: int = 30,  # ignorado — Composio usa el default del calendario
    ) -> Dict[str, Any]:
        """
        Crea un evento en Google Calendar.
        Retorna dict con 'id' y 'htmlLink' (compatible con GoogleCalendarService).
        """
        local_start = self._dt_to_local_naive(start_time)
        duration = end_time - start_time
        duration_minutes = int(duration.total_seconds() // 60)
        duration_hours = duration_minutes // 60
        duration_mins_remainder = duration_minutes % 60

        args: Dict[str, Any] = {
            "start_datetime": local_start.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone": self.timezone,
            "summary": title,
            "description": description,
            "event_duration_hour": duration_hours,
            "event_duration_minutes": duration_mins_remainder,
            "calendar_id": self.calendar_id,
            "create_meeting_room": False,
        }
        if location:
            args["location"] = location
        if attendees:
            args["attendees"] = attendees

        logger.info(
            "create_event",
            title=title,
            start=local_start.isoformat(),
            duration_min=duration_minutes,
        )

        data = await self._execute_async("GOOGLECALENDAR_CREATE_EVENT", args)
        event = data.get("response_data") or {}

        logger.info(
            "create_event OK",
            event_id=event.get("id"),
            html_link=(event.get("htmlLink") or "")[:60],
        )
        return event  # contiene 'id', 'htmlLink', etc.

    async def delete_event(self, event_id: str) -> bool:
        """Elimina un evento. Retorna True si se eliminó o ya no existía."""
        logger.info("delete_event", event_id=event_id)
        try:
            data = await self._execute_async(
                "GOOGLECALENDAR_DELETE_EVENT",
                {"event_id": event_id, "calendar_id": self.calendar_id},
            )
            status = (data.get("response_data") or {}).get("status", "")
            return status == "success" or "success" in str(data).lower()
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "404" in err or "410" in err or "gone" in err:
                logger.warning("delete_event: evento ya no existía", event_id=event_id)
                return True
            logger.error("delete_event: error", event_id=event_id, error=str(e))
            raise

    async def list_events(
        self,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 250,
    ) -> List[Dict[str, Any]]:
        """Lista eventos en un rango de fechas."""
        data = await self._execute_async(
            "GOOGLECALENDAR_EVENTS_LIST",
            {
                "timeMin": self._to_utc_iso(start_date),
                "timeMax": self._to_utc_iso(end_date),
                "maxResults": max_results,
                "calendarId": self.calendar_id,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )
        events = data.get("items", [])
        logger.info("list_events", count=len(events))
        return events

    async def search_events_by_query(
        self,
        q: str,
        start_date: datetime,  # no usado por FIND_EVENT — se busca en todo el calendario
        end_date: datetime,    # no usado por FIND_EVENT
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Busca eventos con texto libre (nombre del paciente, etc.)."""
        data = await self._execute_async(
            "GOOGLECALENDAR_FIND_EVENT",
            {
                "query": q,
                "calendar_id": self.calendar_id,
                "max_results": max_results,
            },
        )
        # FIND_EVENT retorna event_data.event_data o items directamente
        raw = data.get("event_data") or {}
        events = raw.get("event_data") or data.get("items", [])
        if not isinstance(events, list):
            events = [events] if events else []
        logger.info("search_events_by_query", q=q[:40], count=len(events))
        return events

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
        args: Dict[str, Any] = {
            "event_id": event_id,
            "calendar_id": self.calendar_id,
        }
        if title is not None:
            args["summary"] = title
        if description is not None:
            args["description"] = description
        if start_time is not None:
            local_start = self._dt_to_local_naive(start_time)
            args["start_datetime"] = local_start.strftime("%Y-%m-%dT%H:%M:%S")
            args["timezone"] = self.timezone
        if start_time is not None and end_time is not None:
            duration = end_time - start_time
            mins = int(duration.total_seconds() // 60)
            args["event_duration_hour"] = mins // 60
            args["event_duration_minutes"] = mins % 60
        if attendees is not None:
            args["attendees"] = attendees

        logger.info("update_event", event_id=event_id)
        data = await self._execute_async("GOOGLECALENDAR_PATCH_CALENDAR", args)
        return data.get("response_data") or {}

    async def get_available_slots(
        self,
        date: Any,
        duration_minutes: int = 60,
        start_hour: int = 9,
        end_hour: int = 18,
    ) -> List[Dict[str, Any]]:
        """
        Retorna slots libres del día usando FIND_FREE_SLOTS.
        Calcula intervalos de `duration_minutes` dentro del horario laboral.
        """
        if isinstance(date, datetime):
            date_only = date.date()
        else:
            date_only = date

        data = await self._execute_async(
            "GOOGLECALENDAR_FIND_FREE_SLOTS",
            {
                "date": date_only.strftime("%Y-%m-%d"),
                "timezone": self.timezone,
                "calendar_id": self.calendar_id,
            },
        )

        # Log raw response para diagnóstico de mismatch de calendar_id
        calendars_raw = data.get("calendars") or {}
        logger.info(
            "FIND_FREE_SLOTS raw calendar keys",
            keys=list(calendars_raw.keys()),
            calendar_id=self.calendar_id,
        )

        # Extraer períodos ocupados — buscar en todas las keys conocidas
        cal_data = (
            calendars_raw.get(self.calendar_id)
            or calendars_raw.get("primary")
            or (next(iter(calendars_raw.values()), None) if len(calendars_raw) == 1 else None)
            or {}
        )

        busy_periods = cal_data.get("busy", [])

        # Fallback: si FIND_FREE_SLOTS devuelve 0 busy periods, verificar con list_events
        # para detectar citas que el freebusy API podría estar omitiendo.
        if not busy_periods:
            try:
                day_start_dt = datetime.combine(date_only, time(start_hour, 0), tzinfo=self._local_tz)
                day_end_dt = datetime.combine(date_only, time(end_hour, 0), tzinfo=self._local_tz)
                events = await self.list_events(day_start_dt, day_end_dt, max_results=50)
                if events:
                    logger.info(
                        "FIND_FREE_SLOTS devolvió 0 busy — usando list_events como fallback",
                        events_found=len(events),
                    )
                    for ev in events:
                        # Google API anida: {"start": {"dateTime": "..."}} o {"start": {"date": "..."}}
                        s = ev.get("start") or {}
                        e = ev.get("end") or {}
                        ev_start = s.get("dateTime") or s.get("date") or (s if isinstance(s, str) else "")
                        ev_end = e.get("dateTime") or e.get("date") or (e if isinstance(e, str) else "")
                        if ev_start and ev_end:
                            busy_periods.append({"start": ev_start, "end": ev_end})
            except Exception as e:
                logger.warning("Fallback list_events falló", error=str(e))

        # Construir conjuntos de slots ocupados
        busy_slots: set = set()
        for period in busy_periods:
            try:
                p_start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
                p_end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
                p_start_local = p_start.astimezone(self._local_tz).replace(tzinfo=None)
                p_end_local = p_end.astimezone(self._local_tz).replace(tzinfo=None)
                current = p_start_local.replace(second=0, microsecond=0)
                while current < p_end_local:
                    busy_slots.add(current)
                    current += timedelta(minutes=duration_minutes)
            except (KeyError, ValueError):
                continue

        # Generar slots libres en horario laboral
        day_start = datetime.combine(date_only, time(start_hour, 0))
        day_end = datetime.combine(date_only, time(end_hour, 0))
        available_slots = []
        current = day_start
        while current < day_end:
            if current not in busy_slots:
                available_slots.append(
                    {
                        "start": current.replace(tzinfo=self._local_tz),
                        "end": (current + timedelta(minutes=duration_minutes)).replace(
                            tzinfo=self._local_tz
                        ),
                        "available": True,
                    }
                )
            current += timedelta(minutes=duration_minutes)

        logger.info(
            "get_available_slots",
            date=str(date_only),
            available=len(available_slots),
            busy_periods=len(busy_periods),
        )
        return available_slots

    async def check_availability(self, start_time: datetime, end_time: datetime) -> bool:
        """Verifica si un slot específico está libre."""
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=self._local_tz)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=self._local_tz)

        events = await self.list_events(start_time, end_time, max_results=10)

        for event in events:
            es = (event.get("start") or {}).get("dateTime")
            ee = (event.get("end") or {}).get("dateTime")
            if not es or not ee:
                continue
            try:
                ev_start = datetime.fromisoformat(es.replace("Z", "+00:00"))
                ev_end = datetime.fromisoformat(ee.replace("Z", "+00:00"))
                if start_time < ev_end and end_time > ev_start:
                    return False
            except ValueError:
                continue
        return True

    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un evento por ID. Retorna None si no existe."""
        try:
            data = await self._execute_async(
                "GOOGLECALENDAR_FIND_EVENT",
                {"query": event_id, "calendar_id": self.calendar_id, "max_results": 1},
            )
            raw = data.get("event_data") or {}
            events = raw.get("event_data") or []
            for ev in events:
                if ev.get("id") == event_id:
                    return ev
            return None
        except Exception:
            return None
