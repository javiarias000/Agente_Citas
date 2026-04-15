#!/usr/bin/env python3
"""
Test rápido para verificar que no se duplican mensajes en múltiples turnos.
"""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from memory.memory_manager import MemoryManager
from core.config import Settings
from core.store import ArcadiumStore
from agents.state_machine_agent import StateMachineAgent
from agents.context_vars import set_current_phone, reset_phone
from langchain_core.messages import HumanMessage, AIMessage
import uuid

async def main():
    # Configurar settings en memoria
    settings = Settings(
        USE_POSTGRES_FOR_MEMORY=False,
        OPENAI_TEMPERATURE=0.0,
        GOOGLE_CALENDAR_ENABLED=False,
        DATABASE_URL="sqlite+aiosqlite:///:memory:"
    )
    # Inicializar DB
    from sqlalchemy.ext.asyncio import create_async_engine
    from db import init_session_maker, create_all
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    init_session_maker(engine)
    await create_all(engine)
    # Crear proyecto
    from db.models import Project
    from db import get_async_session
    async with get_async_session() as session:
        project = Project(
            name="Test Project",
            slug="test-project",
            api_key="test_api_key",
            is_active=True,
            settings={}
        )
        session.add(project)
        await session.commit()
        project_id = project.id

    memory_manager = MemoryManager(settings)
    store = ArcadiumStore(memory_manager)
    session_id = "+1234567890"

    agent = StateMachineAgent(
        session_id=session_id,
        store=store,
        project_id=project_id,
        verbose=False
    )

    phone_token = set_current_phone(session_id)
    try:
        # Turno 1
        print("=== Turno 1 ===")
        result1 = await agent.process_message("Hola, quiero una limpieza")
        print(f"Estado: current_step={result1.get('current_step')}, intent={result1.get('intent')}")
        history = await store.get_history(session_id)
        print(f"Historial after turno 1: {len(history)} msgs")
        for i, msg in enumerate(history):
            print(f"  {i}: {msg.__class__.__name__}: {msg.content[:50]!r}")

        # Turno 2
        print("\n=== Turno 2 ===")
        result2 = await agent.process_message("Limpieza dental")
        print(f"Estado: current_step={result2.get('current_step')}, selected_service={result2.get('selected_service')}")
        history = await store.get_history(session_id)
        print(f"Historial after turno 2: {len(history)} msjs")
        for i, msg in enumerate(history):
            print(f"  {i}: {msg.__class__.__name__}: {msg.content[:50]!r}")

        # Turno 3
        print("\n=== Turno 3 ===")
        result3 = await agent.process_message("El viernes a las 3pm")
        print(f"Estado: current_step={result3.get('current_step')}, datetime_preference={result3.get('datetime_preference')}")
        history = await store.get_history(session_id)
        print(f"Historial after turno 3: total {len(history)} msjs")
        # Print each
        for i, msg in enumerate(history):
            print(f"  {i}: {msg.__class__.__name__}: {msg.content[:50]!r}")

        # Analizar duplicados
        print("\n=== Análisis de duplicados ===")
        contents = []
        for msg in history:
            if isinstance(msg, (HumanMessage, AIMessage)):
                contents.append((msg.__class__.__name__, msg.content.strip()))
        # Contar repeticiones de cada contenido exacto
        from collections import Counter
        counts = Counter(contents)
        for (role, content), cnt in counts.most_common():
            if cnt > 1:
                print(f"DUPLICADO: {role} \"{content[:80]}\" aparece {cnt} veces")

        total_duplicates = sum(cnt-1 for cnt in counts.values() if cnt>1)
        print(f"\nTotal mensajes duplicados (repeticiones): {total_duplicates}")
        print(f"Mensajes únicos: {len(counts)} de {len(contents)} totales")

    finally:
        reset_phone(phone_token)

if __name__ == "__main__":
    asyncio.run(main())
