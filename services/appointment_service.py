#!/usr/bin/env python3
"""
Servicio de gestión de citas
Reemplaza la lógica de n8n para agendamiento

Integra Google Calendar como fuente de verdad para disponibilidad
y sincronización bidireccional.
"""

from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime, timedelta, time
from dataclasses import dataclass
import structlog
import uuid
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Appointment as AppointmentModel
from core.config import get_settings
from config.calendar_mapping import (
    get_duration_for_service,
    get_dentist_for_service,
    get_service_from_keyword
)
from services.google_calendar_service import (
    GoogleCalendarService,
    get_calendar_service_for_odontologist
)

logger = structlog.get_logger("appointment.service")


@dataclass
class TimeSlot:
    """Representa un horario disponible"""
    start: datetime
    end: datetime
    available: bool = True
    reason: Optional[str] = None


class AppointmentService:
    """
    Servicio para gestión de citas
    Opera sobre PostgreSQL con SQLAlchemy async

    Si google_calendar_service se provee, se sincroniza automáticamente.
    """

    def __init__(
        self,
        settings=None,
        google_calendar_service: Optional[GoogleCalendarService] = None
    ):
        self.settings = settings or get_settings()
        self.appointment_duration_minutes = 60  # Duración estándar (se sobreescribe por servicio)
        self.business_hours_start = 9  # 9 AM
        self.business_hours_end = 18  # 6 PM

        # Servicio de Google Calendar (opcional)
        self.google_calendar = google_calendar_service

        logger.info(
            "AppointmentService inicializado",
            google_enabled=bool(google_calendar_service)
        )

    async def create_appointment(
        self,
        session: AsyncSession,
        phone_number: str,
        appointment_datetime: datetime,
        service_type: str,
        project_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, Optional[AppointmentModel]]:
        """
        Crea una nueva cita

        Flujo:
        1. Validar fecha/hora (futura, laboral, Lun-Vie)
        2. Verificar disponibilidad en Google Calendar + DB
        3. Si Google Calendar está habilitado: CREAR evento en Google
        4. Guardar cita en PostgreSQL con google_event_id
        5. Sincronización bidireccional

        Args:
            session: Sesión de SQLAlchemy
            phone_number: Número de teléfono del cliente
            appointment_datetime: Fecha y hora de la cita
            service_type: Tipo de servicio (nombre oficial)
            project_id: ID del proyecto (opcional, puede ser None para modo legacy)
            notes: Notas adicionales
            metadata: Metadatos adicionales

        Returns:
            Tuple[success: bool, message: str, appointment: Optional[Appointment]]
        """
        try:
            # ============================================
            # PASO 1: VALIDACIONES
            # ============================================

            # 1.1 ¿Fecha en el pasado?
            now = datetime.now(appointment_datetime.tzinfo)
            if appointment_datetime < now:
                return False, "No puedes agendar en el pasado. Por favor elige una fecha futura.", None

            # 1.2 ¿Horario laboral?
            if not self._is_business_hours(appointment_datetime):
                return False, "La cita debe estar en horario laboral (9:00-18:00)", None

            # 1.3 ¿Día de la semana? (Lun-Vie)
            if appointment_datetime.weekday() >= 5:
                return False, "Las citas solo se agendan de lunes a viernes", None

            # 1.4 Calcular duración del servicio
            try:
                duration_minutes = get_duration_for_service(service_type)
            except ValueError:
                # Si no está en mapeo, usar default
                duration_minutes = self.appointment_duration_minutes
                logger.warning(
                    "Duración no encontrada en mapeo, usando default",
                    service=service_type,
                    duration=duration_minutes
                )

            # Calcular end time
            end_datetime = appointment_datetime + timedelta(minutes=duration_minutes)

            # ============================================
            # PASO 2: VERIFICAR DISPONIBILIDAD
            # ============================================

            is_available, conflict_reason = await self.check_availability(
                session,
                appointment_datetime,
                duration_minutes=duration_minutes
            )

            if not is_available:
                return False, f"Horario no disponible: {conflict_reason}", None

            # ============================================
            # PASO 3: CREAR EVENTO EN GOOGLE CALENDAR (si está habilitado)
            # ============================================
            google_event_id = None
            sync_status = "synced"

            if self.google_calendar:
                try:
                    # Determinar qué calendario usar basado en servicio
                    calendar_id = self._get_calendar_id_for_service(service_type)

                    # Crear evento en Google
                    event = await self.google_calendar.create_event(
                        title=f"{service_type} - {phone_number}",
                        start_time=appointment_datetime,
                        end_time=end_datetime,
                        description=f"Servicio: {service_type}\nCliente: {phone_number}\nNotas: {notes or 'N/A'}",
                        attendees=[phone_number] if '@' in phone_number else None,
                        location="Clínica Dental"
                    )

                    google_event_id = event['id']
                    logger.info(
                        "Evento creado en Google Calendar",
                        event_id=google_event_id,
                        calendar=calendar_id,
                        html_link=event.get('htmlLink')
                    )

                except Exception as e:
                    # Si falla Google, igual creamos en DB pero marcamos error
                    logger.error(
                        "Error creando evento en Google Calendar",
                        error=str(e),
                        service=service_type
                    )
                    sync_status = "error"
                    # NO fallamos la creación, continuamos con DB
            else:
                logger.debug("Google Calendar no habilitado, solo creando en DB")

            # ============================================
            # PASO 4: CREAR CITA EN POSTGRESQL
            # ============================================
            appointment = AppointmentModel(
                project_id=project_id,
                phone_number=phone_number,
                appointment_date=appointment_datetime,
                service_type=service_type,
                status="scheduled",
                notes=notes,
                metadata=metadata or {},
                google_event_id=google_event_id,
                sync_status=sync_status
            )

            session.add(appointment)
            await session.flush()
            await session.commit()

            logger.info(
                "Cita creada exitosamente",
                phone_number=phone_number,
                appointment_datetime=appointment_datetime.isoformat(),
                appointment_id=str(appointment.id),
                google_event_id=google_event_id,
                sync_status=sync_status
            )

            # Construir mensaje de confirmación
            message = f"Cita agendada exitosamente para {appointment_datetime.strftime('%d/%m/%Y %H:%M')} ({duration_minutes} min). Servicio: {service_type}"

            if google_event_id and self.google_calendar:
                message += f"\n📅 Link del evento: {event.get('htmlLink', 'No disponible')}"

            return True, message, appointment

        except Exception as e:
            logger.error(
                "Error creando cita",
                phone_number=phone_number,
                error=str(e),
                exc_info=True
            )
            return False, f"Error interno: {str(e)}", None

    def _get_calendar_id_for_service(self, service_type: str) -> str:
        """
        Determina qué calendar_id usar para un servicio.

        Args:
            service_type: Nombre oficial del servicio

        Returns:
            Calendar ID (email)
        """
        try:
            return get_dentist_for_service(service_type)
        except ValueError:
            # Si no está en mapeo, usar default
            default_id = self.settings.GOOGLE_CALENDAR_DEFAULT_ID
            logger.warning(
                "Servicio no tiene odontólogo asignado, usando default",
                service=service_type,
                default=default_id
            )
            return default_id

    async def check_availability(
        self,
        session: AsyncSession,
        start_datetime: datetime,
        duration_minutes: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifica disponibilidad de un horario

        Estrategia de verificación (prioridades):
        1. Si Google Calendar está habilitado: consultar Google PRIMERO (source of truth)
        2. Luego verificar DB local (por si hay citas no sincronizadas)

        Args:
            session: Sesión de SQLAlchemy
            start_datetime: Fecha y hora de inicio
            duration_minutes: Duración en minutos (default: 30)

        Returns:
            Tuple[available: bool, reason: Optional[str]]
        """
        duration = duration_minutes or self.appointment_duration_minutes
        end_datetime = start_datetime + timedelta(minutes=duration)

        # ============================================
        # PASO 1: Verificar Google Calendar (si está habilitado)
        # ============================================
        if self.google_calendar:
            try:
                # Determinar calendar_id basado en fecha? Necesitamos saber el odontólogo.
                # Por implementación simple, usamos default calendar por ahora.
                calendar_id = self.settings.GOOGLE_CALENDAR_DEFAULT_ID

                # Verificar disponibilidad en Google
                is_google_available = await self.google_calendar.check_availability(
                    start_time=start_datetime,
                    end_time=end_datetime
                )

                if not is_google_available:
                    return False, "Horario no disponible en Google Calendar (conflicto existente)"

                logger.debug(
                    "Google Calendar disponible",
                    start=start_datetime.isoformat(),
                    end=end_datetime.isoformat()
                )

            except Exception as e:
                logger.error(
                    "Error verificando Google Calendar, continuando con DB",
                    error=str(e)
                )
                # Si falla Google, continuamos con DB como fallback

        # ============================================
        # PASO 2: Verificar DB local (citas no sincronizadas o DB-only)
        # ============================================
        stmt = select(AppointmentModel).where(
            and_(
                AppointmentModel.status == "scheduled",
                or_(
                    # Nueva cita empieza durante cita existente
                    and_(
                        AppointmentModel.appointment_date <= start_datetime,
                        AppointmentModel.appointment_date + timedelta(minutes=duration) > start_datetime
                    ),
                    # Nueva cita termina durante cita existente
                    and_(
                        AppointmentModel.appointment_date < end_datetime,
                        AppointmentModel.appointment_date + timedelta(minutes=duration) > end_datetime
                    ),
                    # Nueva cita contiene cita existente
                    and_(
                        AppointmentModel.appointment_date >= start_datetime,
                        AppointmentModel.appointment_date + timedelta(minutes=duration) <= end_datetime
                    )
                )
            )
        )

        result = await session.execute(stmt)
        conflicting = result.scalars().all()

        if conflicting:
            conflict = conflicting[0]
            return False, f"Conflicto con cita existente en DB el {conflict.appointment_date}"

        return True, None

    async def get_available_slots(
        self,
        session: AsyncSession,
        date: datetime,
        duration_minutes: Optional[int] = None
    ) -> List[TimeSlot]:
        """
        Obtiene slots disponibles en una fecha

        Estrategia híbrida:
        1. Si Google Calendar está habilitado: consultar slots libres de Google
        2. Cruzar con DB local: excluir citas pendientes de sincronización
        3. Si Google NO habilitado: usar solo DB local

        Args:
            session: Sesión de SQLAlchemy
            date: Fecha a consultar (solo el día)
            duration_minutes: Duración de cada slot

        Returns:
            Lista de slots disponibles
        """
        duration = duration_minutes or self.appointment_duration_minutes

        # Normalizar al día completo
        day_start = datetime.combine(
            date.date(),
            time(hour=self.business_hours_start)
        )
        day_end = datetime.combine(
            date.date(),
            time(hour=self.business_hours_end)
        )

        available_slots = []

        # ============================================
        # ESTRATEGIA A: Google Calendar habilitado
        # ============================================
        if self.google_calendar:
            try:
                # Obtener slots libres de Google
                google_slots = await self.google_calendar.get_available_slots(
                    date=date,
                    duration_minutes=duration,
                    start_hour=self.business_hours_start,
                    end_hour=self.business_hours_end
                )

                # Convertir a TimeSlot objects
                google_available = [
                    TimeSlot(
                        start=slot['start'],
                        end=slot['end'],
                        available=True
                    )
                    for slot in google_slots
                ]

                # Ahora Chequear DB por citas NO sincronizadas (sync_status != 'synced')
                # Estas podrían no estar en Google y por tanto bloquear slots
                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.status == "scheduled",
                        AppointmentModel.appointment_date >= day_start,
                        AppointmentModel.appointment_date < day_end,
                        AppointmentModel.sync_status != 'synced'  # Solo las no sincronizadas
                    )
                )
                result = await session.execute(stmt)
                unsynced_appointments = result.scalars().all()

                # Convertir a set de start times
                unsynced_slots = set()
                for appt in unsynced_appointments:
                    # Redondear al slot más cercano
                    slot_start = appt.appointment_date.replace(
                        minute=(appt.appointment_date.minute // duration) * duration,
                        second=0,
                        microsecond=0
                    )
                    unsynced_slots.add(slot_start)

                # Filtrar: quitar slots que estén ocupados por DB no-sync
                final_slots = [
                    slot for slot in google_available
                    if slot.start.replace(tzinfo=None) not in unsynced_slots
                ]

                logger.info(
                    "Slots calculados (Google + DB filter)",
                    date=date.date().isoformat(),
                    google_total=len(google_available),
                    unsynced=len(unsynced_slots),
                    final=len(final_slots)
                )

                return final_slots

            except Exception as e:
                logger.error(
                    "Error obteniendo slots desde Google Calendar, fallback a DB",
                    error=str(e)
                )
                # Fallback a estrategia B

        # ============================================
        # ESTRATEGIA B: Solo DB (fallback o DB-only)
        # ============================================
        stmt = select(AppointmentModel).where(
            and_(
                AppointmentModel.status == "scheduled",
                AppointmentModel.appointment_date >= day_start,
                AppointmentModel.appointment_date < day_end
            )
        )

        result = await session.execute(stmt)
        booked_appointments = result.scalars().all()

        # Convertir a slots ocupados
        booked_slots = set()
        for appt in booked_appointments:
            slot_start = appt.appointment_date
            booked_slots.add(slot_start)

        # Generar todos los slots posibles
        available_slots = []
        current = day_start
        while current + timedelta(minutes=duration) <= day_end:
            if current not in booked_slots:
                slot = TimeSlot(
                    start=current,
                    end=current + timedelta(minutes=duration)
                )
                available_slots.append(slot)
            current += timedelta(minutes=duration)

        logger.info(
            "Slots calculados (solo DB)",
            date=date.date().isoformat(),
            total=len(available_slots),
            booked=len(booked_slots)
        )

        return available_slots

        logger.info(
            "Slots disponibles obtenidos",
            date=date.date().isoformat(),
            total_slots=len(available_slots),
            booked_slots=len(booked_slots)
        )

        return available_slots

    async def cancel_appointment(
        self,
        session: AsyncSession,
        appointment_id: uuid.UUID
    ) -> Tuple[bool, str]:
        """
        Cancela una cita

        Flujo:
        1. Buscar cita en DB
        2. Si tiene google_event_id: eliminar evento en Google Calendar
        3. Actualizar status en DB a "cancelled"

        Args:
            session: Sesión de SQLAlchemy
            appointment_id: ID de la cita (UUID)

        Returns:
            Tuple[success: bool, message: str]
        """
        try:
            stmt = select(AppointmentModel).where(
                and_(
                    AppointmentModel.id == appointment_id,
                    AppointmentModel.status == "scheduled"
                )
            )

            result = await session.execute(stmt)
            appointment = result.scalar_one_or_none()

            if not appointment:
                return False, "Cita no encontrada o ya cancelada"

            # ============================================
            # PASO 1: Eliminar evento en Google Calendar (si existe)
            # ============================================
            if self.google_calendar and appointment.google_event_id:
                try:
                    success = await self.google_calendar.delete_event(
                        appointment.google_event_id
                    )
                    if success:
                        logger.info(
                            "Evento eliminado de Google Calendar",
                            event_id=appointment.google_event_id
                        )
                    else:
                        logger.warning(
                            "No se pudo eliminar evento de Google",
                            event_id=appointment.google_event_id
                        )
                except Exception as e:
                    logger.error(
                        "Error eliminando evento en Google Calendar",
                        event_id=appointment.google_event_id,
                        error=str(e)
                    )
                    # Continuamos igual, cancelamos en DB

            # ============================================
            # PASO 2: Actualizar DB
            # ============================================
            appointment.status = "cancelled"
            await session.flush()
            await session.commit()

            logger.info(
                "Cita cancelada",
                appointment_id=str(appointment_id),
                google_event_id=appointment.google_event_id
            )

            message = "Cita cancelada exitosamente"
            if appointment.google_event_id:
                message += " ( evento en Google Calendar eliminado )"

            return True, message

        except Exception as e:
            logger.error(
                "Error cancelando cita",
                appointment_id=str(appointment_id),
                error=str(e),
                exc_info=True
            )
            return False, f"Error interno: {str(e)}"

    async def get_appointments_by_phone(
        self,
        session: AsyncSession,
        phone_number: str,
        status: Optional[str] = None,
        upcoming_only: bool = True
    ) -> List[AppointmentModel]:
        """
        Obtiene citas de un cliente

        Args:
            session: Sesión de SQLAlchemy
            phone_number: Número de teléfono
            status: Filtrar por estado (opcional)
            upcoming_only: Si True, solo devuelve citas con fecha >= ahora

        Returns:
            Lista de citas ordenadas por fecha ascendente
        """
        stmt = select(AppointmentModel).where(
            AppointmentModel.phone_number == phone_number
        )

        if status:
            stmt = stmt.where(AppointmentModel.status == status)
        else:
            # Por defecto, solo scheduled (próximas)
            stmt = stmt.where(AppointmentModel.status == "scheduled")

        if upcoming_only:
            now = datetime.utcnow()
            stmt = stmt.where(AppointmentModel.appointment_date >= now)

        stmt = stmt.order_by(AppointmentModel.appointment_date)

        result = await session.execute(stmt)
        appointments = result.scalars().all()

        logger.info(
            "Citas obtenidas",
            phone_number=phone_number,
            count=len(appointments),
            upcoming_only=upcoming_only
        )

        return list(appointments)

    def _is_business_hours(self, dt: datetime) -> bool:
        """Verifica si un datetime está en horario laboral"""
        hour = dt.hour
        minute = dt.minute

        # Horario laboral: 9:00 a 18:00
        if hour < self.business_hours_start or hour >= self.business_hours_end:
            return False

        # Permitir slot completo hasta las 18:00
        if hour == self.business_hours_end and minute > 0:
            return False

        return True

    def get_next_working_day(self, date: datetime) -> datetime:
        """
        Obtiene el siguiente día laboral (omite fines de semana)

        Args:
            date: Fecha de referencia

        Returns:
            Siguiente día laboral
        """
        days_ahead = 1
        while (date + timedelta(days=days_ahead)).weekday() >= 5:
            days_ahead += 1
        return date + timedelta(days=days_ahead)
