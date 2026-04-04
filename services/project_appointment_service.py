#!/usr/bin/env python3
"""
Servicio de gestión de citas multi-proyecto
Wrapea AppointmentService con configuración por proyecto
"""

from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime, timedelta, time
import structlog
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from db.models import ProjectAgentConfig, Appointment as AppointmentModel
from services.appointment_service import AppointmentService as BaseAppointmentService, TimeSlot
from services.google_calendar_service import GoogleCalendarService

logger = structlog.get_logger("appointment.service.project")


class ProjectAppointmentService:
    """
    Servicio de citas que respeta la configuración por proyecto
    """

    def __init__(
        self,
        project_config: ProjectAgentConfig,
        base_service: Optional[BaseAppointmentService] = None
    ):
        """
        Args:
            project_config: Configuración del agente para el proyecto
            base_service: Instancia base opcional (para reutilizar sesiones DB)
        """
        self.project_config = project_config
        self.project_id = project_config.project_id
        self.calendar_enabled = project_config.calendar_enabled
        self.calendar_mapping = project_config.calendar_mapping or {}
        self.calendar_timezone = project_config.calendar_timezone

        # Configurar duración por defecto
        self.appointment_duration_minutes = 30  # default

        # Inicializar Google Calendar si está habilitado
        self.google_calendar = None
        if self.calendar_enabled and project_config.google_calendar_id:
            try:
                # Crear servicio de Google Calendar para este proyecto
                # Nota: Necesitamos credenciales globales; el calendar_id es específico
                self.google_calendar = GoogleCalendarService(
                    calendar_id=project_config.google_calendar_id,
                    timezone=self.calendar_timezone
                )
                logger.info(
                    "Google Calendar inicializado para proyecto",
                    project_id=str(self.project_id),
                    calendar_id=project_config.google_calendar_id
                )
            except Exception as e:
                logger.warning(
                    "No se pudo inicializar Google Calendar para proyecto",
                    project_id=str(self.project_id),
                    error=str(e)
                )
                self.google_calendar = None

        logger.info(
            "ProjectAppointmentService inicializado",
            project_id=str(self.project_id),
            calendar_enabled=self.calendar_enabled
        )

    async def create_appointment(
        self,
        session: AsyncSession,
        phone_number: str,
        appointment_datetime: datetime,
        service_type: str,
        notes: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, Optional[AppointmentModel]]:
        """
        Crea una nueva cita con project_id

        Args:
            session: Sesión de SQLAlchemy
            phone_number: Número de teléfono del cliente
            appointment_datetime: Fecha y hora de la cita
            service_type: Tipo de servicio
            notes: Notas adicionales
            metadata: Metadatos adicionales

        Returns:
            Tuple[success, message, appointment]
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

            # 1.4 Calcular duración del servicio desde mapping
            duration_minutes = self._get_duration_for_service(service_type)
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
                    # Crear evento en Google Calendar
                    event = await self.google_calendar.create_event(
                        title=f"{service_type} - {phone_number}",
                        start_time=appointment_datetime,
                        end_time=end_datetime,
                        description=f"Servicio: {service_type}\nCliente: {phone_number}\nProyecto: {self.project_id}\nNotas: {notes or 'N/A'}",
                        attendees=[phone_number] if '@' in phone_number else None,
                        location="Clínica Dental"
                    )

                    google_event_id = event['id']
                    logger.info(
                        "Evento creado en Google Calendar",
                        event_id=google_event_id,
                        project_id=str(self.project_id)
                    )

                except Exception as e:
                    logger.error(
                        "Error creando evento en Google Calendar",
                        error=str(e),
                        project_id=str(self.project_id),
                        service=service_type
                    )
                    sync_status = "error"
            else:
                logger.debug("Google Calendar no habilitado para proyecto", project_id=str(self.project_id))

            # ============================================
            # PASO 4: CREAR CITA EN POSTGRESQL
            # ============================================
            appointment = AppointmentModel(
                project_id=self.project_id,
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
                project_id=str(self.project_id),
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
                project_id=str(self.project_id),
                phone_number=phone_number,
                error=str(e),
                exc_info=True
            )
            return False, f"Error interno: {str(e)}", None

    async def check_availability(
        self,
        session: AsyncSession,
        start_datetime: datetime,
        duration_minutes: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifica disponibilidad de un horario

        Estrategia:
        1. Si Google Calendar está habilitado: consultar Google PRIMERO
        2. Luego verificar DB local (citas no sincronizadas)
        3. Si Google NO habilitado: solo DB
        """
        duration = duration_minutes or self.appointment_duration_minutes
        end_datetime = start_datetime + timedelta(minutes=duration)

        # PASO 1: Verificar Google Calendar (si está habilitado)
        if self.google_calendar:
            try:
                is_google_available = await self.google_calendar.check_availability(
                    start_time=start_datetime,
                    end_time=end_datetime
                )

                if not is_google_available:
                    return False, "Horario no disponible en Google Calendar (conflicto existente)"

                logger.debug(
                    "Google Calendar disponible",
                    project_id=str(self.project_id),
                    start=start_datetime.isoformat(),
                    end=end_datetime.isoformat()
                )

            except Exception as e:
                logger.error(
                    "Error verificando Google Calendar, continuando con DB",
                    project_id=str(self.project_id),
                    error=str(e)
                )

        # PASO 2: Verificar DB local (citas no sincronizadas o DB-only)
        from db.models import Appointment as AppointmentModel
        from sqlalchemy import select, and_

        stmt = select(AppointmentModel).where(
            and_(
                AppointmentModel.project_id == self.project_id,
                AppointmentModel.status == "scheduled",
                or_(
                    and_(
                        AppointmentModel.appointment_date <= start_datetime,
                        AppointmentModel.appointment_date + timedelta(minutes=duration) > start_datetime
                    ),
                    and_(
                        AppointmentModel.appointment_date < end_datetime,
                        AppointmentModel.appointment_date + timedelta(minutes=duration) > end_datetime
                    ),
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
        Obtiene slots disponibles en una fecha para este proyecto
        """
        duration = duration_minutes or self.appointment_duration_minutes

        # Normalizar al día completo
        day_start = datetime.combine(
            date.date(),
            self._get_business_hours_start_time()
        )
        day_end = datetime.combine(
            date.date(),
            self._get_business_hours_end_time()
        )

        available_slots = []

        # ESTRATEGIA A: Google Calendar habilitado
        if self.google_calendar:
            try:
                google_slots = await self.google_calendar.get_available_slots(
                    date=date,
                    duration_minutes=duration,
                    start_hour=self._get_business_hours_start_hour(),
                    end_hour=self._get_business_hours_end_hour()
                )

                google_available = [
                    TimeSlot(
                        start=slot['start'],
                        end=slot['end'],
                        available=True
                    )
                    for slot in google_slots
                ]

                # Filtrar citas no sincronizadas en DB
                from db.models import Appointment as AppointmentModel
                from sqlalchemy import select, and_

                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.project_id == self.project_id,
                        AppointmentModel.status == "scheduled",
                        AppointmentModel.appointment_date >= day_start,
                        AppointmentModel.appointment_date < day_end,
                        AppointmentModel.sync_status != 'synced'
                    )
                )
                result = await session.execute(stmt)
                unsynced_appointments = result.scalars().all()

                unsynced_slots = set()
                for appt in unsynced_appointments:
                    slot_start = appt.appointment_date.replace(
                        minute=(appt.appointment_date.minute // duration) * duration,
                        second=0,
                        microsecond=0
                    )
                    unsynced_slots.add(slot_start)

                final_slots = [
                    slot for slot in google_available
                    if slot.start.replace(tzinfo=None) not in unsynced_slots
                ]

                logger.info(
                    "Slots calculados (Google + DB filter)",
                    project_id=str(self.project_id),
                    date=date.date().isoformat(),
                    google_total=len(google_available),
                    unsynced=len(unsynced_slots),
                    final=len(final_slots)
                )

                return final_slots

            except Exception as e:
                logger.error(
                    "Error obteniendo slots desde Google Calendar, fallback a DB",
                    project_id=str(self.project_id),
                    error=str(e)
                )

        # ESTRATEGIA B: Solo DB
        from db.models import Appointment as AppointmentModel
        from sqlalchemy import select, and_

        stmt = select(AppointmentModel).where(
            and_(
                AppointmentModel.project_id == self.project_id,
                AppointmentModel.status == "scheduled",
                AppointmentModel.appointment_date >= day_start,
                AppointmentModel.appointment_date < day_end
            )
        )

        result = await session.execute(stmt)
        booked_appointments = result.scalars().all()

        booked_slots = set()
        for appt in booked_appointments:
            slot_start = appt.appointment_date
            booked_slots.add(slot_start)

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
            project_id=str(self.project_id),
            date=date.date().isoformat(),
            total=len(available_slots),
            booked=len(booked_slots)
        )

        return available_slots

    async def get_appointments_by_phone(
        self,
        session: AsyncSession,
        phone_number: str,
        status: Optional[str] = None,
        upcoming_only: bool = True
    ) -> List[AppointmentModel]:
        """
        Obtiene citas de un cliente para este proyecto
        """
        from db.models import Appointment as AppointmentModel
        from sqlalchemy import select

        stmt = select(AppointmentModel).where(
            and_(
                AppointmentModel.project_id == self.project_id,
                AppointmentModel.phone_number == phone_number
            )
        )

        if status:
            stmt = stmt.where(AppointmentModel.status == status)
        else:
            stmt = stmt.where(AppointmentModel.status == "scheduled")

        if upcoming_only:
            now = datetime.utcnow()
            stmt = stmt.where(AppointmentModel.appointment_date >= now)

        stmt = stmt.order_by(AppointmentModel.appointment_date)

        result = await session.execute(stmt)
        appointments = result.scalars().all()

        logger.info(
            "Citas obtenidas",
            project_id=str(self.project_id),
            phone_number=phone_number,
            count=len(appointments),
            upcoming_only=upcoming_only
        )

        return list(appointments)

    def _is_business_hours(self, dt: datetime) -> bool:
        """Verifica si un datetime está en horario laboral"""
        hour = dt.hour
        minute = dt.minute

        start = self._get_business_hours_start_hour()
        end = self._get_business_hours_end_hour()

        if hour < start or hour >= end:
            return False
        if hour == end and minute > 0:
            return False

        return True

    def _get_business_hours_start_hour(self) -> int:
        """Hora de inicio laboral (por defecto 9)"""
        return 9

    def _get_business_hours_end_hour(self) -> int:
        """Hora de fin laboral (por defecto 18)"""
        return 18

    def _get_business_hours_start_time(self) -> time:
        """Devuelve time object para inicio"""
        return time(hour=self._get_business_hours_start_hour(), minute=0)

    def _get_business_hours_end_time(self) -> time:
        """Devuelve time object para fin"""
        return time(hour=self._get_business_hours_end_hour(), minute=0)

    def _get_duration_for_service(self, service_type: str) -> int:
        """
        Obtiene duración para un servicio desde calendar_mapping
        """
        try:
            # Usar calendar_mapping del proyecto
            mapping = self.calendar_mapping.get(service_type)
            if mapping:
                return int(mapping.get('duration', 30))
        except Exception:
            pass

        # Default
        return self.appointment_duration_minutes
