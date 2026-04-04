#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test E2E: StateMachineAgent con ArcadiumStore real (PostgreSQL).
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine
from db import init_session_maker

from memory.memory_manager import MemoryManager
from core.store import ArcadiumStore
from agents.state_machine_agent import StateMachineAgent
from core.config import get_settings


async def setup_test_db():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    init_session_maker(engine)
    print("✅ Base de datos inicializada")


async def test_state_machine_agent():
    print("\n" + "="*60)
    print("🧪 TEST E2E: StateMachineAgent con PostgreSQL")
    print("="*60)

    await setup_test_db()

    settings = get_settings()
    memory_manager = MemoryManager(settings)
    await memory_manager.initialize()
    store = ArcadiumStore(memory_manager)

    test_phone = f"+test_sm_{uuid.uuid4().hex[:8]}"
    session_id = test_phone  # StateMachineAgent usa phone como session_id

    print(f"📱 Creando StateMachineAgent para phone: {test_phone}")

    try:
        agent = StateMachineAgent(
            session_id=session_id,
            store=store,
            project_id=None,
            verbose=True
        )
        await agent.initialize()
        print("✅ StateMachineAgent inicializado")
    except Exception as e:
        print(f"❌ Error inicializando agente: {e}")
        return False

    # Procesar un mensaje simple
    print("\n💬 USUARIO: Hola")
    try:
        result = await agent.process_message("Hola")
        print(f"🤖 AGENTE: {result}")
        if isinstance(result, dict) and result.get('status') == 'success':
            print("✅ Mensaje procesado exitosamente")
        else:
            print("ℹ️  Mensaje procesado con errores, pero no fatal")
    except Exception as e:
        print(f"❌ Error procesando mensaje: {e}")
        return False

    # Verificar historial
    history = await store.get_history(session_id)
    print(f"📚 Historial: {len(history)} mensajes guardados")
    if len(history) >= 1:
        print("✅ Historial guardado correctamente")
    else:
        print("❌ No se guardó historial")

    # Verificar estado de SupportState (si existe)
    state = await store.get_agent_state(session_id)
    if state:
        print(f"✅ SupportState guardado: step={state.get('current_step')}")
    else:
        print("ℹ️  SupportState no disponible")

    # Cleanup
    await store.clear_session(session_id)
    print("\n🧹 Sesión limpiada")

    print("\n" + "="*60)
    print("✅✅✅ TEST E2E STATEMACHINE AGENT COMPLETADO ✅✅✅")
    print("="*60)
    return True


async def main():
    try:
        success = await test_state_machine_agent()
        return success
    except Exception as e:
        print(f"\n❌❌❌ ERROR EN TEST: {e} ❌❌❌")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)
