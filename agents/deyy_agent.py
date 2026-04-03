#!/usr/bin/env python3
"""
Agente Deyy - Agente principal de Arcadium
Diseñado con LangChain moderno (sin deprecated)
Uses contextvars para inyección segura de phone_number en tools
"""

from typing import Any, Dict, List, Optional, Annotated, Callable
from datetime import datetime
import uuid
import contextvars
import structlog

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain.agents import AgentExecutor, create_openai_tools_agent
from pydantic import Field

from core.config import get_settings
from memory.memory_manager import MemoryManager
from services.appointment_service import AppointmentService, TimeSlot
from services.whatsapp_service import WhatsAppService, WhatsAppMessage
from services.google_calendar_service import GoogleCalendarService
from config.calendar_mapping import (
    get_service_from_keyword,
    get_duration_for_service,
    get_dentist_for_service,
    list_available_services
)

logger = structlog.get_logger("agent.deyy")


# ============================================
# HELPER: Obtener AppointmentService configurado
# ============================================
def _get_appointment_service() -> AppointmentService:
    """
    Crea o devuelve AppointmentService con GoogleCalendar si está habilitado.

    Returns:
        AppointmentService configurado
    """
    settings = get_settings()

    if settings.GOOGLE_CALENDAR_ENABLED:
        try:
            gcal = GoogleCalendarService(
                calendar_id=settings.GOOGLE_CALENDAR_DEFAULT_ID,
                credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
                timezone=settings.GOOGLE_CALENDAR_TIMEZONE
            )
            return AppointmentService(
                settings=settings,
                google_calendar_service=gcal
            )
        except Exception as e:
            logger.warning(
                "No se pudo inicializar Google Calendar, usando solo DB",
                error=str(e)
            )
            return AppointmentService(settings=settings)
    else:
        return AppointmentService(settings=settings)


# ============================================
# VALIDADOR DE FECHA/HORA (Decorador)
# ============================================
def validate_appointment_datetime(func):
    """
    Decorator para validar fecha/hora en herramientas de citas.

    Validaciones:
    - Formato ISO válido
    - Fecha futura (no pasado)
    - Día laboral (Lun-Vie)
    - Horario laboral (9:00-18:00)
    - Minutos en slots de 30 (00 o 30)
    """
    from functools import wraps
    from zoneinfo import ZoneInfo

    @wraps(func)
    async def wrapper(fecha: str, *args, **kwargs):
        try:
            dt = datetime.fromisoformat(fecha)
        except ValueError:
            return {
                "success": False,
                "error": "Formato de fecha inválido. Usa ISO 8601: 2025-12-25T14:00"
            }

        settings = get_settings()
        local_tz = ZoneInfo(settings.GOOGLE_CALENDAR_TIMEZONE)

        # Asegurar timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)

        now = datetime.now(local_tz)

        # 1. ¿Fecha pasada?
        if dt < now:
            return {
                "success": False,
                "error": f"No puedes agendar en el pasado. La fecha {dt.strftime('%d/%m/%Y %H:%M')} ya pasó. Por favor elige una fecha futura."
            }

        # 2. ¿Fin de semana?
        if dt.weekday() >= 5:
            return {
                "success": False,
                "error": "Las citas solo se agendan de lunes a viernes (Lun-Vie). Elige un día laborable."
            }

        # 3. ¿Horario laboral?
        hour = dt.hour
        minute = dt.minute

        if hour < 9 or hour >= 18:
            return {
                "success": False,
                "error": f"Horario no laboral. Atendemos de 9:00 a 18:00. La hora {dt.strftime('%H:%M')} está fuera de horario."
            }

        if hour == 18 and minute > 0:
            return {
                "success": False,
                "error": "El último slot disponible termina a las 18:00. Elige antes de las 18:00."
            }

        # 4. ¿Minuto válido? (slots de 30 min: :00 o :30)
        if minute not in [0, 30]:
            nearest_minute = 0 if minute < 30 else 30
            nearest_time = dt.replace(minute=nearest_minute, second=0, microsecond=0)
            return {
                "success": False,
                "error": f"Los slots son cada 30 minutos exactos. ¿Quisiste decir {nearest_time.strftime('%H:%M')}?"
            }

        # Todo OK, proceder
        return await func(fecha, *args, **kwargs)

    return wrapper


logger = structlog.get_logger("agent.deyy")

# Context variable para phone_number (inyección segura en async)
_phone_context = contextvars.ContextVar('phone_number', default=None)


def set_current_phone(phone: str) -> contextvars.Token:
    """Establece el phone_number en el contexto actual (returns token para reset)"""
    return _phone_context.set(phone)


def get_current_phone() -> str:
    """Obtiene el phone_number del contexto actual"""
    phone = _phone_context.get()
    if not phone:
        raise ValueError("No phone number set in context")
    return phone


def reset_phone(token: contextvars.Token) -> None:
    """Resetea el phone_number al token anterior"""
    _phone_context.reset(token)


# ============================================
# TOOLS (Reemplazo de n8n) - Usan get_current_phone()
# ============================================

@tool
@validate_appointment_datetime
async def agendar_cita(
    fecha: Annotated[str, Field(description="Fecha y hora en formato ISO (ej: 2025-12-25T14:30)")],
    servicio: Annotated[str, Field(description="Tipo de servicio (ej: limpieza, consulta, ortodoncia)")],
    notas: Annotated[Optional[str], Field(description="Notas adicionales")] = None
) -> Dict[str, Any]:
    """
    Agenda una nueva cita para el cliente.

    Flujo:
    1. Valida fecha/hora (futura, horario laboral, Lun-Vie)
    2. Convierte servicio coloquial a servicio oficial
    3. Consulta disponibilidad en Google Calendar + DB
    4. Si disponible: crea evento en Google Calendar y guarda en DB
    5. Devuelve confirmación con link del evento

    Args:
        fecha: Fecha y hora en formato ISO (ej: 2025-12-25T14:00)
        servicio: Servicio solicitado (coloquial: "limpieza", "empaste", "frenos")
        notas: Notas adicionales (opcional)

    Returns:
        Dict con confirmación o error
    """
    try:
        phone = get_current_phone()

        # Convertir fecha
        appointment_dt = datetime.fromisoformat(fecha)

        # ============================================
        # PASO 1: Mapear servicio coloquial → oficial
        # ============================================
        try:
            servicio_oficial = get_service_from_keyword(servicio.lower())
        except ValueError:
            # Si no se puede mapear, usar como está (podría ser ya oficial)
            servicio_oficial = servicio
            logger.warning(
                "Servicio no mapeado, usando texto original",
                servicio_original=servicio
            )

        logger.info(
            "Agendando cita",
            phone=phone,
            fecha=appointment_dt.isoformat(),
            servicio_oficial=servicio_oficial
        )

        # ============================================
        # PASO 2: Crear cita (con Google Calendar si está habilitado)
        # ============================================
        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()
            success, message, appointment = await service.create_appointment(
                session=session,
                phone_number=phone,
                appointment_datetime=appointment_dt,
                service_type=servicio_oficial,
                notes=notas
            )

            if success and appointment:
                # Determinar duración para mensaje
                try:
                    duration = get_duration_for_service(servicio_oficial)
                except ValueError:
                    duration = 30

                response = {
                    "success": True,
                    "appointment_id": str(appointment.id),
                    "message": message,
                    "appointment_date": appointment.appointment_date.isoformat(),
                    "servicio": servicio_oficial,
                    "duración_min": duration,
                    "odontólogo": appointment.metadata.get('odontologo', 'No especificado')
                }

                # Añadir link si está disponible
                if appointment.google_event_id:
                    response["google_event_id"] = appointment.google_event_id

                return response
            else:
                return {
                    "success": False,
                    "error": message or "Error desconocido agendando cita"
                }

    except Exception as e:
        logger.error(
            "Error agendando cita",
            phone=get_current_phone(),
            fecha=fecha,
            servicio=servicio,
            error=str(e),
            exc_info=True
        )
        return {
            "success": False,
            "error": f"Error interno: {str(e)}"
        }


@tool
async def consultar_disponibilidad(
    fecha: Annotated[str, Field(description="Fecha a consultar en formato YYYY-MM-DD (ej: 2025-12-25)")],
    servicio: Annotated[Optional[str], Field(description="Tipo de servicio (opcional, si se especifica usa duración específica)")] = None
) -> Dict[str, Any]:
    """
    Consulta horarios disponibles para agendar una cita.

    Flujo:
    1. Parsea la fecha
    2. Si se especifica servicio, usa su duración; si no, usa 30 min default
    3. Consulta Google Calendar (si habilitado) + DB
    4. Devuelve lista de slots libres

    Args:
        fecha: Fecha en formato YYYY-MM-DD
        servicio: Servicio para calcular duración exacta (opcional)

    Returns:
        Dict con slots disponibles
    """
    try:
        # Parsear fecha
        try:
            date_obj = datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            return {
                "success": False,
                "error": "Formato de fecha inválido. Usa YYYY-MM-DD (ej: 2025-12-25)"
            }

        # Determinar duración
        duration_minutes = 30  # default
        if servicio:
            try:
                servicio_oficial = get_service_from_keyword(servicio.lower())
                duration_minutes = get_duration_for_service(servicio_oficial)
            except ValueError:
                logger.warning("Servicio no reconocido para duración, usando 30 min", servicio=servicio)
                duration_minutes = 30

        from db import get_async_session
        async with get_async_session() as session:
            service = _get_appointment_service()
            slots = await service.get_available_slots(
                session=session,
                date=date_obj,
                duration_minutes=duration_minutes
            )

            # Formatear respuesta (máx 10 slots)
            formatted_slots = []
            for slot in slots[:10]:
                formatted_slots.append({
                    "inicio": slot.start.isoformat(),
                    "fin": slot.end.isoformat(),
                    "hora": slot.start.strftime("%H:%M"),
                    "duración": f"{duration_minutes} min"
                })

            # Si no hay slots, sugerir día siguiente
            suggestion = None
            if not formatted_slots:
                tomorrow = date_obj + timedelta(days=1)
                if tomorrow.weekday() < 5:  # Si mañana es laboral
                    suggestion = f"No hay horarios para {date_obj.strftime('%d/%m/%Y')}. ¿Te interesa consultar {tomorrow.strftime('%d/%m/%Y')}?"

            return {
                "success": True,
                "fecha": date_obj.strftime("%Y-%m-%d"),
                "servicio": servicio or "General (30 min)",
                "duración_min": duration_minutes,
                "slots_disponibles": len(formatted_slots),
                "horarios": formatted_slots,
                "sugerencia": suggestion
            }

    except Exception as e:
        logger.error(
            "Error consultando disponibilidad",
            fecha=fecha,
            servicio=servicio,
            error=str(e),
            exc_info=True
        )
        return {
            "success": False,
            "error": f"Error interno: {str(e)}"
        }


@tool
async def obtener_citas_cliente(
    historico: Annotated[Optional[bool], Field(description="Si True, incluye citas pasadas")] = False
) -> Dict[str, Any]:
    """
    Obtiene las citas del cliente actual.

    Por defecto solo muestra citas próximas (futuras). Si se pasa historico=True,
    incluye también citas pasadas.

    Args:
        historico: Si True, incluye citas ya pasadas o canceladas

    Returns:
        Dict con lista de citas
    """
    try:
        phone = get_current_phone()

        from db import get_async_session
        async with get_async_session() as session:
            service = _get_appointment_service()

            if historico:
                # Todas las citas (sin filtro de upcoming)
                appointments = await service.get_appointments_by_phone(
                    session=session,
                    phone_number=phone,
                    upcoming_only=False
                )
            else:
                # Solo próximas (default)
                appointments = await service.get_appointments_by_phone(
                    session=session,
                    phone_number=phone,
                    upcoming_only=True
                )

            citas_formateadas = []
            for appt in appointments:
                # Calcular días restantes
                diff = appt.appointment_date - datetime.now(appt.appointment_date.tzinfo)
                days_left = diff.days
                if days_left == 0:
                    when = "¡HOY!"
                elif days_left == 1:
                    when = "MAÑANA"
                else:
                    when = f"en {days_left} días"

                citas_formateadas.append({
                    "id": str(appt.id),
                    "fecha": appt.appointment_date.strftime("%d/%m/%Y %H:%M"),
                    "servicio": appt.service_type,
                    "estado": appt.status,
                    "notas": appt.notes or "",
                    "google_event_id": appt.google_event_id,
                    "próxima": appt.appointment_date >= datetime.now(appt.appointment_date.tzinfo)
                })

            # Ordenar por fecha
            citas_formateadas.sort(key=lambda x: x['fecha'])

            return {
                "success": True,
                "total": len(citas_formateadas),
                "citas": citas_formateadas,
                "mensaje": f"Tienes {len(citas_formateadas)} cita(s) agendada(s)."
            }

    except Exception as e:
        logger.error(
            "Error obteniendo citas",
            phone=get_current_phone(),
            error=str(e),
            exc_info=True
        )
        return {
            "success": False,
            "error": f"Error interno: {str(e)}"
        }


@tool
async def cancelar_cita(
    appointment_id: Annotated[str, Field(description="ID UUID de la cita a cancelar")] = None
) -> Dict[str, Any]:
    """
    Cancela una cita agendada.

    Si no se proporciona appointment_id, busca la próxima cita del cliente y pregunta.

    Flujo:
    1. Buscar cita en DB (por ID o la próxima)
    2. Si tiene google_event_id: eliminar de Google Calendar
    3. Actualizar estado en DB a "cancelled"

    Args:
        appointment_id: ID de la cita a cancelar (opcional)

    Returns:
        Confirmación de cancelación
    """
    try:
        phone = get_current_phone()
        from uuid import UUID

        from db import get_async_session
        async with get_async_session() as session:
            service = _get_appointment_service()

            # Si no hay appointment_id, buscar próxima cita
            if not appointment_id:
                appointments = await service.get_appointments_by_phone(
                    session=session,
                    phone_number=phone,
                    upcoming_only=True
                )
                if not appointments:
                    return {
                        "success": False,
                        "error": "No tienes citas agendadas para cancelar."
                    }
                # Tomar la primera (más próxima)
                appointment = appointments[0]
                appointment_id = str(appointment.id)
            else:
                # Validar UUID
                try:
                    appt_uuid = UUID(appointment_id)
                except ValueError:
                    return {
                        "success": False,
                        "error": f"ID de cita inválido: {appointment_id}"
                    }

                # Buscar cita específica
                from sqlalchemy import select, and_
                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.id == appt_uuid,
                        AppointmentModel.phone_number == phone
                    )
                )
                result = await session.execute(stmt)
                appointment = result.scalar_one_or_none()

                if not appointment:
                    return {
                        "success": False,
                        "error": f"Cita con ID {appointment_id} no encontrada o no te pertenece."
                    }

            # Confirmar detalles antes de cancelar
            fecha_cita = appointment.appointment_date.strftime("%d/%m/%Y %H:%M")
            servicio = appointment.service_type

            # Cancelar
            success, message = await service.cancel_appointment(session, appointment.id)

            if success:
                return {
                    "success": True,
                    "message": f"✅ Cita cancelada:\n📅 Fecha: {fecha_cita}\n🦷 Servicio: {servicio}\n\n{message}",
                    "appointment_id": str(appointment.id),
                    "fecha": fecha_cita,
                    "servicio": servicio
                }
            else:
                return {
                    "success": False,
                    "error": message
                }

    except Exception as e:
        logger.error(
            "Error cancelando cita",
            phone=get_current_phone(),
            appointment_id=appointment_id,
            error=str(e),
            exc_info=True
        )
        return {
            "success": False,
            "error": f"Error interno: {str(e)}"
        }


@tool
@validate_appointment_datetime
async def reagendar_cita(
    appointment_id: Annotated[Optional[str], Field(description="ID de la cita a reagendar (opcional, si no se especifica se usa la próxima)")] = None,
    nueva_fecha: Annotated[str, Field(description="Nueva fecha y hora en formato ISO (ej: 2025-12-25T14:00)")] = None,
    nuevas_notas: Annotated[Optional[str], Field(description="Notas actualizadas (opcional)")] = None
) -> Dict[str, Any]:
    """
    Reagenda una cita existente a una nueva fecha/hora.

    Flujo:
    1. Buscar cita existente (por ID o la próxima del cliente)
    2. Validar nueva fecha (futura, laboral, Lun-Vie)
    3. Consultar disponibilidad en nueva fecha
    4. Si disponible: actualizar evento en Google Calendar + DB
    5. Devolver confirmación

    Args:
        appointment_id: ID de la cita a modificar (opcional)
        nueva_fecha: Nueva fecha/hora en ISO (REQUERIDO)
        nuevas_notas: Notas actualizadas (opcional)

    Returns:
        Dict con confirmación o error
    """
    try:
        if not nueva_fecha:
            return {
                "success": False,
                "error": "Debes especificar la nueva fecha (nueva_fecha)."
            }

        phone = get_current_phone()
        nueva_dt = datetime.fromisoformat(nueva_fecha)

        from db import get_async_session
        async with get_async_session() as session:
            service = _get_appointment_service()

            # ============================================
            # PASO 1: Buscar cita existente
            # ============================================
            if appointment_id:
                from uuid import UUID
                from sqlalchemy import select, and_
                try:
                    appt_uuid = UUID(appointment_id)
                except ValueError:
                    return {
                        "success": False,
                        "error": f"ID de cita inválido: {appointment_id}"
                    }

                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.id == appt_uuid,
                        AppointmentModel.phone_number == phone,
                        AppointmentModel.status == "scheduled"
                    )
                )
                result = await session.execute(stmt)
                appointment = result.scalar_one_or_none()

                if not appointment:
                    return {
                        "success": False,
                        "error": f"Cita con ID {appointment_id} no encontrada o no está activa."
                    }
            else:
                # Buscar próxima cita
                appointments = await service.get_appointments_by_phone(
                    session=session,
                    phone_number=phone,
                    upcoming_only=True
                )
                if not appointments:
                    return {
                        "success": False,
                        "error": "No tienes citas agendadas para reagendar."
                    }
                appointment = appointments[0]
                appointment_id = str(appointment.id)

            # ============================================
            # PASO 2: Verificar disponibilidad en nueva fecha
            # ============================================
            # Calcular duración de la cita existente partiendo del servicio
            try:
                existing_duration = get_duration_for_service(appointment.service_type)
            except ValueError:
                # Si no está en mapeo, estimar diferencia o usar default
                # Como ya tenemos la cita, calcular diferencia si hay end_date
                # Por ahora default 30
                existing_duration = 30

            is_available, conflict_reason = await service.check_availability(
                session=session,
                start_datetime=nueva_dt,
                duration_minutes=existing_duration
            )

            if not is_available:
                return {
                    "success": False,
                    "error": f"La nueva fecha no está disponible: {conflict_reason}. Por favor elige otro horario."
                }

            # ============================================
            # PASO 3: Actualizar en Google Calendar (si aplica)
            # ============================================
            if appointment.google_event_id and service.google_calendar:
                try:
                    # Determinar calendar_id
                    calendar_id = service._get_calendar_id_for_service(appointment.service_type)

                    # Calcular nuevo end datetime
                    end_dt = nueva_dt + timedelta(minutes=existing_duration)

                    updated_event = await service.google_calendar.update_event(
                        event_id=appointment.google_event_id,
                        start_time=nueva_dt,
                        end_time=end_dt,
                        description=f"Servicio: {appointment.service_type}\nCliente: {phone}\nNotas: {nuevas_notas or appointment.notes or 'N/A'}\n(Reagendado)"
                    )

                    logger.info(
                        "Evento de Google Calendar actualizado",
                        event_id=appointment.google_event_id,
                        new_start=nueva_dt.isoformat()
                    )

                except Exception as e:
                    logger.error(
                        "Error actualizando evento en Google Calendar",
                        event_id=appointment.google_event_id,
                        error=str(e)
                    )
                    # No fallamos, continuamos con DB

            # ============================================
            # PASO 4: Actualizar DB
            # ============================================
            old_date = appointment.appointment_date
            appointment.appointment_date = nueva_dt
            if nuevas_notas is not None:
                appointment.notes = nuevas_notas
            appointment.sync_status = "synced"  # marcar como sincronizado

            await session.flush()
            await session.commit()

            logger.info(
                "Cita reagendada",
                appointment_id=str(appointment.id),
                old_date=old_date.isoformat(),
                new_date=nueva_dt.isoformat()
            )

            # ============================================
            # PASO 5: Respuesta
            # ============================================
            duration = existing_duration
            old_fecha_str = old_date.strftime("%d/%m/%Y %H:%M")
            new_fecha_str = nueva_dt.strftime("%d/%m/%Y %H:%M")

            return {
                "success": True,
                "message": f"✅ Cita reagendada exitosamente:\n📅 Antes: {old_fecha_str}\n📅 Ahora: {new_fecha_str} ({duration} min)\n🦷 Servicio: {appointment.service_type}",
                "appointment_id": str(appointment.id),
                "fecha_anterior": old_fecha_str,
                "fecha_nueva": new_fecha_str,
                "servicio": appointment.service_type
            }

    except Exception as e:
        logger.error(
            "Error reagendando cita",
            appointment_id=appointment_id,
            nueva_fecha=nueva_fecha,
            error=str(e),
            exc_info=True
        )
        return {
            "success": False,
            "error": f"Error interno: {str(e)}"
        }


class DeyyAgent:
    """
    Agente principal Deyy
    Con herramientas integradas y memoria Postgres
    """

    DEFAULT_SYSTEM_PROMPT = """
Eres Deyy, un asistente especializado de Arcadium para gestión de citas de la clínica dental.

Tu personalidad:
- Profesional pero amigable
- Respetuoso y empático
- Claro y conciso
- Proactivo
- Preciso con fechas y horarios

Gestión de Calendario:
- Disponibilidad en TIEMPO REAL desde Google Calendar
- Horario laboral: Lunes-Viernes, 9:00-18:00
- Citas de 30, 45, 60 o 90 min según servicio
- Todos los horarios en timezone America/Guayaquil

Tus capacidades (herramientas):

1. consultar_disponibilidad(fecha, servicio_opcional)
   - Consulta horarios libres en Google Calendar
   - Si servicio especificado, usa su duración exacta
   - Devuelve lista de slots ordenados

2. agendar_cita(fecha, servicio, notas_opcional)
   - Agenda nueva cita en Google Calendar + DB
   - Valida fecha (futura, laboral, Lun-Vie)
   - Muestra link del evento Google al cliente
   - Requiere confirmación explícita antes de agendar

3. obtener_citas_cliente(historico_opcional)
   - Muestra citas agendadas del cliente
   - Por defecto solo próximas (futuras)
   - Incluye fecha, hora, servicio, estado

4. cancelar_cita(appointment_id_opcional)
   - Cancela cita en Google Calendar y DB
   - Si no hay ID, cancela la próxima cita
   - Pide confirmación antes de cancelar
   - Elimina evento de Google y actualiza DB

5. reagendar_cita(appointment_id_opcional, nueva_fecha, nuevas_notas_opcional)
   - Cambia cita existente a nueva fecha/hora
   - Valida nueva fecha (futura, laboral)
   - Consulta disponibilidad primero
   - Actualiza evento en Google + DB
   - Requiere confirmación

Flujos recomendados:

🟢 AGENDAR NUEVA CITA:
Cliente: "Quiero agendar una cita"
Tú: Pregunta fecha, hora y servicio específico
   - Si no sabe servicio: explica opciones (consulta, limpieza, empaste, ortodoncia, etc.)
   - Si no sabe fecha/hora: consulta_disponibilidad para sugerir
Tras recibir datos:
   1. Validar fecha/hora (futura, laboral)
   2. consultar_disponibilidad para confirmar slot libre
   3. Si disponible: preguntar "¿Confirmas agendar [fecha] [hora] para [servicio]?"
   4. Si confirma → agendar_cita
   5. Mostrar confirmación con link de Google Calendar

🟢 CONSULTAR DISPONIBILIDAD:
Cliente: "¿Hay libre el 25/12/2025?" o "¿Cuándo hay para una limpieza?"
Tú: Usa consultar_disponibilidad con fecha y servicio (si se especifica)
   - Si hay slots: muestra lista
   - Si no hay: sugiere otra fecha

🟢 VER MIS CITAS:
Cliente: "¿Qué citas tengo?" o "mis appointments"
Tú: Usa obtener_citas_cliente()
   - Muestra lista con fechas y servicios
   - Si tiene muchas, muestra próxima primero

🟢 CANCELAR CITA:
Cliente: "Cancelar mi cita"
Tú: Busca próxima cita con obtener_citas_cliente()
   - Muestra detalles: "Tienes cita el X a las Y para Z. ¿Confirmas cancelación?"
   - Si confirma → cancelar_cita()
   - Si hay varias: preguntar cuál

🟢 REAGENDAR CITA:
Cliente: "Cambiar mi cita" o "reagendar"
Tú: Obtener citas con obtener_citas_cliente()
   - Identificar cuál reagendar (preguntar si hay varias)
   - Preguntar nueva fecha/hora
   - Validar y consultar disponibilidad
   - Confirmar antes de reagendar
   - Usar reagendar_cita()

Reglas CRÍTICAS (NUNCA violar):

❌ NO agendes sin validar fecha/hora primero
❌ NO crees cita sin confirmación explícita del cliente
❌ NO ignores horario laboral (Lun-Vie 9-18)
❌ NO uses fechas en el pasado
❌ NO inventes horarios; siempre consulta Google Calendar primero
✅ SIEMPRE valida fecha/hora con check_availability antes de agendar
✅ SIEMPRE confirma detalles (fecha exacta, hora, servicio, duración)
✅ SIEMPRE muestra link del evento Google si está disponible
✅ SIEMPRE usa tone natural y amigable

Manejo de errores:
- Si Google Calendar falla: informa "Momento, hay un problema técnico..."
- Si slot ya no disponible: "Ups, alguien más agendó. Te muestro otras opciones..."
- Si no entiendes el servicio: pregunta clarifying questions

Contexto importante:
- Clínica dental con múltiples especialistas
- Cada servicio tiene duración específica:
  * Consulta: 30 min
  * Limpieza: 45 min
  * Empaste/extracción: 45 min
  * Endodoncia: 60-90 min
  * Ortodoncia: 60 min
  * Cirugía: 60-90 min
  * Implantes: 90 min
  * Estética: 60 min
  * Odontopediatría: 30-45 min

NO inventes duraciones; usa la duración estándar del servicio.

Responde siempre en español, tono natural y amigable.
""".strip()

    def __init__(
        self,
        session_id: str,
        memory_manager: MemoryManager,
        whatsapp_service: Optional[WhatsAppService] = None,
        system_prompt: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_temperature: float = 0.7,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.memory_manager = memory_manager
        self.whatsapp_service = whatsapp_service
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.llm_model = llm_model or get_settings().OPENAI_MODEL
        self.llm_temperature = llm_temperature
        self.verbose = verbose

        self._llm: Optional[ChatOpenAI] = None
        self._agent_executor: Optional[AgentExecutor] = None
        self._initialized = False

        logger.info(
            "DeyyAgent creado",
            session_id=session_id,
            model=self.llm_model
        )
        logger.debug(f"DeyyAgent.__init__ completado: {session_id}")

    async def initialize(self):
        """Inicializa el agente con LangChain"""
        if self._initialized:
            return

        logger.info("Inicializando DeyyAgent")
        logger.debug(f"Inicializando agente {self.session_id}")

        # Crear LLM
        logger.debug("Creando ChatOpenAI...")
        self._llm = ChatOpenAI(
            model=self.llm_model,
            temperature=self.llm_temperature,
            api_key=get_settings().OPENAI_API_KEY,
            timeout=get_settings().OPENAI_TIMEOUT,
            max_retries=3
        )
        logger.debug("LLM creado")

        # Definir herramientas (ya decoradas con @tool)
        tools = [
            consultar_disponibilidad,  # Primero: consulta antes de agendar
            agendar_cita,
            obtener_citas_cliente,
            cancelar_cita,
            reagendar_cita
        ]
        logger.debug(f"Tools list: {[t.__name__ if hasattr(t,'__name__') else str(t) for t in tools]}")

        # Crear prompt
        logger.debug("Creando prompt...")
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])
        logger.debug("Prompt creado")

        # Crear agente
        logger.debug("Creando agente con create_openai_tools_agent...")
        agent = create_openai_tools_agent(
            llm=self._llm,
            tools=tools,
            prompt=prompt
        )
        logger.debug("Agente creado")

        # Crear executor
        logger.debug("Creando AgentExecutor...")
        self._agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=self.verbose,
            handle_parsing_errors=True,
            max_iterations=get_settings().AGENT_MAX_ITERATIONS,
            early_stopping_method="generate",
            return_intermediate_steps=True
        )
        logger.debug("Executor creado")

        self._initialized = True
        logger.info("DeyyAgent inicializado")
        logger.debug(f"DeyyAgent {self.session_id} inicializado completamente")

    async def process_message(
        self,
        message: str,
        save_to_memory: bool = True
    ) -> Dict[str, Any]:
        """
        Procesa un mensaje del usuario

        Args:
            message: Texto del mensaje
            save_to_memory: Si guardar en historial

        Returns:
            Dict con respuesta y metadata
        """
        start_time = datetime.utcnow()

        try:
            if not self._initialized:
                await self.initialize()

            # Extraer phone_number del session_id (para tools)
            phone = self._extract_phone_from_session(self.session_id)

            # Establecer contexto de phone para las tools
            token = set_current_phone(phone)

            try:
                # Ejecutar agente
                result = await self._agent_executor.ainvoke({
                    "input": message,
                    "chat_history": await self.memory_manager.get_history(self.session_id)
                })

                response = result.get("output", "")
                tool_calls = self._extract_tool_calls(result)

                execution_time = (datetime.utcnow() - start_time).total_seconds()

                # Guardar en memoria
                if save_to_memory:
                    await self.memory_manager.add_message(
                        session_id=self.session_id,
                        content=message,
                        message_type="human"
                    )
                    await self.memory_manager.add_message(
                        session_id=self.session_id,
                        content=response,
                        message_type="ai"
                    )

                logger.info(
                    "Mensaje procesado",
                    session_id=self.session_id,
                    execution_time=execution_time,
                    tool_calls=len(tool_calls)
                )

                return {
                    "status": "success",
                    "response": response,
                    "tool_calls": tool_calls,
                    "execution_time_seconds": execution_time,
                    "session_id": self.session_id
                }

            finally:
                # Resetear contexto de phone
                reset_phone(token)

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            logger.error(
                "Error procesando mensaje",
                session_id=self.session_id,
                error=str(e),
                execution_time=execution_time
            )
            import traceback
            print(f"\n=== AGENT ERROR en session {self.session_id} ===")
            traceback.print_exc()
            print("=== END AGENT ERROR ===\n")
            raise
            return {
                "status": "error",
                "response": "Lo siento, ocurrió un error procesando tu mensaje.",
                "error": str(e),
                "execution_time_seconds": execution_time,
                "session_id": self.session_id
            }

    def _extract_tool_calls(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrae información de tool calls del resultado"""
        tool_calls = []

        intermediate_steps = result.get("intermediate_steps", [])
        for step in intermediate_steps:
            if len(step) >= 2:
                action, observation = step
                # Obtener nombre de la herramienta de forma segura
                # action.tool puede ser: string, StructuredTool, Tool object
                tool = action.tool
                if isinstance(tool, str):
                    tool_name = tool
                else:
                    # Intentar obtener .name, sino usar __name__ o str()
                    tool_name = getattr(tool, 'name', None)
                    if tool_name is None:
                        tool_name = getattr(tool, '__name__', None)
                    if tool_name is None:
                        tool_name = str(tool).split(' ')[0]  # Fallback seguro

                # Asegurar que el input sea JSON serializable
                tool_input = getattr(action, "tool_input", {})
                if not isinstance(tool_input, dict):
                    tool_input = {"value": str(tool_input)}

                # Debug: log del tipo de herramienta
                logger.debug("Tool extraction", tool_type=type(tool).__name__, tool_name=tool_name)

                tool_calls.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "observation": str(observation)[:500] if observation else None
                })

        return tool_calls

    async def clear_memory(self) -> None:
        """Limpia historial de memoria"""
        await self.memory_manager.clear_session(self.session_id)
        logger.info("Memoria limpiada", session_id=self.session_id)

    def _extract_phone_from_session(self, session_id: str) -> str:
        """
        Extrae número de teléfono del session_id.
        Session ID suele ser el número o un UUID.
        """
        # Si session_id tiene formato de teléfono, usarlo directamente
        if "@" not in session_id and session_id.replace("+", "").isdigit():
            return session_id
        # Si no, extraer de alguna otra fuente
        return session_id
