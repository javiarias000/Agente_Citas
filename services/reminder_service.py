#!/usr/bin/env python3
"""
Servicio de recordatorios automáticos 24h antes de cita.
Se ejecuta periódicamente via APScheduler.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from db.models import Appointment as AppointmentModel
from services.whatsapp_service import WhatsAppService

logger = structlog.get_logger("reminder_service")


async def send_appointment_reminders(
    db_url: str,
    whatsapp_service: WhatsAppService,
) -> int:
    """
    Envía recordatorios 24h antes de citas programadas.

    Args:
        db_url: Database URL (postgresql+asyncpg://...)
        whatsapp_service: Instancia de WhatsAppService

    Returns:
        Número de recordatorios enviados
    """
    sent_count = 0

    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            now = datetime.now(timezone.utc)
            window_start = now + timedelta(hours=24)
            window_end = now + timedelta(hours=26)

            stmt = select(AppointmentModel).where(
                and_(
                    AppointmentModel.status == "scheduled",
                    AppointmentModel.reminder_sent_at.is_(None),
                    AppointmentModel.appointment_date >= window_start,
                    AppointmentModel.appointment_date <= window_end,
                )
            )

            result = await session.execute(stmt)
            appointments = result.scalars().all()

            logger.info(
                "Recordatorios: citas encontradas",
                count=len(appointments)
            )

            for appt in appointments:
                try:
                    # Marcar como enviado ANTES de intentar enviar (evita doble envío)
                    appt.reminder_sent_at = now
                    await session.flush()

                    # Construcción del mensaje
                    appt_time = appt.appointment_date.strftime("%H:%M")
                    message = (
                        f"Hola, recuerda tu cita de {appt.service_type} "
                        f"mañana a las {appt_time}. 😊"
                    )

                    # Enviar por WhatsApp (sin excepción = silencio)
                    await whatsapp_service.send_text(
                        phone_number=appt.phone_number,
                        text=message
                    )

                    sent_count += 1
                    logger.info(
                        "Recordatorio enviado",
                        phone=appt.phone_number,
                        appointment_id=str(appt.id)
                    )

                except Exception as e:
                    # Falla silenciosa por cita individual
                    logger.error(
                        "Error enviando recordatorio",
                        phone=appt.phone_number,
                        appointment_id=str(appt.id),
                        error=str(e)
                    )
                    # No re-raise: siguiente cita continúa

            await session.commit()

    except Exception as e:
        logger.error("Error en send_appointment_reminders", error=str(e))
    finally:
        await engine.dispose()

    return sent_count
