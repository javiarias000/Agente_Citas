# -*- coding: utf-8 -*-
"""
Herramientas específicas de Arcadium usando decorador @tool
Compatibles con LangChain moderno
"""

from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timedelta
import uuid
import structlog

from agents.langchain_compat import tool  # Decorador tool de LangChain
from pydantic import BaseModel, Field

from services.appointment_service import AppointmentService
from services.whatsapp_service import WhatsAppService, WhatsAppMessage
from memory.memory_manager import MemoryManager
import asyncio

logger = structlog.get_logger("arcadium_tools")


# ========== SCHEMAS ==========

class AppointmentInput(BaseModel):
    """Input para agendar cita"""
    phone_number: str = Field(description="Número de teléfono del cliente (formato internacional)")
    service_type: str = Field(description="Tipo de servicio (ej: 'limpieza', 'ortodoncia')")
    appointment_datetime: str = Field(description="Fecha y hora en ISO 8601 (ej: 2025-01-15T14:30:00)")
    notes: Optional[str] = Field(None, description="Notas adicionales para la cita")


class AvailabilityInput(BaseModel):
    """Input para consultar disponibilidad"""
    date: str = Field(description="Fecha a consultar en ISO 8601 (ej: 2025-01-15)")
    service_type: Optional[str] = Field(None, description="Tipo de servicio (opcional)")


class GetAppointmentsInput(BaseModel):
    """Input para obtener citas de un cliente"""
    phone_number: str = Field(description="Número de teléfono del cliente")
    status: Optional[str] = Field("scheduled", description="Estado: scheduled, cancelled, completed, all")


class CancelAppointmentInput(BaseModel):
    """Input para cancelar cita"""
    appointment_id: str = Field(description="ID de la cita a cancelar (UUID)")


class SendWhatsAppInput(BaseModel):
    """Input para enviar mensaje WhatsApp"""
    to: str = Field(description="Número de teléfono destino")
    text: str = Field(description="Texto del mensaje")
    buttons: Optional[List[Dict[str, str]]] = Field(None, description="Botones interactivos (max 3)")


class UpdateProfileInput(BaseModel):
    """Input para actualizar perfil de usuario"""
    phone_number: str = Field(description="Número de teléfono")
    preferences: Optional[Dict[str, Any]] = Field(None, description="Preferencias del usuario")
    notes: Optional[str] = Field(None, description="Notas médicas o de cliente")
    extracted_facts: Optional[Dict[str, Any]] = Field(None, description="Hechos extraídos de la conversación")


class GetProfileInput(BaseModel):
    """Input para obtener perfil de usuario"""
    phone_number: str = Field(description="Número de teléfono del usuario")


# ========== TOOLS ==========

@tool
async def agendar_cita(
    phone_number: str = Field(description="Número de teléfono del cliente"),
    appointment_datetime: str = Field(description="Fecha y hora en ISO 8601 (ej: 2025-01-15T14:30:00)"),
    service_type: str = Field(description="Tipo de servicio (ej: limpieza, ortodoncia, consulta)"),
    notes: Optional[str] = Field(None, description="Notas adicionales")
) -> Dict[str, Any]:
    """
    Agenda una nueva cita en Google Calendar y PostgreSQL.

    Flujo:
    1. Valida fecha/hora (futura, laboral, Lun-Vie, slots de 30 min)
    2. Verifica disponibilidad en Google Calendar y DB
    3. Crea evento en Google Calendar
    4. Guarda cita en PostgreSQL
    5. Retorna confirmación con link del evento

    Returns:
        Dict con success, message, appointment_id, google_event_id, etc.
    """
    try:
        appointment_service = AppointmentService()

        # Parsear fecha
        try:
            dt = datetime.fromisoformat(appointment_datetime.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                tz = getattr(appointment_service, 'timezone', 'America/Guayaquil')
                dt = dt.replace(tzinfo=ZoneInfo(tz))
        except Exception as e:
            return {
                "success": False,
                "error": f"Formato de fecha inválido: {str(e)}",
                "message": "Fecha debe ser ISO 8601 (ej: 2025-01-15T14:30:00)"
            }

        # Crear sesión DB
        from db import get_async_session
        async with get_async_session() as session:
            success, message, appointment = await appointment_service.create_appointment(
                session=session,
                phone_number=phone_number,
                appointment_datetime=dt,
                service_type=service_type,
                notes=notes
            )

        if success and appointment:
            return {
                "success": True,
                "message": message,
                "appointment_id": str(appointment.id),
                "appointment_datetime": appointment.appointment_datetime.isoformat() if hasattr(appointment.appointment_datetime, 'isoformat') else str(appointment.appointment_datetime),
                "service_type": appointment.service_type,
                "google_event_id": appointment.google_event_id,
                "sync_status": appointment.sync_status
            }
        else:
            return {
                "success": False,
                "error": message,
                "message": f"No se pudo agendar: {message}"
            }

    except Exception as e:
        logger.error("Error en agendar_cita", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Error interno agendando cita"
        }


@tool
async def consultar_disponibilidad(
    date: str = Field(description="Fecha a consultar en ISO 8601 (ej: 2025-01-15)"),
    service_type: Optional[str] = Field(None, description="Tipo de servicio (opcional)")
) -> Dict[str, Any]:
    """
    Consulta horarios disponibles para agendar cita.

    Mira slots libres en Google Calendar y citas pendientes en DB.

    Returns:
        Dict con lista de slots disponibles
    """
    try:
        appointment_service = AppointmentService()

        # Parsear fecha
        try:
            target_date = datetime.fromisoformat(date)
        except Exception as e:
            return {
                "success": False,
                "error": f"Formato de fecha inválido: {str(e)}",
                "available_slots": []
            }

        # Duración del servicio
        duration = 60
        if service_type:
            try:
                from config.calendar_mapping import get_duration_for_service
                duration = get_duration_for_service(service_type)
            except Exception:
                duration = 60

        from db import get_async_session
        async with get_async_session() as session:
            slots = await appointment_service.get_available_slots(
                session=session,
                date=target_date,
                duration_minutes=duration
            )

        formatted_slots = []
        for slot in slots:
            formatted_slots.append({
                "start": slot.start.isoformat() if hasattr(slot.start, 'isoformat') else str(slot.start),
                "end": slot.end.isoformat() if hasattr(slot.end, 'isoformat') else str(slot.end),
                "available": slot.available
            })

        return {
            "success": True,
            "date": target_date.date().isoformat(),
            "service_type": service_type,
            "duration_minutes": duration,
            "total_available": len(formatted_slots),
            "available_slots": formatted_slots
        }

    except Exception as e:
        logger.error("Error en consultar_disponibilidad", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "available_slots": []
        }


@tool
async def obtener_citas_cliente(
    phone_number: str = Field(description="Número de teléfono del cliente"),
    status: str = Field("scheduled", description="Estado: scheduled, cancelled, completed, all")
) -> Dict[str, Any]:
    """
    Obtiene todas las citas de un cliente.

    Returns:
        Dict con lista de citas
    """
    try:
        appointment_service = AppointmentService()

        from db import get_async_session
        async with get_async_session() as session:
            appointments = await appointment_service.get_appointments_by_phone(
                session=session,
                phone_number=phone_number,
                status=status if status != "all" else None
            )

        formatted = []
        for appt in appointments:
            formatted.append({
                "id": str(appt.id),
                "appointment_datetime": appt.appointment_datetime.isoformat() if hasattr(appt.appointment_datetime, 'isoformat') else str(appt.appointment_datetime),
                "service_type": appt.service_type,
                "status": appt.status,
                "notes": appt.notes,
                "google_event_id": appt.google_event_id
            })

        return {
            "success": True,
            "phone_number": phone_number,
            "count": len(formatted),
            "appointments": formatted
        }

    except Exception as e:
        logger.error("Error en obtener_citas_cliente", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "appointments": []
        }


@tool
async def cancelar_cita(
    appointment_id: str = Field(description="ID de la cita a cancelar (UUID)")
) -> Dict[str, Any]:
    """
    Cancela una cita existente en Google Calendar y DB.

    Returns:
        Dict con confirmación
    """
    try:
        # Validar UUID
        try:
            appt_uuid = uuid.UUID(appointment_id)
        except ValueError:
            return {
                "success": False,
                "error": "ID de cita inválido",
                "message": "El ID debe ser un UUID válido"
            }

        appointment_service = AppointmentService()

        from db import get_async_session
        async with get_async_session() as session:
            success, message = await appointment_service.cancel_appointment(
                session=session,
                appointment_id=appt_uuid
            )

        return {
            "success": success,
            "message": message,
            "appointment_id": appointment_id
        }

    except Exception as e:
        logger.error("Error en cancelar_cita", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Error cancelando cita"
        }


@tool
async def enviar_mensaje_whatsapp(
    to: str = Field(description="Número de teléfono destino (formato internacional)"),
    text: str = Field(description="Texto del mensaje"),
    buttons: Optional[List[Dict[str, str]]] = Field(None, description="Botones interactivos (max 3)")
) -> Dict[str, Any]:
    """
    Envía mensaje de WhatsApp.

    Usar para confirmaciones, recordatorios, notificaciones.
    Respeta horarios laborales (9:00-18:00).
    """
    try:
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
            "to": to,
            "text_length": len(text)
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
    phone_number: str = Field(description="Número de teléfono del usuario")
) -> Dict[str, Any]:
    """
    Obtiene perfil de usuario desde memoria a largo plazo.

    Incluye: preferencias, notas, hechos extraídos, last_seen, total_conversations.
    """
    try:
        memory_manager = MemoryManager()
        project_id = None  # TODO: obtener desde contexto

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
    phone_number: str = Field(description="Número de teléfono"),
    preferences: Optional[Dict[str, Any]] = Field(None, description="Preferencias a actualizar"),
    notes: Optional[str] = Field(None, description="Notas adicionales"),
    extracted_facts: Optional[Dict[str, Any]] = Field(None, description="Hechos extraídos de la conversación")
) -> Dict[str, Any]:
    """
    Actualiza perfil de usuario con nueva información.

    Campos opcionales: solo actualiza los proporcionados.
    preferences: merge con existentes
    notes: concatena con notas previas
    extracted_facts: merge con existentes
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

        profile = await memory_manager.create_or_update_profile(
            phone_number=phone_number,
            project_id=project_id,
            **update_data
        )

        # Actualizar contador y last_seen
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
    query: str = Field(description="Consulta de búsqueda"),
    k: int = Field(5, description="Número de resultados (1-20)", ge=1, le=20),
    similarity_threshold: float = Field(0.7, description="Umbral mínimo de similitud (0-1)", ge=0, le=1)
) -> Dict[str, Any]:
    """
    Busca en la base de conocimientos (Supabase vector store).

    Ideal para: servicios, precios, políticas, cuidados, procedimientos.
    """
    try:
        try:
            from utils.langchain_components import LangChainComponentFactory
            vectorstore = LangChainComponentFactory.create_supabase_vectorstore()
        except Exception as e:
            logger.warning("Vectorstore no disponible", error=str(e))
            return {
                "status": "error",
                "error": "Base de conocimientos no disponible",
                "documents": []
            }

        docs = vectorstore.similarity_search_with_relevance_scores(
            query=query,
            k=k
        )

        filtered = [
            {
                "content": doc.page_content[:500],
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
    thought: str = Field(description="Pensamiento a estructurar"),
    context: Optional[str] = Field(None, description="Contexto adicional"),
    focus_areas: Optional[List[str]] = Field(None, description="Áreas específicas a considerar")
) -> str:
    """
    Razonamiento estructurado para problemas complejos.

    Analiza: problema, información disponible, opciones, riesgos, decisión.
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
    task: str = Field(description="Tarea a planificar"),
    constraints: Optional[str] = Field(None, description="Restricciones o consideraciones"),
    max_steps: int = Field(10, description="Máximo número de pasos", ge=1, le=50)
) -> Dict[str, Any]:
    """
    Planifica tareas complejas descomponiéndolas en pasos ejecutables.

    Returns:
        Dict con plan estructurado
    """
    plan = {
        "task": task,
        "constraints": constraints or "Ninguna",
        "steps": [],
        "estimated_time": "TBD",
        "dependencies": []
    }

    # Placeholder: dividir en pasos lógicos (en futuro integrar LLM)
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


# Factory - NO crea instancias de BaseTool manualmente, solo devuelve funciones decorated
def get_arcadium_tools() -> List:
    """
    Retorna lista de herramientas Arcadium.

    Nota: Estas herramientas ya están decoradas con @tool y son callables.

    Returns:
        Lista de herramientas
    """
    tools = [
        agendar_cita,
        consultar_disponibilidad,
        obtener_citas_cliente,
        cancelar_cita,
        enviar_mensaje_whatsapp,
        obtener_perfil_usuario,
        actualizar_perfil_usuario,
        knowledge_base_search,
        think,
        planificador_obligatorio
    ]

    logger.info("Herramientas Arcadium creadas", count=len(tools))
    return tools
