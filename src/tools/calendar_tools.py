"""
Herramientas de calendario para el agente ReAct V2.

Todas las tools reciben los servicios vía closure (factory pattern).
No usan InjectedToolArg — son funciones puras que el LLM invoca con argumentos explícitos.

Uso:
    tools = [
        make_check_availability_tool(calendar_service),
        make_book_appointment_tool(calendar_service, db_service),
        ...
    ]
    llm_with_tools = llm.bind_tools(tools)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import structlog
from langchain_core.tools import tool

from src.schemas_v2 import (
    CheckAvailabilityResult,
    SlotInfo,
    BookAppointmentResult,
    CancelAppointmentResult,
    LookupAppointmentsResult,
    AppointmentInfo,
    RescheduleAppointmentResult,
)

logger = structlog.get_logger("tools.calendar")

TIMEZONE = ZoneInfo("America/Guayaquil")

SERVICE_DURATIONS = {
    "consulta": 60,
    "limpieza": 60,
    "empaste": 60,
    "extraccion": 60,
    "endodoncia": 90,
    "ortodoncia": 60,
    "cirugia": 120,
    "implantes": 90,
    "estetica": 60,
    "odontopediatria": 60,
    "blanqueamiento": 60,
    "revision": 60,
}

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _format_slot_display(dt: datetime) -> str:
    dia = DIAS_ES[dt.weekday()]
    return f"{dia} {dt.strftime('%d/%m')} a las {dt.strftime('%H:%M')}"


def _adjust_weekend(dt: datetime) -> tuple[datetime, bool]:
    """Si es sábado (5) o domingo (6), mover al siguiente lunes."""
    if dt.weekday() == 5:
        return dt + timedelta(days=2), True
    if dt.weekday() == 6:
        return dt + timedelta(days=1), True
    return dt, False


# ── Tool 1: check_availability ────────────────────────────────────────────────

def make_check_availability_tool(calendar_service):
    @tool
    async def check_availability(
        date_iso: str,
        service: Literal[
            "consulta", "limpieza", "empaste", "extraccion", "endodoncia",
            "ortodoncia", "cirugia", "implantes", "estetica",
            "odontopediatria", "blanqueamiento", "revision"
        ],
    ) -> CheckAvailabilityResult:
        """
        Consulta los horarios disponibles en Google Calendar para una fecha dada.
        Retorna hasta 8 slots libres en jornada laboral (9:00–18:00 Ecuador).
        Ajusta automáticamente sábado/domingo al siguiente lunes.

        Llama esta tool SIEMPRE antes de ofrecer horarios al paciente o agendar.
        NO inventes horarios — usa los que esta tool devuelva.

        Args:
            date_iso: Fecha en formato YYYY-MM-DD o YYYY-MM-DDTHH:MM.
                      Usa la fecha calculada del contexto del sistema para
                      referencias como "mañana", "el viernes", etc.
            service:  Tipo de servicio solicitado (determina la duración del slot).
        """
        if not calendar_service:
            return CheckAvailabilityResult(
                success=False,
                error="Servicio de calendario no disponible. Llame a la clínica. 📞"
            )

        try:
            # Parsear fecha
            try:
                dt = datetime.fromisoformat(date_iso)
            except ValueError:
                # Intentar solo fecha
                dt = datetime.fromisoformat(date_iso.split("T")[0])

            # Ajustar zona horaria
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TIMEZONE)

            # Ajustar fin de semana
            dt, adjusted = _adjust_weekend(dt)

            duration = SERVICE_DURATIONS.get(service, 60)
            now_ec = datetime.now(TIMEZONE)

            raw_slots = await calendar_service.get_available_slots(
                date=dt,
                duration_minutes=duration,
            )

            slots_out: list[SlotInfo] = []
            for s in raw_slots:
                # Normalizar a datetime
                if isinstance(s, dict):
                    start = s.get("start")
                    if isinstance(start, datetime):
                        slot_dt = start
                    else:
                        try:
                            slot_dt = datetime.fromisoformat(str(start))
                        except ValueError:
                            continue
                elif isinstance(s, str):
                    try:
                        slot_dt = datetime.fromisoformat(s)
                    except ValueError:
                        continue
                else:
                    slot_dt = s

                # Añadir tzinfo si falta
                if slot_dt.tzinfo is None:
                    slot_dt = slot_dt.replace(tzinfo=TIMEZONE)

                # Filtrar slots pasados
                if slot_dt <= now_ec:
                    continue

                slots_out.append(SlotInfo(
                    iso=slot_dt.isoformat(),
                    display=_format_slot_display(slot_dt),
                ))

            logger.info(
                "check_availability",
                date=dt.date().isoformat(),
                service=service,
                available=len(slots_out),
                adjusted=adjusted,
            )

            if not slots_out:
                return CheckAvailabilityResult(
                    success=False,
                    duration_minutes=duration,
                    date_adjusted=adjusted,
                    error="No hay horarios disponibles para esa fecha. Prueba otro día.",
                )

            return CheckAvailabilityResult(
                success=True,
                slots=slots_out[:8],
                duration_minutes=duration,
                date_adjusted=adjusted,
            )

        except Exception as e:
            logger.error("check_availability error", error=str(e))
            return CheckAvailabilityResult(
                success=False,
                error=f"Error consultando disponibilidad: {e}",
            )

    return check_availability


# ── Tool 2: book_appointment ──────────────────────────────────────────────────

def make_book_appointment_tool(calendar_service, db_service):
    @tool
    async def book_appointment(
        slot_iso: str,
        service: Literal[
            "consulta", "limpieza", "empaste", "extraccion", "endodoncia",
            "ortodoncia", "cirugia", "implantes", "estetica",
            "odontopediatria", "blanqueamiento", "revision"
        ],
        patient_name: str,
        phone_number: str,
    ) -> BookAppointmentResult:
        """
        Agenda una cita dental en Google Calendar y la registra en la base de datos.

        IMPORTANTE: Solo llamar DESPUÉS de que el paciente haya confirmado EXPLÍCITAMENTE
        el horario. Si el paciente todavía está eligiendo, NO llames esta tool.
        El turno de confirmación es cuando el paciente dice "sí", "esa hora está bien",
        "confirmo", o similar DESPUÉS de que mostraste los slots.

        Args:
            slot_iso:     Slot exacto confirmado, en ISO con timezone Ecuador.
                          Debe ser uno de los slots que check_availability devolvió.
            service:      Servicio a agendar.
            patient_name: Nombre completo del paciente (obligatorio para el evento).
            phone_number: Número de teléfono del paciente (viene del contexto del sistema).
        """
        if not calendar_service:
            return BookAppointmentResult(
                success=False,
                error="Servicio de calendario no disponible. Llame a la clínica. 📞"
            )

        try:
            dt = datetime.fromisoformat(slot_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TIMEZONE)

            duration = SERVICE_DURATIONS.get(service, 60)
            end_dt = dt + timedelta(minutes=duration)

            # R2 — Re-verificar disponibilidad antes de crear evento.
            # El slot puede haberse tomado entre que se mostró al paciente y que confirmó.
            try:
                fresh_slots = await calendar_service.get_available_slots(
                    date=dt, duration_minutes=duration
                )
                time_key = f"T{dt.hour:02d}:{dt.minute:02d}"
                if fresh_slots and not any(time_key in s for s in fresh_slots):
                    logger.warning(
                        "book_appointment: slot ya no disponible",
                        slot=slot_iso,
                        fresh_slots=fresh_slots[:4],
                    )
                    alternatives = ", ".join(
                        s[11:16] for s in fresh_slots[:3]
                    ) if fresh_slots else "ninguno"
                    return BookAppointmentResult(
                        success=False,
                        error=(
                            f"El horario de las {dt.strftime('%H:%M')} ya fue tomado. "
                            f"Horarios disponibles: {alternatives}. "
                            "Por favor elija otro."
                        ),
                    )
            except Exception as e:
                # Si falla la re-verificación, continuar con el intento de booking
                logger.warning("book_appointment: error en re-verificación (continuando)", error=str(e))

            logger.info(
                "book_appointment: llamando create_event",
                patient=patient_name,
                service=service,
                start=dt.isoformat(),
            )

            event_id, event_link = await calendar_service.create_event(
                start=dt,
                end=end_dt,
                title=f"{service} - {patient_name}",
                description=f"Paciente: {patient_name}\nTeléfono: {phone_number}",
            )

            if not event_id:
                return BookAppointmentResult(
                    success=False,
                    error="Error confirmando en Google Calendar (sin ID). Llame a la clínica. 📞",
                )

            logger.info(
                "book_appointment: evento creado",
                event_id=event_id,
                patient=patient_name,
                service=service,
            )

            # Persistir en DB (no crítico)
            appt_id = None
            if db_service:
                try:
                    from db import get_async_session
                    async with get_async_session() as session:
                        success, _msg, appt = await db_service.create_appointment(
                            session=session,
                            phone_number=phone_number,
                            appointment_datetime=dt,
                            service_type=service,
                            metadata={"google_event_id": event_id, "patient_name": patient_name},
                        )
                        if appt:
                            appt_id = str(appt.id)
                except Exception as e:
                    logger.warning("book_appointment: error DB (no crítico)", error=str(e))

            return BookAppointmentResult(
                success=True,
                event_id=event_id,
                event_link=event_link,
                appointment_id=appt_id or f"gcal_{event_id}",
                slot_iso=dt.isoformat(),
                service=service,
                patient_name=patient_name,
            )

        except Exception as e:
            logger.error("book_appointment error", error=str(e))
            return BookAppointmentResult(
                success=False,
                error=f"Error agendando cita: {e}",
            )

    return book_appointment


# ── Tool 3: cancel_appointment ────────────────────────────────────────────────

def make_cancel_appointment_tool(calendar_service, db_service):
    @tool
    async def cancel_appointment(
        event_id: str,
        phone_number: str,
        reason: Optional[str] = None,
    ) -> CancelAppointmentResult:
        """
        Cancela una cita existente en Google Calendar.

        IMPORTANTE: Solo llamar DESPUÉS de que el paciente confirme que quiere cancelar.
        Primero usa lookup_appointments para obtener el event_id.

        Args:
            event_id:     ID del evento en Google Calendar (de lookup_appointments).
            phone_number: Teléfono del paciente.
            reason:       Razón de cancelación (opcional, para logs).
        """
        try:
            if calendar_service and event_id:
                await calendar_service.delete_event(event_id)
                logger.info("cancel_appointment: evento eliminado", event_id=event_id)

            # Cancelar en DB si hay appointment_id
            if db_service:
                try:
                    from db import get_async_session
                    async with get_async_session() as session:
                        from sqlalchemy import text
                        await session.execute(
                            text("UPDATE appointments SET status='cancelled' "
                                 "WHERE metadata->>'google_event_id' = :eid"),
                            {"eid": event_id},
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning("cancel_appointment: error DB (no crítico)", error=str(e))

            return CancelAppointmentResult(success=True, event_id=event_id)

        except Exception as e:
            logger.error("cancel_appointment error", error=str(e))
            return CancelAppointmentResult(
                success=False,
                error=f"Error cancelando cita: {e}",
            )

    return cancel_appointment


# ── Tool 4: lookup_appointments ───────────────────────────────────────────────

def make_lookup_appointments_tool(calendar_service):
    @tool
    async def lookup_appointments(
        phone_number: str,
        patient_name: Optional[str] = None,
        days_ahead: int = 60,
    ) -> LookupAppointmentsResult:
        """
        Busca las citas existentes del paciente en Google Calendar.

        Úsala cuando el paciente quiera cancelar, reagendar o consultar sus citas.
        Busca por teléfono y nombre en los próximos `days_ahead` días.

        Args:
            phone_number: Número de teléfono del paciente (clave principal de búsqueda).
            patient_name: Nombre del paciente si se conoce (refina la búsqueda).
            days_ahead:   Cuántos días hacia adelante buscar (default 60).
        """
        if not calendar_service:
            return LookupAppointmentsResult(
                success=False,
                error="Servicio de calendario no disponible."
            )

        try:
            tz = TIMEZONE
            now = datetime.now(tz)
            future = now + timedelta(days=days_ahead)

            all_events = await calendar_service.list_events(
                start_date=now,
                end_date=future,
                max_results=100,
            )

            # Normalizar teléfono para comparación
            phone_clean = phone_number.replace("+", "").replace(" ", "").replace("-", "")

            matched = []
            for ev in all_events:
                desc = (ev.get("description") or "").lower()
                summary = (ev.get("summary") or "").lower()
                desc_clean = desc.replace("+", "").replace(" ", "").replace("-", "")

                phone_match = phone_clean in desc_clean
                name_match = (
                    patient_name
                    and len(patient_name) >= 3
                    and patient_name.lower() in (summary + " " + desc)
                )

                if phone_match or name_match:
                    start_raw = (
                        ev.get("start", {}).get("dateTime")
                        or ev.get("start", {}).get("date", "")
                    )
                    end_raw = (
                        ev.get("end", {}).get("dateTime")
                        or ev.get("end", {}).get("date", "")
                    )
                    # Display legible
                    try:
                        start_dt = datetime.fromisoformat(start_raw)
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=tz)
                        display = _format_slot_display(start_dt)
                    except Exception:
                        display = start_raw

                    matched.append(AppointmentInfo(
                        event_id=ev.get("id", ""),
                        title=ev.get("summary", "cita"),
                        start_iso=start_raw,
                        end_iso=end_raw,
                        display=display,
                    ))

            logger.info(
                "lookup_appointments",
                phone=phone_number[:8] + "...",
                found=len(matched),
            )

            return LookupAppointmentsResult(
                success=True,
                appointments=matched[:5],
                total_found=len(matched),
            )

        except Exception as e:
            logger.error("lookup_appointments error", error=str(e))
            return LookupAppointmentsResult(
                success=False,
                error=f"Error buscando citas: {e}",
            )

    return lookup_appointments


# ── Tool 5: reschedule_appointment ────────────────────────────────────────────

def make_reschedule_appointment_tool(calendar_service, db_service):
    @tool
    async def reschedule_appointment(
        event_id: str,
        new_slot_iso: str,
        patient_name: str,
        service: str,
        phone_number: str,
    ) -> RescheduleAppointmentResult:
        """
        Reagenda una cita: cancela el evento actual y crea uno nuevo.

        IMPORTANTE: Solo llamar DESPUÉS de que el paciente confirme el nuevo horario.
        Flujo correcto:
          1. lookup_appointments → obtener event_id actual
          2. check_availability → mostrar nuevos slots
          3. Paciente confirma → reschedule_appointment

        Args:
            event_id:     ID del evento actual a eliminar.
            new_slot_iso: Nuevo slot confirmado en ISO.
            patient_name: Nombre del paciente.
            service:      Nombre del servicio de la cita.
            phone_number: Teléfono del paciente.
        """
        if not calendar_service:
            return RescheduleAppointmentResult(
                success=False,
                error="Servicio de calendario no disponible. Llame a la clínica. 📞"
            )

        try:
            dt = datetime.fromisoformat(new_slot_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TIMEZONE)

            duration = SERVICE_DURATIONS.get(service, 60)
            end_dt = dt + timedelta(minutes=duration)

            # R1 — Create-before-Delete: crear nuevo evento PRIMERO.
            # Si la creación falla, el evento viejo sigue intacto (no se pierde la cita).
            # Solo se elimina el viejo después de confirmar que el nuevo existe.
            new_event_id, new_event_link = await calendar_service.create_event(
                start=dt,
                end=end_dt,
                title=f"{service} - {patient_name}",
                description=f"Paciente: {patient_name}\nTeléfono: {phone_number}\n(Reagendado)",
            )

            if not new_event_id:
                return RescheduleAppointmentResult(
                    success=False,
                    error="Error creando nueva cita en Google Calendar. La cita anterior sigue vigente.",
                )

            logger.info(
                "reschedule_appointment: nuevo evento creado",
                new_event_id=new_event_id,
                new_slot=new_slot_iso,
            )

            # Eliminar evento viejo solo después de confirmar que el nuevo existe
            try:
                await calendar_service.delete_event(event_id)
                logger.info("reschedule_appointment: evento viejo eliminado", event_id=event_id)
            except Exception as del_err:
                # El nuevo evento ya existe — el paciente tiene su cita.
                # El viejo queda huérfano pero no se pierde nada crítico.
                logger.warning(
                    "reschedule_appointment: error eliminando evento viejo (nuevo evento OK)",
                    old_event_id=event_id,
                    new_event_id=new_event_id,
                    error=str(del_err),
                )

            return RescheduleAppointmentResult(
                success=True,
                old_event_id=event_id,
                new_event_id=new_event_id,
                new_event_link=new_event_link,
                new_slot_iso=dt.isoformat(),
                service=service,
                patient_name=patient_name,
            )

        except Exception as e:
            logger.error("reschedule_appointment error", error=str(e))
            return RescheduleAppointmentResult(
                success=False,
                error=f"Error reagendando cita: {e}",
            )

    return reschedule_appointment
