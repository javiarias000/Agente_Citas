#!/usr/bin/env python3
"""
Admin API endpoints - Todos requieren X-API-Key
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
import hashlib

from db.models import Project, ProjectAgentConfig, Conversation, Message, Appointment, AgentToggle, ToolCallLog, LangchainMemory
from core.orchestrator import ArcadiumAPI
from services.project_appointment_service import ProjectAppointmentService

logger = structlog.get_logger("admin_api")

router = APIRouter(prefix="/api/v1", tags=["admin"])


# ============================================
# DEPENDENCIES
# ============================================

async def get_db_session(request: Request) -> AsyncSession:
    api: ArcadiumAPI = request.app.state.api
    async with api.db.get_session() as session:
        yield session


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    request: Request = None
) -> Project:
    """Verifica API key y devuelve el proyecto."""
    api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    api: ArcadiumAPI = request.app.state.api
    async with api.db.get_session() as session:
        stmt = select(Project).where(Project.api_key == api_key_hash)
        result = await session.execute(stmt)
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if not project.is_active:
            raise HTTPException(status_code=403, detail="Project deactivated")
        return project


# ============================================
# PROJECT & CONFIG
# ============================================

@router.get("/projects/current")
async def get_current_project(
    project: Project = Depends(verify_api_key)
) -> Dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "is_active": project.is_active,
        "whatsapp_webhook_url": project.whatsapp_webhook_url,
        "settings": project.settings,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat()
    }


@router.get("/agent/config")
async def get_agent_config(
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(ProjectAgentConfig).where(ProjectAgentConfig.project_id == project.id)
    result = await db_session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        config = await _create_default_config(project, db_session)
    return _config_to_dict(config)


@router.put("/agent/config")
async def update_agent_config(
    updates: Dict[str, Any],
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(ProjectAgentConfig).where(ProjectAgentConfig.project_id == project.id)
    result = await db_session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Agent config not found")

    allowed_fields = [
        'system_prompt', 'custom_instructions', 'max_iterations',
        'temperature', 'enabled_tools', 'calendar_enabled',
        'google_calendar_id', 'calendar_timezone', 'calendar_mapping',
        'global_agent_enabled'
    ]
    for field in allowed_fields:
        if field in updates:
            setattr(config, field, updates[field])

    await db_session.flush()
    await db_session.commit()
    return {"status": "success", "message": "Agent config updated"}


# ============================================
# CONVERSATIONS
# ============================================

@router.get("/conversations")
async def list_conversations(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(Conversation).where(Conversation.project_id == project.id)
    if status:
        stmt = stmt.where(Conversation.status == status)
    stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
    result = await db_session.execute(stmt)
    conversations = result.scalars().all()

    conv_list = []
    for conv in conversations:
        last_msg = await _get_last_message(db_session, conv.id)
        conv_list.append({
            "id": str(conv.id),
            "phone_number": conv.phone_number,
            "status": conv.status,
            "agent_enabled": conv.agent_enabled,
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
            "last_message": last_msg.content if last_msg else None,
            "last_message_at": last_msg.created_at.isoformat() if last_msg else None
        })
    return {"conversations": conv_list, "total": len(conv_list), "limit": limit, "offset": offset}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(Conversation).where(
        and_(Conversation.id == conversation_id, Conversation.project_id == project.id)
    )
    result = await db_session.execute(stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": str(conv.id),
        "project_id": str(conv.project_id),
        "phone_number": conv.phone_number,
        "platform": conv.platform,
        "status": conv.status,
        "agent_enabled": conv.agent_enabled,
        "metadata": conv.meta_data,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat()
    }


@router.post("/conversations/{conversation_id}/agent-toggle")
async def toggle_agent_conversation(
    conversation_id: uuid.UUID,
    enabled: bool,
    reason: Optional[str] = None,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(Conversation).where(
        and_(Conversation.id == conversation_id, Conversation.project_id == project.id)
    )
    result = await db_session.execute(stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.agent_enabled = enabled
    toggle_stmt = select(AgentToggle).where(AgentToggle.conversation_id == conversation_id)
    toggle_result = await db_session.execute(toggle_stmt)
    toggle = toggle_result.scalar_one_or_none()

    if toggle:
        toggle.is_enabled = enabled
        toggle.updated_at = datetime.utcnow()
    else:
        toggle = AgentToggle(
            conversation_id=conversation_id,
            project_id=project.id,
            is_enabled=enabled,
            toggled_by="admin",
            reason=reason
        )
        db_session.add(toggle)

    await db_session.flush()
    await db_session.commit()
    return {"status": "success", "conversation_id": str(conversation_id), "agent_enabled": enabled}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    conv_stmt = select(Conversation).where(
        and_(Conversation.id == conversation_id, Conversation.project_id == project.id)
    )
    conv_result = await db_session.execute(conv_stmt)
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    stmt = select(Message).where(
        Message.conversation_id == conversation_id
    ).order_by(Message.created_at).limit(limit).offset(offset)
    result = await db_session.execute(stmt)
    messages = result.scalars().all()

    return {
        "messages": [{
            "id": str(m.id),
            "direction": m.direction,
            "message_type": m.message_type,
            "content": m.content,
            "processed": m.processed,
            "agent_response": m.agent_response,
            "tool_calls": m.tool_calls,
            "execution_time_ms": m.execution_time_ms,
            "created_at": m.created_at.isoformat()
        } for m in messages],
        "total": len(messages)
    }


# ============================================
# MEMORY
# ============================================

@router.get("/conversations/{conversation_id}/memory")
async def get_memory_visualization(
    conversation_id: uuid.UUID,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    conv_stmt = select(Conversation).where(
        and_(Conversation.id == conversation_id, Conversation.project_id == project.id)
    )
    result = await db_session.execute(conv_stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memory_records = select(LangchainMemory).where(
        and_(
            LangchainMemory.project_id == project.id,
            LangchainMemory.session_id == conv.phone_number
        )
    ).order_by(LangchainMemory.created_at)
    mem_result = await db_session.execute(memory_records)
    memories = mem_result.scalars().all()

    return {
        "conversation_id": str(conv.id),
        "phone_number": conv.phone_number,
        "project_id": str(project.id),
        "timeline": [{"id": m.id, "type": m.type, "content": m.content, "created_at": m.created_at.isoformat()} for m in memories],
        "message_count": len(memories)
    }


@router.delete("/conversations/{conversation_id}/memory")
async def clear_conversation_memory(
    conversation_id: uuid.UUID,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    conv_stmt = select(Conversation).where(
        and_(Conversation.id == conversation_id, Conversation.project_id == project.id)
    )
    result = await db_session.execute(conv_stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    delete_stmt = LangchainMemory.__table__.delete().where(
        and_(
            LangchainMemory.project_id == project.id,
            LangchainMemory.session_id == conv.phone_number
        )
    )
    await db_session.execute(delete_stmt)
    await db_session.commit()
    return {"status": "success", "message": "Memory cleared"}


# ============================================
# APPOINTMENTS
# ============================================

@router.get("/appointments")
async def list_appointments(
    phone_number: Optional[str] = None,
    status: Optional[str] = None,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    stmt = select(Appointment).where(Appointment.project_id == project.id)
    if phone_number:
        stmt = stmt.where(Appointment.phone_number == phone_number)
    if status:
        stmt = stmt.where(Appointment.status == status)
    stmt = stmt.order_by(Appointment.appointment_date.desc())
    result = await db_session.execute(stmt)
    appointments = result.scalars().all()
    return [{
        "id": str(a.id),
        "phone_number": a.phone_number,
        "appointment_date": a.appointment_date.isoformat(),
        "service_type": a.service_type,
        "status": a.status,
        "notes": a.notes,
        "google_event_id": a.google_event_id,
        "sync_status": a.sync_status,
        "created_at": a.created_at.isoformat()
    } for a in appointments]


@router.post("/appointments")
async def create_appointment_manual(
    phone_number: str = Body(...),
    appointment_datetime: str = Body(...),
    service_type: str = Body(...),
    notes: Optional[str] = Body(None),
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    stmt = select(ProjectAgentConfig).where(ProjectAgentConfig.project_id == project.id)
    result = await db_session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Project agent config not found")

    service = ProjectAppointmentService(config)
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(appointment_datetime)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format. Use ISO 8601")

    success, message, appointment = await service.create_appointment(
        session=db_session,
        phone_number=phone_number,
        appointment_datetime=dt,
        service_type=service_type,
        notes=notes
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "message": message, "appointment_id": str(appointment.id)}


# ============================================
# TOOLS & STATS
# ============================================

@router.get("/tools")
async def list_tools(
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    """
    Lista todas las herramientas disponibles en el sistema.
    Devuelve información desde la configuración del agente del proyecto.
    """
    try:
        # Obtener configuración del agente
        stmt = select(ProjectAgentConfig).where(ProjectAgentConfig.project_id == project.id)
        result = await db_session.execute(stmt)
        config = result.scalar_one_or_none()

        if not config:
            # Si no hay config, usar lista por defecto
            config = await _create_default_config(project, db_session)

        # Devolver herramientas desde la configuración
        tools_info = []
        for tool_name in config.enabled_tools:
            # Descripciones hardcodeadas por ahora
            descriptions = {
                "agendar_cita": "Agenda una nueva cita para un cliente con fecha, hora y tipo de servicio",
                "consultar_disponibilidad": "Consulta horarios disponibles para agendar una cita",
                "obtener_citas_cliente": "Obtiene las citas del cliente actual (última o histórico)",
                "cancelar_cita": "Cancela una cita agendada por ID",
                "reagendar_cita": "Reagenda una cita existente a nueva fecha/hora"
            }
            tools_info.append({
                "name": tool_name,
                "description": descriptions.get(tool_name, "Herramienta del agente")
            })

        return tools_info
    except Exception as e:
        logger.error("Error listing tools", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load tools")


@router.get("/stats")
async def get_project_stats(
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    conv_count = await db_session.scalar(
        select(func.count()).select_from(Conversation).where(
            and_(Conversation.project_id == project.id, Conversation.status == "active")
        )
    ) or 0
    from datetime import date, datetime
    today = datetime.combine(date.today(), datetime.min.time())
    msg_count = await db_session.scalar(
        select(func.count()).select_from(Message).where(
            and_(Message.project_id == project.id, Message.created_at >= today)
        )
    ) or 0
    appt_count = await db_session.scalar(
        select(func.count()).select_from(Appointment).where(
            and_(Appointment.project_id == project.id, Appointment.status == "scheduled")
        )
    ) or 0
    return {
        "project_id": str(project.id),
        "active_conversations": conv_count,
        "messages_today": msg_count,
        "scheduled_appointments": appt_count,
        "generated_at": datetime.utcnow().isoformat()
    }


@router.get("/audit/logs")
async def get_audit_logs(
    tool_name: Optional[str] = None,
    limit: int = 100,
    project: Project = Depends(verify_api_key),
    db_session: AsyncSession = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    stmt = select(ToolCallLog).where(ToolCallLog.project_id == project.id)
    if tool_name:
        stmt = stmt.where(ToolCallLog.tool_name == tool_name)
    stmt = stmt.order_by(ToolCallLog.created_at.desc()).limit(limit)
    result = await db_session.execute(stmt)
    logs = result.scalars().all()
    return [{
        "id": str(l.id),
        "project_id": str(l.project_id),
        "session_id": l.session_id,
        "tool_name": l.tool_name,
        "input_data": l.input_data,
        "output_data": l.output_data,
        "success": l.success,
        "error_message": l.error_message,
        "execution_time_ms": l.execution_time_ms,
        "created_at": l.created_at.isoformat()
    } for l in logs]


# ============================================
# HELPERS
# ============================================

async def _get_last_message(db_session: AsyncSession, conversation_id: uuid.UUID) -> Optional[Message]:
    stmt = select(Message).where(Message.conversation_id == conversation_id)\
        .order_by(Message.created_at.desc()).limit(1)
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


def _config_to_dict(config: ProjectAgentConfig) -> Dict[str, Any]:
    return {
        "id": str(config.id),
        "project_id": str(config.project_id),
        "agent_name": config.agent_name,
        "system_prompt": config.system_prompt,
        "custom_instructions": config.custom_instructions,
        "max_iterations": config.max_iterations,
        "temperature": float(config.temperature),
        "enabled_tools": config.enabled_tools,
        "calendar_enabled": config.calendar_enabled,
        "google_calendar_id": config.google_calendar_id,
        "calendar_timezone": config.calendar_timezone,
        "calendar_mapping": config.calendar_mapping,
        "global_agent_enabled": config.global_agent_enabled,
        "created_at": config.created_at.isoformat(),
        "updated_at": config.updated_at.isoformat()
    }


async def _create_default_config(project: Project, session: AsyncSession) -> ProjectAgentConfig:
    config = ProjectAgentConfig(
        project_id=project.id,
        agent_name="DeyyAgent",
        system_prompt=f"Eres Deyy, un asistente especializado de Arcadium para gestión de citas.\n\n"
                      f"Proyecto: {project.name}\n\n"
                      "Ayuda a los clientes con gestión de citas y consultas.",
        max_iterations=10,
        temperature=0.7,
        enabled_tools=["agendar_cita", "consultar_disponibilidad", "obtener_citas_cliente", "cancelar_cita", "reagendar_cita"],
        calendar_enabled=False,
        global_agent_enabled=True
    )
    session.add(config)
    await session.flush()
    await session.commit()
    return config
