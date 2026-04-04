#!/usr/bin/env python3
"""
Agente Deyy - Agente principal de Arcadium
Diseñado con LangChain moderno (sin deprecated)
Uses contextvars para inyección segura de phone_number en tools
"""

from typing import Any, Dict, List, Optional, Annotated, Callable
from datetime import datetime, timedelta
import uuid
import contextvars
import structlog
from uuid import UUID
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # For Python < 3.9

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from pydantic import Field

from core.config import get_settings
from core.store import ArcadiumStore
from graphs.deyy_graph import create_deyy_graph
from services.appointment_service import AppointmentService, TimeSlot
from services.whatsapp_service import WhatsAppService, WhatsAppMessage
from services.google_calendar_service import GoogleCalendarService
from services.project_appointment_service import ProjectAppointmentService
from config.calendar_mapping import (
    get_service_from_keyword,
    get_duration_for_service,
    get_dentist_for_service,
    list_available_services
)
from utils.phone_utils import normalize_phone
from db.models import ProjectAgentConfig
from memory.memory_manager import MemoryManager
from services.whatsapp_service import WhatsAppService, WhatsAppMessage

logger = structlog.get_logger("agent.deyy")

# Context vars para inyección segura en herramientas
_phone_context = contextvars.ContextVar('phone_number', default=None)
_project_context = contextvars.ContextVar('project_id', default=None)
_project_config_context = contextvars.ContextVar('project_config', default=None)


def set_current_phone(phone: str) -> contextvars.Token:
    """Set current phone number in context"""
    return _phone_context.set(phone)


def get_current_phone() -> str:
    """Get current phone number from context"""
    phone = _phone_context.get()
    if not phone:
        raise ValueError("No phone number set in context")
    return phone


def reset_phone(token: contextvars.Token) -> None:
    """Reset phone context"""
    _phone_context.reset(token)


def set_current_project(project_id: uuid.UUID, project_config: Optional[ProjectAgentConfig] = None):
    """Set current project in context"""
    _project_context.set(project_id)
    _project_config_context.set(project_config)
    # Return a simple marker (not a real token) since we don't need精细 reset
    return True  # Just indicate that context was set


def get_current_project_id() -> Optional[uuid.UUID]:
    """Get current project_id from context"""
    return _project_context.get()


def get_current_project_config() -> Optional[ProjectAgentConfig]:
    """Get current project_config from context"""
    return _project_config_context.get()


def reset_project() -> None:
    """Reset project context"""
    _project_context.set(None)
    _project_config_context.set(None)


# ============================================
# HELPER: Obtener AppointmentService configurado (multi-tenant)
# ============================================
def _get_appointment_service() -> AppointmentService:
    """
    Crea o devuelve AppointmentService.
    Si hay project_id en contexto, usa ProjectAppointmentService.
    Si no, usa el servicio global (legacy single-tenant).

    Returns:
        AppointmentService configurado
    """
    # Verificar si hay proyecto en contexto
    project_id = get_current_project_id()
    project_config = get_current_project_config()

    if project_id and project_config:
        # Modo multi-tenant: usar servicio específico del proyecto
        return ProjectAppointmentService(project_config)
    else:
        # Modo legacy: usar servicio global con settings
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


# ============================================
# HERRAMIENTAS ADICIONALES (WhatsApp, Perfiles, Knowledge)
# ============================================

@tool
async def enviar_mensaje_whatsapp(
    to: Annotated[str, Field(description="Número de teléfono destino (formato internacional)")],
    text: Annotated[str, Field(description="Texto del mensaje")],
    buttons: Annotated[Optional[List[Dict[str, str]]], Field(description="Botones interactivos (max 3)")] = None
) -> Dict[str, Any]:
    """
    Envía un mensaje de WhatsApp a un número.

    USAR CUANDO:
    - Confirmar cita por WhatsApp
    - Enviar recordatorios
    - Notificar cambios importantes
    - Comunicarte fuera de la conversación

    IMPORTANTE:
    - Respeta horarios laborales (9:00-18:00)
    - No abuses: solo para comunicaciones necesarias
    - Máximo 3 botones por mensaje
    """
    try:
        # Obtener whatsapp_service (podría inyectarse como dependencia)
        whatsapp_service = WhatsAppService()

        message = WhatsAppMessage(
            to=to,
            text=text,
            buttons=buttons[:3] if buttons else None
        )

        result = await whatsapp_service.send_message(message)

        return {
            "success": result.get("success", False),
            "message_id": result.get("message_id"),
            "status": result.get("status"),
            "to": to
        }

    except Exception as e:
        logger.error("Error enviando WhatsApp", to=to, error=str(e))
        return {
            "success": False,
            "error": str(e),
            "to": to
        }


@tool
async def obtener_perfil_usuario(
    phone_number: Annotated[str, Field(description="Número de teléfono del usuario")]
) -> Dict[str, Any]:
    """
    Obtiene el perfil completo de un usuario desde memoria a largo plazo.

    Información disponible:
    - Preferencias (servicios favoritos, horarios)
    - Notas médicas o de cliente
    - Hechos extraídos de conversaciones previas
    - Total de conversaciones
    - Fechas first_seen / last_seen

    USAR CUANDO:
    - El usuario regresa y quieres personalizar la atención
    - Para recordar preferencias
    - Antes de actualizar perfil (ver estado actual)
    """
    try:
        memory_manager = MemoryManager()
        # TODO: Obtener project_id desde contexto
        project_id = None

        profile = await memory_manager.get_user_profile(phone_number, project_id)

        if profile is None:
            return {
                "found": False,
                "phone_number": phone_number,
                "message": "Perfil no encontrado"
            }

        return {
            "found": True,
            "phone_number": phone_number,
            "profile": profile
        }

    except Exception as e:
        logger.error("Error obteniendo perfil", phone=phone_number, error=str(e))
        return {
            "found": False,
            "error": str(e),
            "phone_number": phone_number
        }


@tool
async def actualizar_perfil_usuario(
    phone_number: Annotated[str, Field(description="Número de teléfono del usuario")],
    preferences: Annotated[Optional[Dict[str, Any]], Field(description="Preferencias a actualizar")] = None,
    notes: Annotated[Optional[str], Field(description="Notas adicionales")] = None,
    extracted_facts: Annotated[Optional[Dict[str, Any]], Field(description="Hechos extraídos de la conversación")] = None
) -> Dict[str, Any]:
    """
    Actualiza el perfil del usuario con nueva información.

    USAR CUANDO:
    - El usuario menciona preferencias (ej: "prefiero los viernes")
    - Extraes hechos relevantes de la conversación
    - Necesitas guardar notas médicas o de cliente
    - Actualizas información de contacto o servicios de interés

    NOTA:
    - Campos opcionales: solo actualiza los campos proporcionados
    - preferences: merge con existentes
    - notes: concatena con notas previas
    - extracted_facts: merge con existentes
    """
    try:
        memory_manager = MemoryManager()
        project_id = None

        # Obtener perfil existente
        existing = await memory_manager.get_user_profile(phone_number, project_id)

        update_data = {}
        if preferences is not None:
            existing_prefs = existing.get('preferences', {}) if existing else {}
            if isinstance(existing_prefs, dict):
                existing_prefs.update(preferences)
            update_data['preferences'] = existing_prefs

        if notes is not None:
            existing_notes = existing.get('notes', '') if existing else ''
            update_data['notes'] = (existing_notes + "\n" + notes) if existing_notes else notes

        if extracted_facts is not None:
            existing_facts = existing.get('extracted_facts', {}) if existing else {}
            if isinstance(existing_facts, dict):
                existing_facts.update(extracted_facts)
            update_data['extracted_facts'] = existing_facts

        # Guardar
        profile = await memory_manager.create_or_update_profile(
            phone_number=phone_number,
            project_id=project_id,
            **update_data
        )

        # Incrementar contador de conversaciones
        await memory_manager.increment_user_conversation_count(phone_number, project_id)
        await memory_manager.update_user_last_seen(phone_number, project_id)

        return {
            "success": True,
            "phone_number": phone_number,
            "profile": profile
        }

    except Exception as e:
        logger.error("Error actualizando perfil", phone=phone_number, error=str(e))
        return {
            "success": False,
            "error": str(e)
        }


@tool
async def knowledge_base_search(
    query: Annotated[str, Field(description="Consulta de búsqueda")],
    k: Annotated[int, Field(description="Número de resultados (1-20)", ge=1, le=20)] = 5,
    similarity_threshold: Annotated[float, Field(description="Umbral mínimo de similitud (0-1)", ge=0, le=1)] = 0.7
) -> Dict[str, Any]:
    """
    Busca información en la base de conocimientos (Supabase vector store).

    USAR CUANDO:
    - Necesitas información de la clínica, servicios, precios, políticas
    - El usuario pregunta sobre tratamientos, cuidados, procedimientos
    - Para responder preguntas frecuentes
    - Para acceder a documentación interna

    Retorna documentos relevantes con puntuaciones de similitud.
    """
    try:
        # Intentar crear vectorstore
        try:
            vectorstore = LangChainComponentFactory.create_supabase_vectorstore()
        except Exception as e:
            logger.warning("Vectorstore no disponible", error=str(e))
            return {
                "status": "error",
                "error": "Base de conocimientos no disponible",
                "documents": []
            }

        # Búsqueda
        docs = vectorstore.similarity_search_with_relevance_scores(
            query=query,
            k=k
        )

        # Filtrar por umbral
        filtered = [
            {
                "content": doc.page_content[:500],  # Limitar longitud
                "metadata": doc.metadata,
                "score": float(score)
            }
            for doc, score in docs
            if score >= similarity_threshold
        ]

        return {
            "status": "success",
            "query": query,
            "total_results": len(filtered),
            "documents": filtered
        }

    except Exception as e:
        logger.error("Error en knowledge search", query=query, error=str(e))
        return {
            "status": "error",
            "error": str(e),
            "documents": []
        }


@tool
async def think(
    thought: Annotated[str, Field(description="Pensamiento a estructurar")],
    context: Annotated[Optional[str], Field(description="Contexto adicional")] = None,
    focus_areas: Annotated[Optional[List[str]], Field(description="Áreas específicas a considerar")] = None
) -> str:
    """
    Razonamiento estructurado para problemas complejos.

    USAR CUANDO:
    - Analizar situaciones complicadas antes de actuar
    - Evaluar múltiples opciones
    - Identificar riesgos
    - Estructurar pensamiento lógico

    Retorna: Razonamiento completo estructurado
    """
    reasoning = f"""
RAZONAMIENTO ESTRUCTURADO
===========================

PROBLEMA:
{thought}

CONTEXTO:
{context or 'No especificado'}

FOCUS AREAS:
{', '.join(focus_areas) if focus_areas else 'General'}

ANÁLISIS:
1. ¿Cuál es el problema real?
   - Identificar causa raíz
   - Separar síntomas de problema

2. ¿Qué información tengo?
   - Datos disponibles
   - Datos faltantes
   - Supuestos

3. ¿Qué opciones hay?
   - Múltiples soluciones posibles
   - Pros y contras de cada una

4. ¿Qué riesgos hay?
   - Posibles fallos
   - Impacto de errores
   - Mitigaciones

5. ¿Qué he decidido?
   - Razón de la decisión
   - Alternativas descartadas
   - Próximos pasos

IMPLICACIONES:
- Impacto en sistema existente
- Recursos necesarios
- Tiempo de implementación

CONCLUSIÓN:
La mejor aproximación después de analizar el problema es...
""".strip()

    return reasoning


@tool
async def planificador_obligatorio(
    task: Annotated[str, Field(description="Tarea a planificar")],
    constraints: Annotated[Optional[str], Field(description="Restricciones o consideraciones")] = None,
    max_steps: Annotated[int, Field(description="Máximo número de pasos", ge=1, le=50)] = 10
) -> Dict[str, Any]:
    """
    Planifica tareas complejas descomponiéndolas en pasos ejecutables.

    USAR CUANDO:
    - Implementaciones grandes
    - Proyectos con múltiples pasos
    - Cualquier cosa que requiera secuencia lógica

    Retorna: Plan con pasos secuenciales y estimaciones
    """
    plan = {
        "task": task,
        "constraints": constraints or "Ninguna",
        "steps": [],
        "estimated_time": "TBD",
        "dependencies": []
    }

    # Placeholder: dividir en pasos lógicos
    # TODO: Integrar con LLM para generar plan real
    steps = [
        {"step": 1, "description": "Analizar requisitos", "estimated": "1h"},
        {"step": 2, "description": "Diseñar solución", "estimated": "2h"},
        {"step": 3, "description": "Implementar", "estimated": "4h"},
        {"step": 4, "description": "Testear", "estimated": "2h"},
        {"step": 5, "description": "Desplegar", "estimated": "1h"}
    ]

    plan["steps"] = steps[:max_steps]
    plan["estimated_time"] = sum([2, 2, 4, 2, 1][:max_steps])

    return {
        "status": "success",
        "plan": plan,
        "total_steps": len(plan["steps"]),
        "tool": "planificador_obligatorio"
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

INFORMACIÓN IMPORTANTE DE FECHAS:
- Fecha actual: {current_date}
- Hora actual: {current_time}
- Timezone: America/Guayaquil (UTC-5)
- Días laborables: Lunes-Viernes (9:00-18:00)
- Fines de semana (sábado, domingo): NO hay atención

Reglas de fechas:
- NUNCA uses fechas en el pasado
- NUNCA inventes horarios; siempre consulta Google Calendar primero
- SIEMPRE calcula fechas relativas (mañana, próximo viernes) basándote en {current_date}
- Para "mañana": suma 1 día a {current_date}
- Para "esta semana": considera días desde hoy hasta el viernes
- Para "próxima semana": suma 7 días a {current_date}
- AUTO-AJUSTE FINES DE SEMANA: Si la fecha solicitada cae en sábado o domingo, NO preguntes al cliente. Asume que quiere la próxima fecha laborable (lunes) a la MISMA HORA. Ajusta automáticamente y procede.
  Ejemplo: Si hoy es sábado y el usuario pide "mañana a las 10", asume que quiere el lunes a las 10:00. Si es domingo y pide "mañana", asume lunes.
  Di: "Entiendo que quieres [servicio] para mañana a las [hora]. Los [sábados/domingos] no atendemos, así que te lo agendaré para el lunes [fecha] a las [hora]. Voy a verificar disponibilidad..."

Ejemplo de cálculo correcto:
- Si hoy es {current_date}, "mañana" es {tomorrow_date}
- Si hoy es {current_date}, "el próximo lunes" calculado correctamente

CONSULTA DISPONIBILIDAD ANTES DE AGENDAR.

Tus capacidades (herramientas):

📅 GESTIÓN DE CITAS:
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

📱 COMUNICACIÓN:
6. enviar_mensaje_whatsapp(to, text, buttons_opcional)
   - Envía mensaje de WhatsApp
   - Para confirmaciones, recordatorios, notificaciones
   - Respeta horarios laborales (9-18)
   - Máximo 3 botones por mensaje

🧠 CONOCIMIENTO Y MEMORIA:
7. knowledge_base_search(query, k=5, similarity_threshold=0.7)
   - Busca en base de conocimientos
   - Ideal para servicios, precios, políticas, cuidados
   - Retorna documentos relevantes con scores

8. obtener_perfil_usuario(phone_number)
   - Obtiene preferencias, notas, hechos del usuario
   - Para personalizar atención

9. actualizar_perfil_usuario(phone_number, preferences, notes, extracted_facts)
   - Guarda preferencias, notas, hechos extraídos
   - Todos los campos opcionales (merge con existentes)

🤝 RAZONAMIENTO Y PLANIFICACIÓN:
10. think(thought, context, focus_areas)
    - Razonamiento estructurado para problemas complejos
    - Analiza opciones, riesgos, implicaciones

11. planificador_obligatorio(task, constraints, max_steps)
    - Descompone tareas en pasos ejecutables
    - Para implementaciones, proyectos grandes

Flujos recomendados:

🟢 AGENDAR NUEVA CITA:
Cliente: "Quiero agendar una cita"
Tú: Pregunta fecha, hora y servicio específico
   - Si no sabe servicio: explica opciones (consulta, limpieza, empaste, ortodoncia, etc.)
   - Si no sabe fecha/hora: consulta_disponibilidad para sugerir

Tras recibir datos:
   1. VALIDA FECHA:
      - Si es fin de semana (sábado/domingo): NO preguntes. Auto-ajusta automáticamente al próximo día laborable (lunes) a la MISMA HORA. Informa al cliente del ajuste y continúa.
      - Si es fecha pasada: informa que no se puede agendar en el pasado y pide fecha futura.
   2. Usa consultar_disponibilidad para esa fecha (ajustada si fue fin de semana)
   3. Si el usuario especificó HORA (ej: "a las 10") y ese slot exacto está disponible:
        - Salta directamente a confirmación: "¿Confirmas agendar [servicio] para [fecha] a las [hora]?"
        - NO muestres la lista completa de horarios
      Si NO está disponible o el usuario no especificó hora:
        - Muestra 3-4 opciones de horarios y pregunta cuál prefiere
   4. Una vez elegida/o confirmada la hora, pregunta "¿Confirmas agendar [fecha] [hora] para [servicio]?" (si no lo hiciste en paso 3)
   5. Si confirma → agendar_cita
   6. Mostrar confirmación con link de Google Calendar

🟢 CONSULTAR DISPONIBILIDAD:
Cliente: "¿Hay libre el 25/12/2025?" o "¿Cuándo hay para una limpieza?"
Tú: Usa consulta_disponibilidad con fecha y servicio (si se especifica)
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

🟢 ENVIAR CONFIRMACIÓN:
Después de agendar/reagendar:
   - Usa enviar_mensaje_whatsapp para confirmación
   - Incluye link del evento Google si está disponible
   - Información clara: fecha, hora, servicio

🟢 PERSONALIZAR ATENCIÓN:
Si el usuario regresa:
   - Usa obtener_perfil_usuario para recordar preferencias
   - "Veo que la última vez tuviste [servicio]..."
   - Sugiere según historial y preferencias

Reglas CRÍTICAS (NUNCA violar):

❌ NO agendes sin validar fecha/hora primero
❌ NO crees cita sin confirmación explícita del cliente
❌ NO ignores horario laboral (Lun-Vie 9-18)
❌ NO uses fechas en el pasado
❌ NO inventes horarios; siempre consulta Google Calendar primero
❌ NO_enviar WhatsApp fuera de horario laboral
❌ NO compartas datos sensibles en WhatsApp
✅ SIEMPRE valida fecha/hora con check_availability antes de agendar
✅ SIEMPRE confirma detalles (fecha exacta, hora, servicio, duración)
✅ SIEMPRE muestra link del evento Google si está disponible
✅ SIEMPRE usa tone natural y amigable
✅ SIEMPRE guarda preferencias y hechos en perfil del usuario
✅ SIEMPRE consulta knowledge base para preguntas sobre servicios

Manejo de errores:
- Si Google Calendar falla: informa "Momento, hay un problema técnico..."
- Si slot ya no disponible: "Ups, alguien más agendó. Te muestro otras opciones..."
- Si no entiendes el servicio: pregunta clarifying questions
- Si WhatsApp falla: informa y continúa (no bloquea)

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
  * Blanqueamiento: 60 min
  * Revisión: 30 min

NO inventes duraciones; usa la duración estándar del servicio.

Responde siempre en español, tono natural y amigable.
""".strip()

    def __init__(
        self,
        session_id: str,
        store: ArcadiumStore,  # Ahora usa Store en lugar de MemoryManager directo
        project_id: Optional[uuid.UUID] = None,
        project_config: Optional[ProjectAgentConfig] = None,
        whatsapp_service: Optional[WhatsAppService] = None,
        system_prompt: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_temperature: Optional[float] = None,
        max_iterations: Optional[int] = None,
        verbose: bool = False,
        checkpointer: Optional[Any] = None  # Para inyectar checkpointer en tests
    ):
        self.session_id = session_id
        self.store = store  # ArcadiumStore (wrapper sobre MemoryManager)
        self.project_id = project_id
        self.project_config = project_config
        self.whatsapp_service = whatsapp_service
        self._checkpointer = checkpointer  # Guardar para inyectar en create_deyy_graph

        # Configuración desde project_config o settings
        settings = get_settings()
        self.llm_model = llm_model or settings.OPENAI_MODEL
        self.llm_temperature = llm_temperature if llm_temperature is not None else (project_config.temperature if project_config else settings.OPENAI_TEMPERATURE)
        self.max_iterations = max_iterations or (project_config.max_iterations if project_config else settings.AGENT_MAX_ITERATIONS)

        # System prompt con variables de proyecto
        if project_config and project_config.system_prompt:
            # Aplicar variables del template
            base_prompt = project_config.system_prompt
            formatted_prompt = base_prompt.format(
                project_name=project_config.project.name if project_config.project else "Arcadium",
                custom_instructions=project_config.custom_instructions or ""
            )
            self.system_prompt = system_prompt or formatted_prompt
        else:
            # Formatear DEFAULT_SYSTEM_PROMPT con fecha/hora actual en zona America/Guayaquil
            tz = ZoneInfo("America/Guayaquil")
            now = datetime.now(tz)
            current_date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")
            tomorrow_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")

            formatted_prompt = self.DEFAULT_SYSTEM_PROMPT.format(
                current_date=current_date,
                current_time=current_time,
                tomorrow_date=tomorrow_date
            )
            self.system_prompt = system_prompt or formatted_prompt

        self.verbose = verbose

        self._llm: Optional[ChatOpenAI] = None
        self._graph = None  # StateGraph
        self._initialized = False

        # Servicio de citas específico del proyecto
        self.appointment_service: Optional[ProjectAppointmentService] = None

        logger.info(
            "DeyyAgent creado",
            session_id=session_id,
            project_id=str(project_id) if project_id else None,
            model=self.llm_model
        )
        logger.debug(f"DeyyAgent.__init__ completado: {session_id}")

    async def initialize(self):
        """Inicializa el agente con StateGraph (DeyyGraph)"""
        if self._initialized:
            return

        logger.info(
            "Inicializando DeyyAgent",
            project_id=str(self.project_id) if self.project_id else None
        )

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

        # Inicializar servicio de citas para este proyecto
        if self.project_config:
            self.appointment_service = ProjectAppointmentService(self.project_config)
            logger.info(
                "ProjectAppointmentService creado",
                project_id=str(self.project_id),
                calendar_enabled=self.project_config.calendar_enabled
            )
        else:
            # Fallback al servicio global (single-tenant legacy)
            self.appointment_service = _get_appointment_service()
            logger.info("Usando AppointmentService global (legacy mode)")

        # Crear DeyyGraph (StateGraph)
        logger.debug("Creando DeyyGraph...")
        # Recopilar herramientas definidas en este módulo
        from agents.deyy_agent import (
            agendar_cita,
            consultar_disponibilidad,
            obtener_citas_cliente,
            cancelar_cita,
            reagendar_cita,
            enviar_mensaje_whatsapp,
            obtener_perfil_usuario,
            actualizar_perfil_usuario,
            knowledge_base_search,
            think,
            planificador_obligatorio
        )
        tools = [
            agendar_cita,
            consultar_disponibilidad,
            obtener_citas_cliente,
            cancelar_cita,
            reagendar_cita,
            enviar_mensaje_whatsapp,
            obtener_perfil_usuario,
            actualizar_perfil_usuario,
            knowledge_base_search,
            think,
            planificador_obligatorio
        ]

        self._graph = await create_deyy_graph(
            session_id=self.session_id,
            store=self.store,
            project_id=self.project_id,
            system_prompt=self.system_prompt,
            llm_model=self.llm_model,
            llm_temperature=self.llm_temperature,
            tools=tools,
            checkpointer=self._checkpointer if self._checkpointer else None
        )

        self._initialized = True
        logger.info("DeyyAgent inicializado con StateGraph")

    async def _check_agent_toggle(self) -> bool:
        """
        Verifica si el agente está habilitado para esta conversación.

        Returns:
            True si habilitado, False si deshabilitado
        """
        if not self.project_id:
            # Sin proyecto, asumir habilitado
            return True

        # Consultar AgentToggle en DB
        try:
            from db import get_async_session
            from db.models import AgentToggle

            async with get_async_session() as session:
                stmt = select(AgentToggle).where(
                    AgentToggle.conversation_id == uuid.UUID(self.session_id)
                    # Nota: session_id es phone_number, pero AgentToggle usa conversation_id FK.
                    # Necesito buscar la Conversation primero.
                )
                # En realidad: AgentToggle tiene conversation_id FK. Necesito obtener conversation por phone+project.
                from db.models import Conversation
                conv_stmt = select(Conversation).where(
                    Conversation.phone_number == self.session_id,
                    Conversation.project_id == self.project_id
                )
                result = await session.execute(conv_stmt)
                conversation = result.scalar_one_or_none()

                if conversation:
                    toggles_stmt = select(AgentToggle).where(
                        AgentToggle.conversation_id == conversation.id
                    )
                    toggles_result = await session.execute(toggles_stmt)
                    toggle = toggles_result.scalar_one_or_none()

                    if toggle:
                        logger.debug(
                            "AgentToggle encontrado",
                            conversation_id=str(conversation.id),
                            is_enabled=toggle.is_enabled
                        )
                        return toggle.is_enabled

                # Si no hay toggle explícito, verificar project_config.global_agent_enabled
                if self.project_config:
                    return self.project_config.global_agent_enabled

                return True  # default habilitado

        except Exception as e:
            logger.error(
                "Error verificando agent toggle, asumiendo habilitado",
                error=str(e),
                session_id=self.session_id,
                project_id=str(self.project_id)
            )
            return True

    async def process_message(
        self,
        message: str,
        save_to_memory: bool = True,
        check_toggle: bool = True,
        context_vars: Optional[Dict[str, Any]] = None,
        skip_user_message_addition: bool = False
    ) -> Dict[str, Any]:
        """
        Procesa un mensaje del usuario usando StateGraph.

        Args:
            message: Mensaje del usuario
            save_to_memory: Si se guarda en memoria
            check_toggle: Si verifica toggle habilitado
            context_vars: Variables de contexto (fechas calculadas, etc.)
            skip_user_message_addition: Si True, no agrega el mensaje del usuario al estado
                (asume que ya está en el historial del store). Usado por StateMachineAgent.
        """
        start_time = datetime.utcnow()

        try:
            if not self._initialized:
                await self.initialize()

            # Extraer phone_number del session_id (para tools)
            phone = self._extract_phone_from_session(self.session_id)

            # Establecer contexto de proyecto (para tools)
            project_token = None
            if self.project_id:
                project_token = set_current_project(self.project_id, self.project_config)

            # Verificar toggle si se requiere (agente habilitado)
            if check_toggle and self.project_id:
                toggle_enabled = await self._check_agent_toggle()
                if not toggle_enabled:
                    logger.info(
                        "Agente deshabilitado para esta conversación",
                        session_id=self.session_id,
                        project_id=str(self.project_id)
                    )
                    # Aún así guardar el mensaje del usuario en memoria?
                    if save_to_memory:
                        await self.store.add_message(
                            self.session_id,
                            message,
                            message_type="human",
                            project_id=self.project_id
                        )
                    return {
                        "status": "agent_disabled",
                        "response": "Lo siento, el agente está temporalmente deshabilitado para esta conversación. Un administrador te asistirá pronto.",
                        "agent_disabled": True
                    }

            # Establecer contexto de phone para las tools
            token = set_current_phone(phone)

            try:
                # 1. Cargar historial desde store
                history = await self.store.get_history(self.session_id)

                logger.info(
                    "Historial cargado",
                    session_id=self.session_id,
                    message_count=len(history),
                    phone=phone
                )

                # 2. Crear estado DeyyState (messages vacío; load_initial_context lo cargará desde store)
                from graphs.deyy_graph import DeyyState
                state_params = {
                    "messages": [],  # será cargado por load_initial_context desde store
                    "phone_number": phone,
                    "project_id": self.project_id,
                    "context_vars": context_vars,
                    "save_to_memory": save_to_memory,  # Flag para controlar guardado en store
                }
                if not skip_user_message_addition:
                    state_params["current_user_message"] = message
                # Si skip_user_message_addition=True, asumimos que el mensaje ya está en el store
                state = DeyyState(**state_params)
                print(f"[DEBUG] skip_user_message_addition={skip_user_message_addition}, state has current_user_message: {'current_user_message' in state}")

                # 4. Invocar StateGraph
                config = {"configurable": {"thread_id": self.session_id}}
                result = await self._graph.ainvoke(state, config=config)

                # 5. Extraer respuesta (último mensaje AI) y tool calls
                response = ""
                tool_calls = []
                if result.get("messages"):
                    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
                    if ai_messages:
                        response = ai_messages[-1].content
                        # Extraer todos los tool_calls de todos los AIMessages (excepto el último si ya tiene respuesta? todos)
                        for msg in ai_messages:
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tool_calls.append({
                                        "name": tc.get("name") or tc.get("function", {}).get("name"),
                                        "args": tc.get("args") or tc.get("function", {}).get("arguments", {})
                                    })

                execution_time = (datetime.utcnow() - start_time).total_seconds()

                logger.info(
                    "Mensaje procesado con StateGraph",
                    session_id=self.session_id,
                    execution_time=execution_time,
                    response_len=len(response),
                    tool_calls_count=len(tool_calls)
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
                # Resetear contexto de proyecto
                if project_token:
                    reset_project()

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
            return {
                "status": "error",
                "response": "Lo siento, ocurrió un error procesando你的 mensaje.",
                "error": str(e),
                "execution_time_seconds": execution_time,
                "session_id": self.session_id
            }
    def _extract_tool_calls(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrae tool calls del resultado del agente (por implementar)"""
        # TODO: implementar extracción de tool calls
        return []

    def _extract_phone_from_session(self, session_id: str) -> str:
        """
        Extrae número de teléfono del session_id.
        Session ID suele ser el número o un UUID.
        Normaliza el número a formato E.164.
        """
        # Si session_id tiene formato de teléfono, normalizarlo
        if "@" not in session_id and session_id.replace("+", "").isdigit():
            try:
                return normalize_phone(session_id)
            except ValueError:
                return session_id  # Fallback: usar original
        # Si no, extraer de alguna otra fuente
        return session_id
