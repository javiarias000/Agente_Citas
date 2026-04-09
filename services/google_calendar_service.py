#!/usr/bin/env python3
"""
Servicio de integración con Google Calendar API v3.

Este servicio:
- Se autentica con OAuth2 desde archivo de credenciales
- Consulta eventos disponibles en tiempo real
- Crea/actualiza/elimina eventos con alta precisión
- Sincroniza con PostgreSQL (permitiendo caché local)
- Maneja timezones correctamente (UTC ↔ local)
- Retry automático en errores transitorios

Requiere:
- pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
- Archivo de credenciales OAuth2 en GOOGLE_CALENDAR_CREDENTIALS_PATH
"""

import os
import json
import hashlib
import base64
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import structlog
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

# Cargar variables de entorno desde .env al importar este módulo
# Esto asegura que os.getenv() tenga acceso a las variables
load_dotenv()

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.config import get_settings

logger = structlog.get_logger("google_calendar")


class GoogleCalendarService:
    """
    Servicio para gestionar Google Calendar.

    Atributos:
        calendar_id: ID del calendario (email o 'primary')
        timezone: Timezone para mostrar fechas (ej: 'America/Guayaquil')
        credentials_path: Ruta al JSON de credenciales OAuth2
        service: Cliente de Google Calendar API (lazy init)
    """

    # Scope de permisos requeridos
    # NOTA: Usar solo 'calendar' que incluye eventos. Si se necesita separación, generar refresh token con ambos scopes.
    SCOPES = [
        'https://www.googleapis.com/auth/calendar'  # Read/write (incluye eventos)
    ]

    def __init__(
        self,
        calendar_id: str,
        credentials_path: Optional[str] = None,
        timezone: str = "America/Guayaquil",
        redirect_uri: Optional[str] = None
    ):
        """
        Inicializa el servicio.

        Args:
            calendar_id: Email del calendario o 'primary'
            credentials_path: Ruta a google_credentials.json
            timezone: Timezone local (para display)
            redirect_uri: URI de redirección para OAuth flow (ej: http://localhost:8000/oauth2callback)
        """
        self.calendar_id = calendar_id
        self.timezone = timezone
        self.credentials_path = credentials_path or get_settings().GOOGLE_CALENDAR_CREDENTIALS_PATH
        self.redirect_uri = redirect_uri or getattr(get_settings(), 'GOOGLE_REDIRECT_URI', None)
        self._service = None
        self._local_tz = ZoneInfo(timezone)

        logger.info(
            "GoogleCalendarService creado",
            calendar_id=calendar_id,
            timezone=timezone,
            redirect_uri=self.redirect_uri
        )

    def _get_credentials(self) -> Credentials:
        """
        Obtiene credenciales OAuth2.

        Estrategia (en orden):
        1. Usar refresh token de GOOGLE_REFRESH_TOKEN (settings)
        2. Usar token.json cacheado
        3. Si no hay o expira: flujo OAuth con navegador (run_local_server)

        Returns:
            Credentials válidas
        """
        creds = None
        token_path = os.path.join(os.path.dirname(self.credentials_path), 'token.json')

        # ============================================
        # OPCIÓN 1: Refresh Token desde .env (variables de entorno)
        # ============================================
        refresh_token = os.getenv('GOOGLE_REFRESH_TOKEN')
        if refresh_token:
            client_id = os.getenv('GOOGLE_CLIENT_ID')
            client_secret = os.getenv('GOOGLE_CLIENT_SECRET')

            if client_id and client_secret:
                try:
                    creds = Credentials(
                        None,  # access_token (se obtiene con refresh)
                        refresh_token=refresh_token,
                        token_uri='https://oauth2.googleapis.com/token',
                        client_id=client_id,
                        client_secret=client_secret,
                        scopes=self.SCOPES
                    )
                    # Refrescar para obtener access_token
                    creds.refresh(Request())
                    logger.info("Credenciales obtenidas via refresh token (env)")
                    return creds
                except Exception as e:
                    logger.error("Error usando refresh token de env", error=str(e))
                    creds = None
            else:
                logger.warning("GOOGLE_REFRESH_TOKEN seteado pero faltan GOOGLE_CLIENT_ID/SECRET en .env")

        # ============================================
        # OPCIÓN 2: Token cacheado en archivo
        # ============================================
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, self.SCOPES)
                logger.debug("Token cargado desde archivo", path=token_path)
            except Exception as e:
                logger.warning("Error leyendo token.json", error=str(e))
                creds = None

        # ============================================
        # OPCIÓN 3: Flujo OAuth completo (navegador)
        # ============================================
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Refrescar token de archivo
                logger.info("Refrescando token desde archivo...")
                try:
                    creds.refresh(Request())
                    logger.info("Token refrescado exitosamente")
                    # Guardar token actualizado
                    with open(token_path, 'w') as f:
                        f.write(creds.to_json())
                    return creds
                except Exception as e:
                    logger.error("Error refrescando token", error=str(e))
                    creds = None

            if not creds:
                # Flujo completo: abrir navegador
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Archivo de credenciales no encontrado: {self.credentials_path}\n"
                        "Descárgalo desde Google Cloud Console > APIs & Services > Credentials"
                    )

                logger.info("Iniciando flujo OAuth con navegador...")
                flow = Flow.from_client_secrets_file(
                    self.credentials_path,
                    scopes=self.SCOPES,
                    redirect_uri=self.redirect_uri
                )

                # Usar redirect_uri si está configurado
                if self.redirect_uri:
                    # Para endpoint callback (producción)
                    flow.redirect_uri = self.redirect_uri
                    # Nota: En este modo, NO usamos run_local_server
                    # En su lugar, generamos la URL y el usuario la abre manualmente
                    auth_url, _ = flow.authorization_url(
                        access_type='offline',
                        include_granted_scopes='true'
                    )
                    logger.info("URL de autorización generada", url=auth_url)
                    # Esto no debería usarse en _get_credentials, sino en un endpoint aparte
                    raise NotImplementedError(
                        "Para usar redirect_uri, debes usar el método get_authorization_url() "
                        "y el endpoint /oauth2callback"
                    )
                else:
                    # Modo automático (localhost) - abre navegador
                    creds = flow.run_local_server(port=0)

                    # Guardar token
                    os.makedirs(os.path.dirname(token_path), exist_ok=True)
                    with open(token_path, 'w') as token_file:
                        token_file.write(creds.to_json())
                    logger.info("Token guardado en", path=token_path)

        return creds

    @property
    def service(self):
        """Lazy initialization del servicio Google Calendar"""
        if self._service is None:
            creds = self._get_credentials()
            self._service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
            logger.info("Google Calendar service inicializado")
        return self._service

    # ============================================
    # TIMEZONE CONVERSION HELPERS
    # ============================================
    def to_utc(self, local_dt: datetime) -> datetime:
        """
        Convierte datetime local (sin tzinfo) a UTC.

        Args:
            local_dt: Datetime en timezone local

        Returns:
            Datetime en UTC con tzinfo
        """
        if local_dt.tzinfo is None:
            local_dt = local_dt.replace(tzinfo=self._local_tz)
        return local_dt.astimezone(ZoneInfo("UTC"))

    def from_utc(self, utc_dt: datetime) -> datetime:
        """
        Convierte UTC a datetime local para display.

        Args:
            utc_dt: Datetime en UTC

        Returns:
            Datetime en timezone local
        """
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=ZoneInfo("UTC"))
        return utc_dt.astimezone(self._local_tz)

    # ============================================
    # GOOGLE CALENDAR API OPERATIONS
    # ============================================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(HttpError)
    )
    async def list_events(
        self,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 250
    ) -> List[Dict[str, Any]]:
        """
        Lista eventos en un rango de fechas.

        Args:
            start_date: Fecha/hora inicio (local o UTC)
            end_date: Fecha/hora fin (local o UTC)
            max_results: Máximo eventos a retornar

        Returns:
            Lista de eventos de Google Calendar
        """
        try:
            # Convertir a UTC para API
            start_utc = self.to_utc(start_date)
            end_utc = self.to_utc(end_date)

            # Formato ISO para API
            time_min = start_utc.isoformat()
            time_max = end_utc.isoformat()

            logger.debug(
                "Listando eventos",
                calendar=self.calendar_id,
                start=time_min,
                end=time_max
            )

            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            logger.info("Eventos encontrados", count=len(events))
            return events

        except HttpError as e:
            logger.error("Error listando eventos", error=str(e), calendar=self.calendar_id)
            raise
        except Exception as e:
            logger.error("Error inesperado listando eventos", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(HttpError)
    )
    async def search_events_by_query(
        self,
        q: str,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Busca eventos usando texto libre (título, descripción, etc.).

        Args:
            q: Texto a buscar (nombre del paciente, teléfono, etc.)
            start_date: Inicio del rango
            end_date: Fin del rango
            max_results: Máximo eventos a retornar

        Returns:
            Lista de eventos que coincidan
        """
        try:
            start_utc = self.to_utc(start_date)
            end_utc = self.to_utc(end_date)

            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=start_utc.isoformat(),
                timeMax=end_utc.isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
                q=q,
            ).execute()

            events = events_result.get("items", [])
            logger.info(
                "Búsqueda por query",
                q=q[:50],
                count=len(events),
            )
            return events

        except HttpError as e:
            logger.error("Error buscando eventos por query", q=q, error=str(e))
            raise
        except Exception as e:
            logger.error("Error inesperado buscando eventos", q=q, error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(HttpError)
    )
    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: str = "",
        attendees: Optional[List[str]] = None,
        location: str = "",
        reminder_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        Crea un evento en Google Calendar.

        Args:
            title: Título del evento
            start_time: Fecha/hora inicio (local)
            end_time: Fecha/hora fin (local)
            description: Descripción (puede incluir notes)
            attendees: Lista de emails de asistentes
            location: Ubicación (opcional)
            reminder_minutes: Minutos antes para recordatorio

        Returns:
            Dict con evento creado (incluye id, htmlLink)
        """
        try:
            # Convertir a UTC
            start_utc = self.to_utc(start_time)
            end_utc = self.to_utc(end_time)

            event = {
                'summary': title,
                'description': description,
                'location': location,
                'start': {
                    'dateTime': start_utc.isoformat(),
                    'timeZone': self.timezone,
                },
                'end': {
                    'dateTime': end_utc.isoformat(),
                    'timeZone': self.timezone,
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': reminder_minutes},
                    ],
                },
            }

            if attendees:
                event['attendees'] = [{'email': email} for email in attendees]

            logger.info(
                "Creando evento en Google Calendar",
                calendar=self.calendar_id,
                title=title,
                start=start_utc.isoformat()
            )

            created_event = self.service.events().insert(
                calendarId=self.calendar_id,
                body=event,
                sendNotifications=True  # Enviar invitación por email
            ).execute()

            logger.info(
                "Evento creado exitosamente",
                event_id=created_event['id'],
                html_link=created_event.get('htmlLink')
            )

            return created_event

        except HttpError as e:
            logger.error("Error creando evento", error=str(e), calendar=self.calendar_id)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(HttpError)
    )
    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Actualiza un evento existente.

        Args:
            event_id: ID del evento en Google Calendar
            title: Nuevo título (opcional)
            start_time: Nueva fecha/hora inicio (local)
            end_time: Nueva fecha/hora fin (local)
            description: Nueva descripción (opcional)
            attendees: Nuevos asistentes (opcional)

        Returns:
            Dict con evento actualizado
        """
        try:
            # Primero obtener evento existente
            event = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()

            # Actualizar campos
            if title is not None:
                event['summary'] = title
            if description is not None:
                event['description'] = description
            if attendees is not None:
                event['attendees'] = [{'email': email} for email in attendees]

            if start_time is not None and end_time is not None:
                start_utc = self.to_utc(start_time)
                end_utc = self.to_utc(end_time)
                event['start'] = {
                    'dateTime': start_utc.isoformat(),
                    'timeZone': self.timezone,
                }
                event['end'] = {
                    'dateTime': end_utc.isoformat(),
                    'timeZone': self.timezone,
                }

            logger.info(
                "Actualizando evento",
                event_id=event_id,
                calendar=self.calendar_id
            )

            updated_event = self.service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=event
            ).execute()

            logger.info("Evento actualizado", event_id=event_id)
            return updated_event

        except HttpError as e:
            logger.error("Error actualizando evento", event_id=event_id, error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(HttpError)
    )
    async def delete_event(self, event_id: str) -> bool:
        """
        Elimina un evento.

        Args:
            event_id: ID del evento

        Returns:
            True si eliminado exitosamente
        """
        try:
            logger.info(
                "Eliminando evento",
                event_id=event_id,
                calendar=self.calendar_id
            )

            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id,
                sendNotifications=True  # Notificar a asistentes
            ).execute()

            logger.info("Evento eliminado", event_id=event_id)
            return True

        except HttpError as e:
            # 410 Gone significa que ya fue eliminado
            if e.resp.status == 410:
                logger.warning("Evento ya no existe", event_id=event_id)
                return True
            logger.error("Error eliminando evento", event_id=event_id, error=str(e))
            raise

    async def get_available_slots(
        self,
        date: datetime,
        duration_minutes: int = 30,
        start_hour: int = 9,
        end_hour: int = 18
    ) -> List[Dict[str, Any]]:
        """
        Obtiene horarios disponibles en una fecha específica.

        Estrategia:
        1. Obtener todos los eventos del día (start date 00:00, end date 23:59)
        2. Generar slots de X minutos entre start_hour y end_hour
        3. Filtrar slots que se solapan con eventos existentes

        Args:
            date: Fecha a consultar (solo el día)
            duration_minutes: Duración de cada slot (default 30)
            start_hour: Hora inicio laboral (default 9)
            end_hour: Hora fin laboral (default 18)

        Returns:
            Lista de slots disponibles, cada uno con:
            {
                'start': datetime,
                'end': datetime,
                'available': True
            }
        """
        try:
            
            # Normalizar al día completo en timezone local
            if isinstance(date, datetime):
                date_only = date.date()
            else:
                date_only = date

            day_start_local = datetime.combine(
                date_only,
                time(hour=start_hour, minute=0),
                tzinfo=self._local_tz
            )

            day_end_local = datetime.combine(
                date_only,
                time(hour=end_hour, minute=0),
                tzinfo=self._local_tz
            )

            # Obtener eventos del día
            events = await self.list_events(day_start_local, day_end_local)

            # Extraer slots ocupados
            busy_slots = set()
            for event in events:
                # Obtener start time del evento
                start_str = event['start'].get('dateTime')
                end_str = event['end'].get('dateTime')

                if not start_str or not end_str:
                    continue  # Todo-day event, skip

                event_start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                event_end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

                event_start_local = self.from_utc(event_start)
                event_end_local = self.from_utc(event_end)

                current_busy = event_start_local.replace(second=0, microsecond=0)

                while current_busy < event_end_local:
                    busy_slots.add(current_busy)
                    current_busy += timedelta(minutes=duration_minutes)


            # Generar todos los slots posibles
            available_slots = []
            current = day_start_local

            while current < day_end_local:
                slot_end = current + timedelta(minutes=duration_minutes)

                # Si el slot no está ocupado, agregar
                if current not in busy_slots:
                    available_slots.append({
                        'start': current,
                        'end': slot_end,
                        'available': True
                    })

                current += timedelta(minutes=duration_minutes)

            logger.info(
                "Slots disponibles calculados",
                date=date_only.isoformat(),
                total=len(available_slots),
                busy=len(busy_slots)
            )

            logger.debug(
                "Slots debug",
                sample_busy=list(busy_slots)[:5]
            )

            return available_slots

        except Exception as e:
            logger.error("Error calculando slots", error=str(e), date=str(date))
            raise

    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene un evento específico por ID.

        Args:
            event_id: ID del evento

        Returns:
            Dict con evento o None si no existe
        """
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()
            return event
        except HttpError as e:
            if e.resp.status == 404:
                return None
            raise

    async def check_availability(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """
        Verifica si un slot específico está disponible (sin conflictos).

        Args:
            start_time: Hora inicio (local)
            end_time: Hora fin (local)

        Returns:
            True si disponible, False si hay conflicto
        """
        try:
            # Normalizar fechas al día
            date = start_time.date()
            
            day_start = datetime.combine(
                date,
                time(hour=0),
                tzinfo=self._local_tz
            )

            day_end = datetime.combine(
                date,
                time(hour=23, minute=59),
                tzinfo=self._local_tz
            )

            # Obtener eventos del día
            events = await self.list_events(day_start, day_end)

            # Verificar solape
            slot_start_utc = self.to_utc(start_time)
            slot_end_utc = self.to_utc(end_time)

            for event in events:
                event_start_str = event['start'].get('dateTime')
                event_end_str = event['end'].get('dateTime')

                if not event_start_str or not event_end_str:
                    continue

                event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))

                # Chequear solape
                if (slot_start_utc < event_end) and (slot_end_utc > event_start):
                    logger.debug(
                        "Conflicto detectado",
                        slot_start=slot_start_utc.isoformat(),
                        slot_end=slot_end_utc.isoformat(),
                        event_start=event_start.isoformat(),
                        event_end=event_end.isoformat()
                    )
                    return False

            return True

        except Exception as e:
            logger.error("Error verificando disponibilidad", error=str(e))
            raise

    # ============================================
    # OAUTH FLOW HELPERS
    # ============================================
    def get_authorization_url(self) -> str:
        """
        Genera URL de autorización OAuth2 con PKCE para que el usuario autorice.

        Returns:
            URL completa para autorizar la aplicación
        """
        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"Archivo de credenciales no encontrado: {self.credentials_path}"
            )

        # Generar code_verifier (random 32-96 bytes, luego base64url)
        code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip('=')
        # Calcular code_challenge = SHA256(code_verifier) -> base64url
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')

        flow = Flow.from_client_secrets_file(
            self.credentials_path,
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri
        )

        # Generar URL con code_challenge (PKCE)
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
            code_challenge=code_challenge,
            code_challenge_method='S256'
        )

        # Guardar code_verifier para usar en el callback
        credentials_dir = os.path.dirname(os.path.abspath(self.credentials_path))
        verifier_file = os.path.join(credentials_dir, 'oauth_verifier.txt')
        try:
            with open(verifier_file, 'w') as f:
                f.write(code_verifier)
            logger.debug("Code verifier guardado para callback", path=verifier_file, verifier_len=len(code_verifier))
        except Exception as e:
            logger.warning("No se pudo guardar code_verifier", error=str(e))

        logger.info("URL de autorización generada (con PKCE)", url=auth_url, state=state)
        return auth_url

    def exchange_code_for_tokens(self, code: str) -> Credentials:
        """
        Intercambia código de autorización por tokens.

        Args:
            code: Código recibido en el callback /oauth2callback

        Returns:
            Credentials con access_token y refresh_token
        """
        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"Archivo de credenciales no encontrado: {self.credentials_path}"
            )

        # Recuperar code_verifier guardado (necesario para PKCE)
        credentials_dir = os.path.dirname(os.path.abspath(self.credentials_path))
        verifier_file = os.path.join(credentials_dir, 'oauth_verifier.txt')
        code_verifier = None
        try:
            if os.path.exists(verifier_file):
                with open(verifier_file, 'r') as f:
                    code_verifier = f.read().strip()
                logger.debug("Code verifier recuperado", verifier_len=len(code_verifier) if code_verifier else 0)
                # Eliminar archivo después de usar (limpieza)
                os.remove(verifier_file)
            else:
                logger.warning("Archivo code_verifier no encontrado", path=verifier_file)
        except Exception as e:
            logger.warning("No se pudo recuperar code_verifier", error=str(e))

        flow = Flow.from_client_secrets_file(
            self.credentials_path,
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri
        )

        # Intercambiar código por tokens (con PKCE si tenemos code_verifier)
        if code_verifier:
            flow.fetch_token(code=code, code_verifier=code_verifier)
        else:
            # Sin PKCE (fallback - puede fallar con Google si requiere PKCE)
            flow.fetch_token(code=code)

        creds = flow.credentials

        # Guardar token para futuros usos
        token_dir = os.path.dirname(os.path.abspath(self.credentials_path))
        token_path = os.path.join(token_dir, 'token.json')
        os.makedirs(token_dir, exist_ok=True)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

        logger.info(
            "Token intercambiado y guardado",
            token_path=token_path,
            refresh_token_exists=bool(creds.refresh_token)
        )

        return creds

    def _save_credentials(self, creds: Credentials) -> None:
        """Helper: guarda credenciales a token.json"""
        token_path = os.path.join(os.path.dirname(self.credentials_path), 'token.json')
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
        logger.info("Token guardado", path=token_path)


# ============================================
# FACTORY HELPERS
# ============================================
def get_calendar_service_for_odontologist(odontologist_email: str) -> GoogleCalendarService:
    """
    Factory: crea servicio para un odontólogo específico.

    Args:
        odontologist_email: Email del odontólogo (también es Calendar ID)

    Returns:
        GoogleCalendarService configurado
    """
    settings = get_settings()

    return GoogleCalendarService(
        calendar_id=odontologist_email,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
        timezone=settings.GOOGLE_CALENDAR_TIMEZONE,
        redirect_uri=getattr(settings, 'GOOGLE_REDIRECT_URI', None)
    )


def get_default_calendar_service() -> GoogleCalendarService:
    """
    Factory: crea servicio con calendar_id por defecto.

    Returns:
        GoogleCalendarService configurado
    """
    settings = get_settings()

    return GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_DEFAULT_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
        timezone=settings.GOOGLE_CALENDAR_TIMEZONE,
        redirect_uri=getattr(settings, 'GOOGLE_REDIRECT_URI', None)
    )
