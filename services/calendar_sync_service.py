#!/usr/bin/env python3
"""
Servicio de sincronización bidireccional con Google Calendar.
Maneja webhooks push de cambios en eventos de calendario.

Step 7: Sync bidireccional Google Calendar
"""

from typing import Optional, Dict, Any
import structlog
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("calendar_sync_service")


async def handle_calendar_push_notification(
    payload: Dict[str, Any],
    headers: Dict[str, str],
    calendar_services: Dict[str, Any],
    whatsapp_service: Any,
) -> None:
    """
    Maneja notificaciones push de Google Calendar.

    Args:
        payload: Body del webhook
        headers: Headers (X-Goog-Resource-State, etc.)
        calendar_services: Dict de servicios por doctor
        whatsapp_service: Servicio para enviar mensajes por WhatsApp
    """
    try:
        resource_state = headers.get("X-Goog-Resource-State", "unknown")

        # sync = sincronización inicial, ignorar
        if resource_state == "sync":
            logger.info("Webhook de sincronización inicial (sync), ignorando")
            return

        # exists = cambio en evento, deleted = evento eliminado
        if resource_state not in ("exists", "deleted"):
            logger.warning("Estado de recurso desconocido", state=resource_state)
            return

        logger.info(
            "Notificación push de Google Calendar recibida",
            resource_state=resource_state,
            headers=headers,
        )

        # TODO: Implementar lógica de:
        # 1. Extraer google_event_id del payload
        # 2. Buscar Appointment por google_event_id en BD
        # 3. Si resource_state="deleted" → marcar appointment.status="cancelled"
        # 4. Notificar al paciente por WhatsApp

    except Exception as e:
        logger.error("Error manejando notificación push", error=str(e))


async def renew_watch_channels(
    db_engine: Any,
    calendar_services: Dict[str, Any],
    settings: Any,
) -> None:
    """
    Renueva los canales de watch de Google Calendar.
    Se ejecuta periódicamente via APScheduler para mantener webhooks activos.

    Args:
        db_engine: Engine de SQLAlchemy para acceder a settings
        calendar_services: Dict de servicios por doctor
        settings: Configuración de la app
    """
    try:
        if not settings.CALENDAR_WEBHOOK_ENABLED:
            logger.debug("Webhook deshabilitado, omitiendo renovación")
            return

        logger.info("Renovando canales de watch de Google Calendar")

        # TODO: Implementar lógica de:
        # 1. Iterar calendar_services por doctor
        # 2. Para cada servicio, obtener channel_id y resource_id desde BD
        # 3. Llamar watch_calendar() para renovar cada 24h

    except Exception as e:
        logger.error("Error renovando canales de watch", error=str(e))
