#!/usr/bin/env python3
"""
Prueba con formato Evolution API (plano)
simula el webhook real de WhatsApp
"""

import asyncio
from core.orchestrator import ArcadiumAutomation

async def main():
    print("=" * 70)
    print("PRUEBA: FORMATO EVOLUTION API")
    print("=" * 70)

    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()

    # Simular webhook de Evolution API con diferentes formatos de sender
    test_cases = [
        {
            "sender": "+34612345678",
            "message": "Hola, quiero agendar cita",
            "desc": "Formato E.164 con +"
        },
        {
            "sender": "34612345678",
            "message": "Cuéntame más sobre sus servicios",
            "desc": "Sin +, con código país"
        },
        {
            "sender": "612345678",
            "message": "¿Qué citas tengo?",
            "desc": "Solo número nacional"
        },
    ]

    for idx, test in enumerate(test_cases, 1):
        payload = {
            "sender": test["sender"],
            "message": test["message"],
            "message_type": "text"
        }

        print(f"\n📨 Mensaje {idx}: {test['desc']}")
        print(f"   Sender raw: '{test['sender']}'")

        result = await orchestrator.process_webhook(payload)
        print(f"   Session ID: {result.get('session_id', 'N/A')}")
        print(f"   Respuesta (50 chars): {result.get('response', '')[:50]}...")

        await asyncio.sleep(0.3)

    # Verificar en DB
    print("\n" + "=" * 70)
    print("📊 VERIFICACIÓN EN DB")
    print("=" * 70)

    from core.orchestrator import Database
    from sqlalchemy import text

    db = Database(orchestrator.settings.DATABASE_URL)
    async with db.get_session() as session:
        res = await session.execute(
            text('SELECT DISTINCT session_id FROM langchain_memory ORDER BY session_id')
        )
        unique_sessions = [row[0] for row in res.fetchall()]
        print(f"Session IDs únicos: {unique_sessions}")

        if len(unique_sessions) == 1:
            print("\n✅ ÉXITO: Todos los mensajes en UNA sesión (normalización OK)")
        else:
            print("\n❌ FALLO: Múltiples sesiones → se pierde contexto")

    await orchestrator.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
