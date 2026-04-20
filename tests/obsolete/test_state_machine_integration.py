#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de integración para StateMachineAgent.
Demuestra el flujo completo: reception → info_collector → scheduler → resolution.
"""

import asyncio
import pytest
import sys
from pathlib import Path

# Añadir directorio raíz al path (como hace conftest pero local)
sys.path.insert(0, str(Path(__file__).parents[1]))

from agents.state_machine_agent import StateMachineAgent
from memory.memory_manager import MemoryManager
from core.config import Settings
from core.store import ArcadiumStore
from agents.context_vars import set_current_phone, reset_phone


@pytest.fixture
def arcadium_store():
    """ArcadiumStore con MemoryManager en memoria para tests"""
    import builtins  # Importar para manipular DEFAULT_PROJECT_ID

    # Guardar settings original para restaurar después
    import core.config
    original_settings = getattr(core.config, '_settings', None)

    # Deshabilitar Google Calendar y usar DB en memoria para tests
    settings = Settings(
        USE_POSTGRES_FOR_MEMORY=False,
        OPENAI_TEMPERATURE=0.0,
        GOOGLE_CALENDAR_ENABLED=False,  # No usar Google Calendar en tests
        DATABASE_URL="sqlite+aiosqlite:///:memory:"  # DB en memoria
    )
    # Inyectar settings en el global de core.config para que get_settings() lo devuelva
    core.config._settings = settings

    # Inicializar DB manualmente para tests
    from sqlalchemy.ext.asyncio import create_async_engine
    from db import init_session_maker, create_all
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    init_session_maker(engine)
    # Crear tablas
    import asyncio
    asyncio.run(create_all(engine))

    # Crear un proyecto por defecto para tests (ya que appointments requiere project_id)
    from db.models import Project
    from db import get_async_session
    import uuid
    async def init_project():
        async with get_async_session() as session:
            project = Project(
                name="Test Project",
                slug="test-project",
                api_key="test_api_key_" + str(uuid.uuid4()).replace("-", "")[:16],
                is_active=True,
                settings={}
            )
            session.add(project)
            await session.commit()
            # Guardar project_id globalmente para usar en tests
            builtins.DEFAULT_PROJECT_ID = project.id
    asyncio.run(init_project())

    memory_manager = MemoryManager(settings)
    store = ArcadiumStore(memory_manager)

    yield store

    # Limpieza: restaurar settings original y quitar DEFAULT_PROJECT_ID
    if original_settings is not None:
        core.config._settings = original_settings
    else:
        if hasattr(core.config, '_settings'):
            delattr(core.config, '_settings')
    # Remover DEFAULT_PROJECT_ID de builtins si existe
    if hasattr(builtins, 'DEFAULT_PROJECT_ID'):
        delattr(builtins, 'DEFAULT_PROJECT_ID')


@pytest.mark.asyncio
async def test_full_agendar_flow(arcadium_store):
    """
    Test del flujo completo de agendamiento:
    1. Reception: clasifica intención "agendar"
    2. Info Collector: registra servicio y fecha
    3. Scheduler: consulta disponibilidad y agenda
    4. Resolution: confirma cita
    """
    session_id = "+1234567890"
    # Usar project_id del fixture (defecto)
    import builtins
    project_id = getattr(builtins, 'DEFAULT_PROJECT_ID', None)

    # Crear agente
    agent = StateMachineAgent(
        session_id=session_id,
        store=arcadium_store,
        project_id=project_id,
        verbose=False
    )

    # Configurar contexto
    phone_token = set_current_phone(session_id)
    try:
        # Turno 1: Usuario expresa intención de agendar
        response1 = await agent.process_message("Quiero agendar una limpieza dental")
        assert response1["status"] == "success"
        assert response1["current_step"] == "info_collector"
        state1 = response1["state"]
        assert state1["intent"] == "agendar"

        # Turno 2: Agregar servicio (y posiblemente fecha si el LLM la infiere)
        response2 = await agent.process_message("Limpieza")
        assert response2["status"] == "success"
        state2 = response2["state"]
        assert state2["selected_service"] == "limpieza"
        # El step puede ser info_collector o scheduler dependiendo si ya se registró la fecha
        assert state2["current_step"] in ["info_collector", "scheduler"]

        # Turno 3: Si aún no hay fecha, agregar fecha preferida
        response3 = await agent.process_message("El viernes a las 3pm")
        assert response3["status"] == "success"
        state3 = response3["state"]
        assert state3["datetime_preference"] is not None
        # Después de agregar fecha, debería estar en scheduler o resolution
        assert state3["current_step"] in ["scheduler", "resolution"]

        # Turno 4: Consultar disponibilidad y agendar (si no está ya en resolution)
        response4 = await agent.process_message("Agenda la cita")
        assert response4["status"] == "success"
        state4 = response4["state"]
        assert state4.get("appointment_id") is not None
        assert state4["current_step"] == "resolution"

        # Verificar historial
        history = await arcadium_store.get_history(session_id)
        assert len(history) >= 4  # Al menos 4 mensajes

    finally:
        reset_phone(phone_token)


@pytest.mark.asyncio
async def test_consultar_sin_agendar_flow(arcadium_store):
    """
    Flujo de solo consulta (sin agendar):
    Reception → Scheduler → Resolution
    """
    session_id = "+9876543210"
    project_id = None

    agent = StateMachineAgent(
        session_id=session_id,
        store=arcadium_store,
        project_id=project_id,
        verbose=False
    )

    phone_token = set_current_phone(session_id)
    try:
        # Turno 1: Consultar disponibilidad
        response1 = await agent.process_message("¿Qué disponibilidad hay esta semana?")
        assert response1["status"] == "success"
        state1 = response1["state"]
        assert state1["intent"] == "consultar"
        assert state1["current_step"] == "scheduler"

        # Turno 2: Agendar si hay slot (simulado)
        response2 = await agent.process_message("Agenda el primer slot")
        # Podría o no agendar dependiendo de datos mock, pero el flujo debe continuar
        assert response2["status"] == "success"

    finally:
        reset_phone(phone_token)


@pytest.mark.asyncio
async def test_cancelar_flow(arcadium_store):
    """
    Flujo de cancelación:
    Reception → Resolution (con cancelar_cita) → Reception
    """
    session_id = "+5555555555"
    project_id = None

    agent = StateMachineAgent(
        session_id=session_id,
        store=arcadium_store,
        project_id=project_id,
        verbose=False
    )

    phone_token = set_current_phone(session_id)
    try:
        # Turno 1: Usuario quiere cancelar
        response1 = await agent.process_message("Quiero cancelar mi cita")
        assert response1["status"] == "success"
        state1 = response1["state"]
        assert state1["intent"] == "cancelar"
        assert state1["current_step"] == "resolution"

        # Turno 2: Confirmar cancelación
        response2 = await agent.process_message("Sí, cancélala")
        assert response2["status"] == "success"
        state2 = response2["state"]
        assert state2["current_step"] == "reception"
        assert state2.get("appointment_id") is None

    finally:
        reset_phone(phone_token)


@pytest.mark.asyncio
async def test_state_persistence(arcadium_store):
    """
    Test que el estado se guarda y recupera correctamente desde MemoryManager.
    """
    session_id = "+1111111111"
    project_id = None

    agent1 = StateMachineAgent(
        session_id=session_id,
        store=arcadium_store,
        project_id=project_id,
        verbose=False
    )

    phone_token = set_current_phone(session_id)
    try:
        # Procesar un mensaje para generar estado
        await agent1.process_message("Quiere una consulta")
        state_after = await agent1.get_current_state()
        assert state_after["current_step"] == "info_collector"

        # Crear nuevo agente con misma session_id debe recuperar estado
        agent2 = StateMachineAgent(
            session_id=session_id,
            store=arcadium_store,
            project_id=project_id,
            verbose=False
        )

        state = await agent2.get_current_state()
        assert state is not None
        assert state["current_step"] == "info_collector"
        assert "intent" in state

    finally:
        reset_phone(phone_token)