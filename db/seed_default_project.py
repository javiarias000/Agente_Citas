#!/usr/bin/env python3
"""
Seed script: Crea proyecto 'default' y migra datos existentes
"""

import asyncio
import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from db.models import (
    Base, Project, ProjectAgentConfig, Conversation, Message,
    Appointment, ToolCallLog, LangchainMemory, User
)
from core.config import get_settings
from utils.logger import setup_logger
import secrets
import hashlib
import json

logger = setup_logger("INFO")  # Usar nivel INFO estándar


async def generate_api_key() -> str:
    """Genera una API key aleatoria"""
    raw = secrets.token_urlsafe(32)
    # Podríamos hashearla en DB, pero por ahora guardamos plana (luego proteger)
    return raw


async def hash_api_key(api_key: str) -> str:
    """Hashea API key para almacenamiento seguro"""
    return hashlib.sha256(api_key.encode()).hexdigest()


async def create_default_project(session: AsyncSession) -> Project:
    """
    Crea el proyecto 'default' si no existe.
    """
    # Verificar si ya existe proyecto 'default'
    stmt = select(Project).where(Project.slug == "default")
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        logger.info("Proyecto 'default' ya existe", project_id=str(existing.id))
        return existing

    # Generar API key
    api_key = await generate_api_key()
    api_key_hash = await hash_api_key(api_key)

    project = Project(
        name="Default Project",
        slug="default",
        api_key=api_key_hash,  # Guardamos hash
        is_active=True,
        whatsapp_webhook_url=None,
        settings={
            "default": True,
            "created_by_seed": True
        }
    )
    session.add(project)
    await session.flush()
    await session.commit()

    logger.info(
        "Proyecto 'default' creado",
        project_id=str(project.id),
        api_key_plain=api_key  # mostrar solo una vez
    )
    logger.warning("GUARDA ESTA API KEY: ¡No se volverá a mostrar!", api_key=api_key)

    # Crear configuración de agente por defecto
    config = ProjectAgentConfig(
        project_id=project.id,
        agent_name="DeyyAgent",
        system_prompt=f"""Eres Deyy, un asistente especializado de Arcadium para gestión de citas de la clínica dental.

Tu personalidad:
- Profesional pero amigable
- Respetuoso y empático
- Claro y conciso
- Proactivo
- Preciso con fechas y horarios

Gestión de Calendario:
- Disponibilidad en TIEMPO REAL desde Google Calendar (si está configurado)
- Horario laboral: Lunes-Viernes, 9:00-18:00
- Citas según duración del servicio
- Todos los horarios en timezone America/Guayaquil

Tus capacidades (herramientas):
1. consultar_disponibilidad(fecha, servicio_opcional)
2. agendar_cita(fecha, servicio, notas_opcional)
3. obtener_citas_cliente(historico_opcional)
4. cancelar_cita(appointment_id_opcional)
5. reagendar_cita(appointment_id_opcional, nueva_fecha, nuevas_notas_opcional)

Flujos recomendados siguen en documentación.

IMPORTANTE: Siempre valida fecha/hora antes de agendar. Pide confirmación explícita.
""",
        custom_instructions=None,
        max_iterations=10,
        temperature=0.7,
        enabled_tools=["agendar_cita", "consultar_disponibilidad", "obtener_citas_cliente", "cancelar_cita", "reagendar_cita"],
        calendar_enabled=False,
        google_calendar_id=None,
        calendar_timezone="America/Guayaquil",
        calendar_mapping={},
        global_agent_enabled=True
    )
    session.add(config)
    await session.flush()
    await session.commit()

    logger.info("ProjectAgentConfig creada para proyecto default", project_id=str(project.id))

    # Crear usuario admin por defecto (password: admin123 - cambiar luego)
    # Hashear password con bcrypt (simulado por ahora)
    dummy_hash = "$2b$12$dummy_hash_for_default_admin"  # reemplazar con bcrypt real
    admin_user = User(
        email="admin@arcadium.local",
        hashed_password=dummy_hash,
        name="Administrator",
        role="admin",
        is_active=True,
        last_login=None
    )
    session.add(admin_user)
    await session.flush()

    # Asociar usuario admin al proyecto default
    from db.models import UserProject
    user_project = UserProject(
        user_id=admin_user.id,
        project_id=project.id,
        role_in_project="admin"
    )
    session.add(user_project)
    await session.commit()

    logger.info("Usuario admin creado y asignado", user_id=str(admin_user.id))

    return project


async def migrate_existing_data(session: AsyncSession, default_project: Project):
    """
    Migra datos existentes asignando project_id default
    """
    logger.info("Iniciando migración de datos existentes al proyecto default")

    # 1. Conversations sin project_id
    stmt = select(Conversation).where(Conversation.project_id.is_(None))
    result = await session.execute(stmt)
    convs = result.scalars().all()
    for conv in convs:
        conv.project_id = default_project.id
    logger.info(f"Actualizadas {len(convs)} conversations")
    await session.flush()

    # 2. Messages sin project_id (inferir de conversation)
    stmt = select(Message).where(Message.project_id.is_(None))
    result = await session.execute(stmt)
    msgs = result.scalars().all()
    for msg in msgs:
        # Buscar conversation
        conv_stmt = select(Conversation).where(Conversation.id == msg.conversation_id)
        conv_res = await session.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if conv and conv.project_id:
            msg.project_id = conv.project_id
    logger.info(f"Actualizadas {len(msgs)} messages")
    await session.flush()

    # 3. Appointments sin project_id (inferir de phone? o crear mapping).
    # Como Appointment no tiene conversation_id, buscamos por phone_number y project correspondiente
    stmt = select(Appointment).where(Appointment.project_id.is_(None))
    result = await session.execute(stmt)
    appts = result.scalars().all()
    for appt in appts:
        # Buscar conversation del mismo phone para inferir project
        conv_stmt = select(Conversation).where(
            Conversation.phone_number == appt.phone_number
        ).limit(1)
        conv_res = await session.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if conv and conv.project_id:
            appt.project_id = conv.project_id
        else:
            appt.project_id = default_project.id
    logger.info(f"Actualizados {len(appts)} appointments")
    await session.flush()

    # 4. ToolCallLogs sin project_id (inferir de session_id? difícil, asignar default)
    stmt = select(ToolCallLog).where(ToolCallLog.project_id.is_(None))
    result = await session.execute(stmt)
    logs = result.scalars().all()
    for log in logs:
        # Intentar obtener session_id que puede ser phone number
        # Asignar default si no se puede inferir
        log.project_id = default_project.id
    logger.info(f"Actualizados {len(logs)} tool_call_logs")
    await session.flush()

    # 5. LangchainMemory sin project_id (inferir de session_id)
    stmt = select(LangchainMemory).where(LangchainMemory.project_id.is_(None))
    result = await session.execute(stmt)
    memories = result.scalars().all()
    for mem in memories:
        # El session_id probablemente es "deyy_+phone". Intentar extraer phone
        # o buscar conversation correspondiente
        # Para simplificar, asignar default
        mem.project_id = default_project.id
    logger.info(f"Actualizados {len(memories)} langchain_memory records")
    await session.flush()

    await session.commit()
    logger.info("Migración completada")


async def main():
    """Función principal"""
    settings = get_settings()
    from db import get_async_session, init_session_maker
    from sqlalchemy.ext.asyncio import create_async_engine

    # Crear engine async y session maker
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20
    )
    init_session_maker(engine)

    async with get_async_session() as session:
        try:
            # Crear proyecto default
            default_project = await create_default_project(session)

            # Migrar datos existentes
            await migrate_existing_data(session, default_project)

            logger.info("✅ Seed completado exitosamente")
            print("\n" + "="*60)
            print("PROYECTO DEFAULT CREADO")
            print("="*60)
            print(f"Project ID: {default_project.id}")
            print(f"Project Slug: default")
            print("Revise los logs para la API Key generada.")
            print("IMPORTANTE: Guarde la API Key en un lugar seguro.")
            print("="*60 + "\n")

        except Exception as e:
            logger.error("Error en seed", error=str(e), exc_info=True)
            raise


if __name__ == "__main__":
    asyncio.run(main())
