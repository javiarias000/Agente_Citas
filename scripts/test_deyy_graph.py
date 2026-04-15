#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test simple de DeyyAgent con StateGraph (sin DB)
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from core.store import ArcadiumStore
from agents.deyy_agent import DeyyAgent
from core.config import get_settings


async def test_deyy_agent_basic():
    """Test básico: crear agente y procesar un mensaje simple"""
    print("🧪 Test DeyyAgent con StateGraph...")

    # 1. Setup
    settings = get_settings()

    # Mock MemoryManager (para ArcadiumStore)
    class MockMemoryManager:
        async def get_history(self, session_id):
            return []
        async def add_message(self, session_id, content, message_type, project_id=None):
            print(f"  [Mock] add_message: {session_id} - {message_type}: {content[:50]}...")
        async def clear_session(self, session_id):
            pass
        async def get_user_profile(self, phone, project_id):
            return None
        async def create_or_update_profile(self, phone, project_id, **updates):
            return MagicMock(id=uuid.uuid4(), phone_number=phone, project_id=project_id, **updates)
        async def update_user_last_seen(self, phone, project_id):
            pass
        async def increment_user_conversation_count(self, phone, project_id):
            pass
        async def extract_and_save_facts_from_conversation(self, phone, project_id, user_msg, agent_msg):
            pass

    mock_mm = MockMemoryManager()
    store = ArcadiumStore(mock_mm)

    # 2. Crear agente
    agent = DeyyAgent(
        session_id="+34612345678",
        store=store,
        project_id=uuid.uuid4(),
        verbose=False
    )

    # 3. Inicializar (crea grafo)
    print("  Inicializando agente...")
    await agent.initialize()
    print(f"  ✅ Grafo creado: {type(agent._graph).__name__}")

    # 4. Procesar un mensaje simple
    print("  Procesando mensaje...")
    result = await agent.process_message("Hola, quiero agendar una cita")

    print(f"  ✅ Respuesta: {result.get('response', '')[:100]}...")
    print(f"  Status: {result.get('status')}")

    if result.get('status') == 'success':
        print("✅ Test pasó: DeyyAgent con StateGraph funciona")
        return True
    else:
        print(f"❌ Test falló: {result}")
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(test_deyy_agent_basic())
        exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Error en test: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
