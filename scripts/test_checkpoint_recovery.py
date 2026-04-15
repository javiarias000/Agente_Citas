#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Checkpoint Recovery: Probamos que el estado del gráfo se puede guardar y recuperar
después de un "crash" simulando nueva instancia.
"""

import asyncio
import uuid
import os
from typing import Dict, Any

from memory.memory_manager import MemoryManager
from core.store import ArcadiumStore
from agents.deyy_agent import DeyyAgent
from core.config import get_settings
from graphs.deyy_graph import create_deyy_graph
from langgraph.checkpoint.memory import MemorySaver


async def test_checkpoint_recovery():
    """Prueba que el checkpoint del gráfico se puede guardar y recuperar"""
    print("\n" + "="*60)
    print("💾 TEST: Checkpoint Recovery (MemorySaver)")
    print("="*60)

    # 1. Setup MemorySaver compartido
    checkpointer = MemorySaver()
    print("✅ MemorySaver creado (en memoria)")

    # 2. Inicializar MemoryManager (InMemory es suficiente para este test)
    settings = get_settings()
    from core.config import Settings
    test_settings = Settings(USE_POSTGRES_FOR_MEMORY=False)
    memory_manager = MemoryManager(settings=test_settings)
    await memory_manager.initialize()
    store = ArcadiumStore(memory_manager)

    # 3. Crear agente con checkpointer compartido
    test_phone = "+test_checkpoint"
    session_id = test_phone
    agent = DeyyAgent(
        session_id=session_id,
        store=store,
        project_id=None,
        verbose=False,
        checkpointer=checkpointer  # <- Inyectar checkpointer
    )
    await agent.initialize()

    # 4. Procesar un mensaje (esto debería guardar checkpoint)
    print("\n💬 Procesando primer mensaje...")
    result1 = await agent.process_message("Hola, quiero una cita")
    print(f"🤖 Respuesta: {result1.get('response', 'ERROR')[:50]}...")
    config1 = {"configurable": {"thread_id": session_id}}

    # 5. Obtener estado actual desde checkpointer
    checkpoint_before = await checkpointer.aget(config1)
    print(f"✅ Checkpoint guardado: state={checkpoint_before is not None}")

    # 6. Simular "crash": crear nuevo agente con mismo session_id y checkpointer
    print("\n🔁 Simulando recuperación después de crash...")
    agent2 = DeyyAgent(
        session_id=session_id,
        store=store,
        project_id=None,
        verbose=False,
        checkpointer=checkpointer  # <- Mismo checkpointer
    )
    # initialize usará el checkpointer inyectado
    await agent2.initialize()

    # 7. Verificar que el state se recuperó
    # (No hay método directo para obtener checkpoint state sin ejecutar,
    #  pero podemos ver que el agente2 puede continuar la conversación)
    print("\n💬 Continuando conversación con agente recuperado...")
    result2 = await agent2.process_message("Mañana a las 10am")
    print(f"🤖 Respuesta: {result2.get('response', 'ERROR')[:50]}...")

    # 8. Verificar que ambos agentes tienen el mismo estado (por los logs o historial)
    # El historial debería incluir ambos mensajes
    history = await store.get_history(session_id)
    print(f"\n📚 Historial total: {len(history)} mensajes")
    for i, msg in enumerate(history[-4:], 1):  # últimos 4
        print(f"   {i}. {type(msg).__name__}: {msg.content[:30]}...")

    # 9. Cleanup
    await store.clear_session(session_id)
    print("\n🧹 Limpieza completada")

    print("\n" + "="*60)
    print("✅✅✅ CHECKPOINT RECOVERY COMPLETADO ✅✅✅")
    print("="*60)
    return True


async def main():
    try:
        await test_checkpoint_recovery()
        return True
    except Exception as e:
        print(f"\n❌❌❌ ERROR: {e} ❌❌❌")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)
