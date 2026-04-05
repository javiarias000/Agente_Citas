#!/usr/bin/env python3
"""
Agente Deyy - Agente principal de Arcadium

FIXES APLICADOS:
- [CRÍTICO]   logger definido dos veces → una sola definición al inicio
- [CRÍTICO]   set_current_phone / get_current_phone / reset_phone definidos dos veces → eliminados duplicados
- [CRÍTICO]   set_current_project no retornaba Token real → ahora usa ContextVar.set() correctamente
- [IMPORTANTE] obtener_perfil_usuario y actualizar_perfil_usuario creaban MemoryManager()
                sin inicializar → ahora llaman await initialize()
- [IMPORTANTE] process_message tenía texto chino en mensaje de error → corregido
- [MENOR]     reset_project usaba set(None) en ContextVar tipado → ahora usa token o set(None) limpio
"""

import contextvars
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict, List, Optional

import structlog

from utils.langchain_components import LangChainComponentFactory

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import Field

from config.calendar_mapping import get_duration_for_service, get_service_from_keyword
from core.config import get_settings
from core.store import ArcadiumStore
from db.models import ProjectAgentConfig
from graphs.deyy_graph import create_deyy_graph
from memory.memory_manager import MemoryManager
from services.appointment_service import AppointmentService
from services.google_calendar_service import GoogleCalendarService
from services.project_appointment_service import ProjectAppointmentService
from services.whatsapp_service import WhatsAppMessage, WhatsAppService
from utils.phone_utils import normalize_phone

# ============================================
# LOGGER - una sola definición
# ============================================
logger = structlog.get_logger("agent.deyy")

# ============================================
# CONTEXT VARS - una sola definición de cada uno
# ============================================
_phone_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "phone_number", default=None
)
_project_context: contextvars.ContextVar[Optional[uuid.UUID]] = contextvars.ContextVar(
    "project_id", default=None
)
_project_config_context: contextvars.ContextVar[Optional[ProjectAgentConfig]] = (
    contextvars.ContextVar("project_config", default=None)
)


def set_current_phone(phone: str) -> contextvars.Token:
    """Establece el phone_number en el contexto actual. Retorna token para reset."""
    return _phone_context.set(phone)


def get_current_phone() -> str:
    """Obtiene el phone_number del contexto actual."""
    phone = _phone_context.get()
    if not phone:
        raise ValueError("No phone number set in context")
    return phone


def reset_phone(token: contextvars.Token) -> None:
    """Resetea el phone_number al valor anterior."""
    _phone_context.reset(token)


def set_current_project(
    project_id: uuid.UUID, project_config: Optional[ProjectAgentConfig] = None
) -> tuple:
    """
    FIX: Ahora retorna los tokens reales de ambos ContextVars para poder resetear.
    Antes retornaba True (no se podía resetear correctamente).
    """
    token_id = _project_context.set(project_id)
    token_cfg = _project_config_context.set(project_config)
    return token_id, token_cfg


def get_current_project_id() -> Optional[uuid.UUID]:
    return _project_context.get()


def get_current_project_config() -> Optional[ProjectAgentConfig]:
    return _project_config_context.get()


def reset_project(tokens: Optional[tuple] = None) -> None:
    """
    FIX: Acepta los tokens retornados por set_current_project para reset limpio.
    Si no se pasan tokens, hace reset a None (fallback).
    """
    if tokens is not None:
        token_id, token_cfg = tokens
        _project_context.reset(token_id)
        _project_config_context.reset(token_cfg)
    else:
        _project_context.set(None)
        _project_config_context.set(None)


# ============================================
# HELPER: Obtener AppointmentService configurado
# ============================================


def _get_appointment_service() -> AppointmentService:
    project_id = get_current_project_id()
    project_config = get_current_project_config()

    if project_id and project_config:
        return ProjectAppointmentService(project_config)
    else:
        settings = get_settings()
        if settings.GOOGLE_CALENDAR_ENABLED:
            try:
                gcal = GoogleCalendarService(
                    calendar_id=settings.GOOGLE_CALENDAR_DEFAULT_ID,
                    credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
                    timezone=settings.GOOGLE_CALENDAR_TIMEZONE,
                )
                return AppointmentService(
                    settings=settings, google_calendar_service=gcal
                )
            except Exception as e:
                logger.warning(
                    "No se pudo inicializar Google Calendar, usando solo DB",
                    error=str(e),
                )
                return AppointmentService(settings=settings)
        else:
            return AppointmentService(settings=settings)


# ============================================
# VALIDADOR DE FECHA/HORA
# ============================================


def validate_appointment_datetime(func):
    """Decorator para validar fecha/hora en herramientas de citas.

    Busca el parámetro de fecha por nombre: 'fecha' o 'nueva_fecha',
    para ser compatible con agendar_cita y reagendar_cita.
    """
    from functools import wraps

    async def wrapper(*args, **kwargs):
        # Find the datetime arg: could be positional (fecha) or keyword (fecha or nueva_fecha)
        fecha_value = kwargs.get("fecha") or kwargs.get("nueva_fecha")
        if fecha_value is None and args:
            fecha_value = args[0]

        if fecha_value is None:
            return await func(*args, **kwargs)

        try:
            dt = datetime.fromisoformat(fecha_value)
        except ValueError:
            return {
                "success": False,
                "error": "Formato de fecha inválido. Usa ISO 8601: 2025-12-25T14:00",
            }

        settings = get_settings()
        local_tz = ZoneInfo(settings.GOOGLE_CALENDAR_TIMEZONE)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)

        now = datetime.now(local_tz)

        if dt < now:
            return {
                "success": False,
                "error": f"No puedes agendar en el pasado. La fecha {dt.strftime('%d/%m/%Y %H:%M')} ya pasó.",
            }

        if dt.weekday() >= 5:
            return {
                "success": False,
                "error": "Las citas solo se agendan de lunes a viernes. Elige un día laborable.",
            }

        hour, minute = dt.hour, dt.minute

        if hour < 9 or hour >= 18:
            return {
                "success": False,
                "error": f"Horario no laboral. Atendemos de 9:00 a 18:00. La hora {dt.strftime('%H:%M')} está fuera de horario.",
            }

        if minute not in [0, 30]:
            nearest_minute = 0 if minute < 30 else 30
            nearest_time = dt.replace(minute=nearest_minute, second=0, microsecond=0)
            return {
                "success": False,
                "error": f"Los slots son cada 30 minutos exactos. ¿Quisiste decir {nearest_time.strftime('%H:%M')}?",
            }

        return await func(*args, **kwargs)

    return wraps(func)(wrapper)


# ============================================
# TOOLS
# ============================================


@tool
@validate_appointment_datetime
async def agendar_cita(
    fecha: Annotated[
        str, Field(description="Fecha y hora en formato ISO (ej: 2025-12-25T14:30)")
    ],
    servicio: Annotated[str, Field(description="Tipo de servicio dental")],
    notas: Annotated[Optional[str], Field(description="Notas adicionales")] = None,
) -> Dict[str, Any]:
    """Agenda una nueva cita para el cliente."""
    try:
        phone = get_current_phone()
        appointment_dt = datetime.fromisoformat(fecha)

        try:
            servicio_oficial = get_service_from_keyword(servicio.lower())
        except ValueError:
            servicio_oficial = servicio
            logger.warning(
                "Servicio no mapeado, usando texto original", servicio=servicio
            )

        logger.info(
            "Agendando cita",
            phone=phone,
            fecha=appointment_dt.isoformat(),
            servicio=servicio_oficial,
        )

        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()
            success, message, appointment = await service.create_appointment(
                session=session,
                phone_number=phone,
                appointment_datetime=appointment_dt,
                service_type=servicio_oficial,
                notes=notas,
            )

            if success and appointment:
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
                    "odontólogo": appointment.metadata.get(
                        "odontologo", "No especificado"
                    ),
                }
                if appointment.google_event_id:
                    response["google_event_id"] = appointment.google_event_id
                return response
            else:
                return {
                    "success": False,
                    "error": message or "Error desconocido agendando cita",
                }

    except Exception as e:
        logger.error(
            "Error agendando cita",
            fecha=fecha,
            servicio=servicio,
            error=str(e),
            exc_info=True,
        )
        return {"success": False, "error": f"Error interno: {str(e)}"}


@tool
async def consultar_disponibilidad(
    fecha: Annotated[str, Field(description="Fecha en formato YYYY-MM-DD")],
    servicio: Annotated[
        Optional[str], Field(description="Tipo de servicio (opcional)")
    ] = None,
) -> Dict[str, Any]:
    """Consulta horarios disponibles para agendar una cita."""
    try:
        try:
            date_obj = datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            return {
                "success": False,
                "error": "Formato de fecha inválido. Usa YYYY-MM-DD",
            }

        duration_minutes = 30
        if servicio:
            try:
                servicio_oficial = get_service_from_keyword(servicio.lower())
                duration_minutes = get_duration_for_service(servicio_oficial)
            except ValueError:
                logger.warning(
                    "Servicio no reconocido para duración, usando 30 min",
                    servicio=servicio,
                )

        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()
            slots = await service.get_available_slots(
                session=session, date=date_obj, duration_minutes=duration_minutes
            )

            formatted_slots = [
                {
                    "inicio": slot.start.isoformat(),
                    "fin": slot.end.isoformat(),
                    "hora": slot.start.strftime("%H:%M"),
                    "duración": f"{duration_minutes} min",
                }
                for slot in slots[:10]
            ]

            suggestion = None
            if not formatted_slots:
                tomorrow = date_obj + timedelta(days=1)
                if tomorrow.weekday() < 5:
                    suggestion = f"No hay horarios para {date_obj.strftime('%d/%m/%Y')}. ¿Te interesa {tomorrow.strftime('%d/%m/%Y')}?"

            return {
                "success": True,
                "fecha": date_obj.strftime("%Y-%m-%d"),
                "servicio": servicio or "General (30 min)",
                "duración_min": duration_minutes,
                "slots_disponibles": len(formatted_slots),
                "horarios": formatted_slots,
                "sugerencia": suggestion,
            }

    except Exception as e:
        logger.error(
            "Error consultando disponibilidad", fecha=fecha, error=str(e), exc_info=True
        )
        return {"success": False, "error": f"Error interno: {str(e)}"}


@tool
async def obtener_citas_cliente(
    historico: Annotated[
        Optional[bool], Field(description="Si True, incluye citas pasadas")
    ] = False,
) -> Dict[str, Any]:
    """Obtiene las citas del cliente actual."""
    try:
        phone = get_current_phone()

        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()
            appointments = await service.get_appointments_by_phone(
                session=session, phone_number=phone, upcoming_only=not historico
            )

            citas_formateadas = []
            for appt in appointments:
                diff = appt.appointment_date - datetime.now(
                    appt.appointment_date.tzinfo
                )
                days_left = diff.days
                when = (
                    "¡HOY!"
                    if days_left == 0
                    else ("MAÑANA" if days_left == 1 else f"en {days_left} días")
                )

                citas_formateadas.append(
                    {
                        "id": str(appt.id),
                        "fecha": appt.appointment_date.strftime("%d/%m/%Y %H:%M"),
                        "servicio": appt.service_type,
                        "estado": appt.status,
                        "notas": appt.notes or "",
                        "google_event_id": appt.google_event_id,
                        "próxima": appt.appointment_date
                        >= datetime.now(appt.appointment_date.tzinfo),
                        "cuando": when,
                    }
                )

            citas_formateadas.sort(key=lambda x: x["fecha"])

            return {
                "success": True,
                "total": len(citas_formateadas),
                "citas": citas_formateadas,
                "mensaje": f"Tienes {len(citas_formateadas)} cita(s) agendada(s).",
            }

    except Exception as e:
        logger.error("Error obteniendo citas", error=str(e), exc_info=True)
        return {"success": False, "error": f"Error interno: {str(e)}"}


@tool
async def cancelar_cita(
    appointment_id: Annotated[
        Optional[str], Field(description="ID UUID de la cita a cancelar")
    ] = None,
) -> Dict[str, Any]:
    """Cancela una cita agendada."""
    try:
        phone = get_current_phone()
        from uuid import UUID

        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()

            if not appointment_id:
                appointments = await service.get_appointments_by_phone(
                    session=session, phone_number=phone, upcoming_only=True
                )
                if not appointments:
                    return {
                        "success": False,
                        "error": "No tienes citas agendadas para cancelar.",
                    }
                appointment = appointments[0]
                appointment_id = str(appointment.id)
            else:
                try:
                    appt_uuid = UUID(appointment_id)
                except ValueError:
                    return {
                        "success": False,
                        "error": f"ID de cita inválido: {appointment_id}",
                    }

                from sqlalchemy import and_, select

                from db.models import Appointment as AppointmentModel

                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.id == appt_uuid,
                        AppointmentModel.phone_number == phone,
                    )
                )
                result = await session.execute(stmt)
                appointment = result.scalar_one_or_none()

                if not appointment:
                    return {
                        "success": False,
                        "error": f"Cita {appointment_id} no encontrada o no te pertenece.",
                    }

            fecha_cita = appointment.appointment_date.strftime("%d/%m/%Y %H:%M")
            servicio = appointment.service_type

            success, message = await service.cancel_appointment(session, appointment.id)

            if success:
                return {
                    "success": True,
                    "message": f"✅ Cita cancelada:\n📅 {fecha_cita}\n🦷 {servicio}\n\n{message}",
                    "appointment_id": str(appointment.id),
                    "fecha": fecha_cita,
                    "servicio": servicio,
                }
            else:
                return {"success": False, "error": message}

    except Exception as e:
        logger.error(
            "Error cancelando cita",
            appointment_id=appointment_id,
            error=str(e),
            exc_info=True,
        )
        return {"success": False, "error": f"Error interno: {str(e)}"}


@tool
@validate_appointment_datetime
async def reagendar_cita(
    appointment_id: Annotated[
        Optional[str], Field(description="ID de la cita a reagendar")
    ] = None,
    nueva_fecha: Annotated[str, Field(description="Nueva fecha y hora en ISO")] = None,
    nuevas_notas: Annotated[
        Optional[str], Field(description="Notas actualizadas")
    ] = None,
) -> Dict[str, Any]:
    """Reagenda una cita existente a una nueva fecha/hora."""
    try:
        if not nueva_fecha:
            return {"success": False, "error": "Debes especificar la nueva fecha."}

        phone = get_current_phone()
        nueva_dt = datetime.fromisoformat(nueva_fecha)

        from db import get_async_session

        async with get_async_session() as session:
            service = _get_appointment_service()

            if appointment_id:
                from uuid import UUID

                from sqlalchemy import and_, select

                from db.models import Appointment as AppointmentModel

                try:
                    appt_uuid = UUID(appointment_id)
                except ValueError:
                    return {
                        "success": False,
                        "error": f"ID de cita inválido: {appointment_id}",
                    }

                stmt = select(AppointmentModel).where(
                    and_(
                        AppointmentModel.id == appt_uuid,
                        AppointmentModel.phone_number == phone,
                        AppointmentModel.status == "scheduled",
                    )
                )
                result = await session.execute(stmt)
                appointment = result.scalar_one_or_none()

                if not appointment:
                    return {
                        "success": False,
                        "error": f"Cita {appointment_id} no encontrada o no está activa.",
                    }
            else:
                appointments = await service.get_appointments_by_phone(
                    session=session, phone_number=phone, upcoming_only=True
                )
                if not appointments:
                    return {
                        "success": False,
                        "error": "No tienes citas agendadas para reagendar.",
                    }
                appointment = appointments[0]
                appointment_id = str(appointment.id)

            try:
                existing_duration = get_duration_for_service(appointment.service_type)
            except ValueError:
                existing_duration = 30

            is_available, conflict_reason = await service.check_availability(
                session=session,
                start_datetime=nueva_dt,
                duration_minutes=existing_duration,
            )

            if not is_available:
                return {
                    "success": False,
                    "error": f"La nueva fecha no está disponible: {conflict_reason}.",
                }

            if (
                appointment.google_event_id
                and hasattr(service, "google_calendar")
                and service.google_calendar
            ):
                try:
                    end_dt = nueva_dt + timedelta(minutes=existing_duration)
                    await service.google_calendar.update_event(
                        event_id=appointment.google_event_id,
                        start_time=nueva_dt,
                        end_time=end_dt,
                        description=f"Servicio: {appointment.service_type}\nNotas: {nuevas_notas or appointment.notes or 'N/A'}\n(Reagendado)",
                    )
                except Exception as e:
                    logger.error(
                        "Error actualizando Google Calendar",
                        event_id=appointment.google_event_id,
                        error=str(e),
                    )

            old_date = appointment.appointment_date
            appointment.appointment_date = nueva_dt
            if nuevas_notas is not None:
                appointment.notes = nuevas_notas

            await session.flush()
            await session.commit()

            return {
                "success": True,
                "message": (
                    f"✅ Cita reagendada:\n"
                    f"📅 Antes: {old_date.strftime('%d/%m/%Y %H:%M')}\n"
                    f"📅 Ahora: {nueva_dt.strftime('%d/%m/%Y %H:%M')} ({existing_duration} min)\n"
                    f"🦷 Servicio: {appointment.service_type}"
                ),
                "appointment_id": str(appointment.id),
                "fecha_anterior": old_date.strftime("%d/%m/%Y %H:%M"),
                "fecha_nueva": nueva_dt.strftime("%d/%m/%Y %H:%M"),
                "servicio": appointment.service_type,
            }

    except Exception as e:
        logger.error(
            "Error reagendando cita",
            nueva_fecha=nueva_fecha,
            error=str(e),
            exc_info=True,
        )
        return {"success": False, "error": f"Error interno: {str(e)}"}


@tool
async def enviar_mensaje_whatsapp(
    to: Annotated[str, Field(description="Número de teléfono destino")],
    text: Annotated[str, Field(description="Texto del mensaje")],
    buttons: Annotated[
        Optional[List[Dict[str, str]]],
        Field(description="Botones interactivos (max 3)"),
    ] = None,
) -> Dict[str, Any]:
    """Envía un mensaje de WhatsApp a un número."""
    try:
        whatsapp_service = WhatsAppService()
        message = WhatsAppMessage(
            to=to, text=text, buttons=buttons[:3] if buttons else None
        )
        result = await whatsapp_service.send_message(message)
        return {
            "success": result.get("success", False),
            "message_id": result.get("message_id"),
            "status": result.get("status"),
            "to": to,
        }
    except Exception as e:
        logger.error("Error enviando WhatsApp", to=to, error=str(e))
        return {"success": False, "error": str(e), "to": to}


@tool
async def obtener_perfil_usuario(
    phone_number: Annotated[str, Field(description="Número de teléfono del usuario")],
) -> Dict[str, Any]:
    """
    Obtiene el perfil completo del usuario desde memoria a largo plazo.
    FIX: Ahora inicializa MemoryManager correctamente antes de usarlo.
    """
    try:
        memory_manager = MemoryManager()
        await (
            memory_manager.initialize()
        )  # FIX: era omitido antes → AttributeError en _backend

        project_id = get_current_project_id()
        profile = await memory_manager.get_user_profile(phone_number, project_id)

        if profile is None:
            return {
                "found": False,
                "phone_number": phone_number,
                "message": "Perfil no encontrado",
            }

        return {"found": True, "phone_number": phone_number, "profile": profile}

    except Exception as e:
        logger.error("Error obteniendo perfil", phone=phone_number, error=str(e))
        return {"found": False, "error": str(e), "phone_number": phone_number}


@tool
async def actualizar_perfil_usuario(
    phone_number: Annotated[str, Field(description="Número de teléfono del usuario")],
    preferences: Annotated[
        Optional[Dict[str, Any]], Field(description="Preferencias a actualizar")
    ] = None,
    notes: Annotated[Optional[str], Field(description="Notas adicionales")] = None,
    extracted_facts: Annotated[
        Optional[Dict[str, Any]], Field(description="Hechos extraídos")
    ] = None,
) -> Dict[str, Any]:
    """
    Actualiza el perfil del usuario con nueva información.
    FIX: Ahora inicializa MemoryManager correctamente antes de usarlo.
    """
    try:
        memory_manager = MemoryManager()
        await (
            memory_manager.initialize()
        )  # FIX: era omitido antes → AttributeError en _backend

        project_id = get_current_project_id()
        existing = await memory_manager.get_user_profile(phone_number, project_id)

        update_data = {}
        if preferences is not None:
            existing_prefs = existing.get("preferences", {}) if existing else {}
            if isinstance(existing_prefs, dict):
                existing_prefs.update(preferences)
            update_data["preferences"] = existing_prefs

        if notes is not None:
            existing_notes = existing.get("notes", "") if existing else ""
            update_data["notes"] = (
                (existing_notes + "\n" + notes).strip() if existing_notes else notes
            )

        if extracted_facts is not None:
            existing_facts = existing.get("extracted_facts", {}) if existing else {}
            if isinstance(existing_facts, dict):
                existing_facts.update(extracted_facts)
            update_data["extracted_facts"] = existing_facts

        profile = await memory_manager.create_or_update_profile(
            phone_number=phone_number, project_id=project_id, **update_data
        )
        await memory_manager.increment_user_conversation_count(phone_number, project_id)
        await memory_manager.update_user_last_seen(phone_number, project_id)

        return {"success": True, "phone_number": phone_number, "profile": profile}

    except Exception as e:
        logger.error("Error actualizando perfil", phone=phone_number, error=str(e))
        return {"success": False, "error": str(e)}


@tool
async def knowledge_base_search(
    query: Annotated[str, Field(description="Consulta de búsqueda")],
    k: Annotated[
        int, Field(description="Número de resultados (1-20)", ge=1, le=20)
    ] = 5,
    similarity_threshold: Annotated[
        float, Field(description="Umbral mínimo de similitud (0-1)", ge=0, le=1)
    ] = 0.7,
) -> Dict[str, Any]:
    """Busca información en la base de conocimientos."""
    try:
        try:
            vectorstore = LangChainComponentFactory.create_supabase_vectorstore()
        except Exception as e:
            logger.warning("Vectorstore no disponible", error=str(e))
            return {
                "status": "error",
                "error": "Base de conocimientos no disponible",
                "documents": [],
            }

        docs = vectorstore.similarity_search_with_relevance_scores(query=query, k=k)
        filtered = [
            {
                "content": doc.page_content[:500],
                "metadata": doc.metadata,
                "score": float(score),
            }
            for doc, score in docs
            if score >= similarity_threshold
        ]

        return {
            "status": "success",
            "query": query,
            "total_results": len(filtered),
            "documents": filtered,
        }

    except Exception as e:
        logger.error("Error en knowledge search", query=query, error=str(e))
        return {"status": "error", "error": str(e), "documents": []}


@tool
async def think(
    thought: Annotated[str, Field(description="Pensamiento a estructurar")],
    context: Annotated[Optional[str], Field(description="Contexto adicional")] = None,
    focus_areas: Annotated[
        Optional[List[str]], Field(description="Áreas específicas a considerar")
    ] = None,
) -> str:
    """Razonamiento estructurado para problemas complejos."""
    return f"""
RAZONAMIENTO ESTRUCTURADO
===========================
PROBLEMA: {thought}
CONTEXTO: {context or "No especificado"}
FOCUS AREAS: {", ".join(focus_areas) if focus_areas else "General"}

ANÁLISIS:
1. ¿Cuál es el problema real? → Identificar causa raíz
2. ¿Qué información tengo? → Datos disponibles y faltantes
3. ¿Qué opciones hay? → Múltiples soluciones y sus trade-offs
4. ¿Qué riesgos hay? → Posibles fallos y mitigaciones
5. ¿Qué he decidido? → Razón de la decisión y próximos pasos
""".strip()


@tool
async def planificador_obligatorio(
    task: Annotated[str, Field(description="Tarea a planificar")],
    constraints: Annotated[Optional[str], Field(description="Restricciones")] = None,
    max_steps: Annotated[
        int, Field(description="Máximo número de pasos", ge=1, le=50)
    ] = 10,
) -> Dict[str, Any]:
    """Planifica tareas complejas descomponiéndolas en pasos ejecutables."""
    steps = [
        {"step": 1, "description": "Analizar requisitos", "estimated": "1h"},
        {"step": 2, "description": "Diseñar solución", "estimated": "2h"},
        {"step": 3, "description": "Implementar", "estimated": "4h"},
        {"step": 4, "description": "Testear", "estimated": "2h"},
        {"step": 5, "description": "Desplegar", "estimated": "1h"},
    ]
    selected_steps = steps[:max_steps]
    return {
        "status": "success",
        "plan": {
            "task": task,
            "constraints": constraints or "Ninguna",
            "steps": selected_steps,
            "estimated_time": sum([1, 2, 4, 2, 1][: len(selected_steps)]),
        },
        "total_steps": len(selected_steps),
        "tool": "planificador_obligatorio",
    }


# ============================================
# CLASE DeyyAgent
# ============================================


class DeyyAgent:
    """Agente principal Deyy con herramientas integradas y memoria Postgres."""

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
- SIEMPRE calcula fechas relativas basándote en {current_date}
- Para "mañana": suma 1 día a {current_date} → {tomorrow_date}
- AUTO-AJUSTE FINES DE SEMANA: Si la fecha cae en sábado o domingo,
  ajusta automáticamente al lunes siguiente a la misma hora e informa al cliente.

Responde siempre en español, tono natural y amigable.
""".strip()

    def __init__(
        self,
        session_id: str,
        store: ArcadiumStore,
        project_id: Optional[uuid.UUID] = None,
        project_config: Optional[ProjectAgentConfig] = None,
        whatsapp_service: Optional[WhatsAppService] = None,
        system_prompt: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_temperature: Optional[float] = None,
        max_iterations: Optional[int] = None,
        verbose: bool = False,
        checkpointer: Optional[Any] = None,
    ):
        self.session_id = session_id
        self.store = store
        self.project_id = project_id
        self.project_config = project_config
        self.whatsapp_service = whatsapp_service
        self._checkpointer = checkpointer

        settings = get_settings()
        self.llm_model = llm_model or settings.OPENAI_MODEL
        self.llm_temperature = (
            llm_temperature
            if llm_temperature is not None
            else (
                project_config.temperature
                if project_config
                else settings.OPENAI_TEMPERATURE
            )
        )
        self.max_iterations = max_iterations or (
            project_config.max_iterations
            if project_config
            else settings.AGENT_MAX_ITERATIONS
        )

        if project_config and project_config.system_prompt:
            base_prompt = project_config.system_prompt
            formatted_prompt = base_prompt.format(
                project_name=project_config.project.name
                if project_config.project
                else "Arcadium",
                custom_instructions=project_config.custom_instructions or "",
            )
            self.system_prompt = system_prompt or formatted_prompt
        else:
            tz = ZoneInfo("America/Guayaquil")
            now = datetime.now(tz)
            formatted_prompt = self.DEFAULT_SYSTEM_PROMPT.format(
                current_date=now.strftime("%Y-%m-%d"),
                current_time=now.strftime("%H:%M"),
                tomorrow_date=(now + timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            self.system_prompt = system_prompt or formatted_prompt

        self.verbose = verbose
        self._llm: Optional[ChatOpenAI] = None
        self._graph = None
        self._initialized = False
        self.appointment_service: Optional[ProjectAppointmentService] = None

        logger.info(
            "DeyyAgent creado",
            session_id=session_id,
            project_id=str(project_id) if project_id else None,
            model=self.llm_model,
        )

    async def initialize(self):
        """Inicializa el agente con StateGraph (DeyyGraph)."""
        if self._initialized:
            return

        logger.info("Inicializando DeyyAgent", session_id=self.session_id)

        self._llm = ChatOpenAI(
            model=self.llm_model,
            temperature=self.llm_temperature,
            api_key=get_settings().OPENAI_API_KEY,
            timeout=get_settings().OPENAI_TIMEOUT,
            max_retries=3,
        )

        if self.project_config:
            self.appointment_service = ProjectAppointmentService(self.project_config)
        else:
            self.appointment_service = _get_appointment_service()

        from agents.tools_state_machine import record_patient_name

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
            planificador_obligatorio,
            record_patient_name,
        ]

        self._graph = await create_deyy_graph(
            session_id=self.session_id,
            store=self.store,
            project_id=self.project_id,
            system_prompt=self.system_prompt,
            llm_model=self.llm_model,
            llm_temperature=self.llm_temperature,
            tools=tools,
            checkpointer=self._checkpointer,
        )

        self._initialized = True
        logger.info("DeyyAgent inicializado", session_id=self.session_id)

    async def _check_agent_toggle(self) -> bool:
        """Verifica si el agente está habilitado para esta conversación."""
        if not self.project_id:
            return True

        try:
            from sqlalchemy import select

            from db import get_async_session
            from db.models import AgentToggle, Conversation

            async with get_async_session() as session:
                conv_stmt = select(Conversation).where(
                    Conversation.phone_number == self.session_id,
                    Conversation.project_id == self.project_id,
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
                        return toggle.is_enabled

                if self.project_config:
                    return self.project_config.global_agent_enabled

                return True

        except Exception as e:
            logger.error(
                "Error verificando agent toggle",
                error=str(e),
                session_id=self.session_id,
            )
            return True

    async def process_message(
        self,
        message: str,
        save_to_memory: bool = True,
        check_toggle: bool = True,
        context_vars: Optional[Dict[str, Any]] = None,
        skip_user_message_addition: bool = False,
    ) -> Dict[str, Any]:
        """Procesa un mensaje del usuario usando StateGraph."""
        start_time = datetime.utcnow()

        try:
            if not self._initialized:
                await self.initialize()

            phone = self._extract_phone_from_session(self.session_id)

            # FIX: set_current_project ahora retorna tokens reales
            project_tokens = None
            if self.project_id:
                project_tokens = set_current_project(
                    self.project_id, self.project_config
                )

            if check_toggle and self.project_id:
                toggle_enabled = await self._check_agent_toggle()
                if not toggle_enabled:
                    logger.info("Agente deshabilitado", session_id=self.session_id)
                    if save_to_memory:
                        await self.store.add_message(
                            self.session_id,
                            message,
                            message_type="human",
                            project_id=self.project_id,
                        )
                    return {
                        "status": "agent_disabled",
                        "response": "Lo siento, el agente está temporalmente deshabilitado. Un administrador te asistirá pronto.",
                        "agent_disabled": True,
                    }

            token = set_current_phone(phone)

            try:
                from graphs.deyy_graph import DeyyState

                state_params = {
                    "messages": [],
                    "phone_number": phone,
                    "project_id": self.project_id,
                    "context_vars": context_vars,
                    "save_to_memory": save_to_memory,
                }
                if not skip_user_message_addition:
                    state_params["current_user_message"] = message

                state = DeyyState(**state_params)

                config = {"configurable": {"thread_id": self.session_id}}
                result = await self._graph.ainvoke(state, config=config)

                response = ""
                tool_calls = []
                if result.get("messages"):
                    ai_messages = [
                        m for m in result["messages"] if isinstance(m, AIMessage)
                    ]
                    if ai_messages:
                        response = ai_messages[-1].content
                        for msg in ai_messages:
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tool_calls.append(
                                        {
                                            "name": tc.get("name")
                                            or tc.get("function", {}).get("name"),
                                            "args": tc.get("args")
                                            or tc.get("function", {}).get(
                                                "arguments", {}
                                            ),
                                        }
                                    )

                execution_time = (datetime.utcnow() - start_time).total_seconds()

                logger.info(
                    "Mensaje procesado",
                    session_id=self.session_id,
                    execution_time=execution_time,
                    response_len=len(response),
                    tool_calls_count=len(tool_calls),
                )

                return {
                    "status": "success",
                    "response": response,
                    "tool_calls": tool_calls,
                    "execution_time_seconds": execution_time,
                    "session_id": self.session_id,
                }

            finally:
                reset_phone(token)
                # FIX: reset_project ahora recibe los tokens reales
                if project_tokens is not None:
                    reset_project(project_tokens)

        except Exception as e:
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            logger.error(
                "Error procesando mensaje",
                session_id=self.session_id,
                error=str(e),
                exc_info=True,
            )
            return {
                "status": "error",
                # FIX: eliminado texto chino "你的" en mensaje de error
                "response": "Lo siento, ocurrió un error procesando tu mensaje.",
                "error": str(e),
                "execution_time_seconds": execution_time,
                "session_id": self.session_id,
            }

    def _extract_phone_from_session(self, session_id: str) -> str:
        """Extrae y normaliza número de teléfono del session_id."""
        if "@" not in session_id and session_id.replace("+", "").isdigit():
            try:
                return normalize_phone(session_id)
            except ValueError:
                return session_id
        return session_id
