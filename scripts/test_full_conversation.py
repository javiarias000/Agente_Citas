#!/usr/bin/env python3
"""
Prueba completa: agendar cita, consultar, cancelar (en un solo proceso)
"""

import asyncio
import json
from core.orchestrator import ArcadiumAutomation

async def test_conversation():
    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()

    phone = "+34612345678"
    conversation_id = "test999"

    def make_payload(message, conv_id):
        return {
            "body": {
                "conversation": {
                    "messages": [
                        {
                            "sender": {
                                "phone_number": phone,
                                "name": "Test User"
                            },
                            "content": message
                        }
                    ]
                }
            },
            "account_id": 1,
            "conversation_id": conv_id
        }

    print("=" * 70)
    print("PRUEBA COMPLETA DE CONVERSACIÓN")
    print("=" * 70)

    # Paso 1: Agendar cita
    print("\n📅 PASO 1: Agendar cita")
    payload1 = make_payload("Quiero agendar una cita de mantenimiento para mañana a las 9am", 1)
    result1 = await orchestrator.process_webhook(payload1)
    print(f"   Respuesta: {result1.get('agent_response', '')[:150]}...")
    print(f"   Herramientas: {[t['tool'] for t in result1.get('tools_used', [])]}")

    # Paso 2: Consultar citas
    print("\n📋 PASO 2: Consultar citas")
    payload2 = make_payload("¿Qué citas tengo?", 2)
    result2 = await orchestrator.process_webhook(payload2)
    print(f"   Respuesta: {result2.get('agent_response', '')[:150]}...")
    print(f"   Herramientas: {[t['tool'] for t in result2.get('tools_used', [])]}")

    # Paso 3: Cancelar cita
    print("\n❌ PASO 3: Cancelar cita")
    payload3 = make_payload("Cancela mi cita de mantenimiento", 3)
    result3 = await orchestrator.process_webhook(payload3)
    print(f"   Respuesta: {result3.get('agent_response', '')[:150]}...")
    print(f"   Herramientas: {[t['tool'] for t in result3.get('tools_used', [])]}")

    # Verificar estado final en DB
    print("\n📊 VERIFICACIÓN EN BASE DE DATOS")
    from core.orchestrator import Database
    from sqlalchemy import text

    db = Database(orchestrator.settings.DATABASE_URL)
    async with db.get_session() as session:
        res = await session.execute(text('SELECT COUNT(*) FROM appointments WHERE phone_number = :p'), {'p': phone})
        total = res.scalar()
        print(f"   Total citas en DB para {phone}: {total}")

        if total > 0:
            res2 = await session.execute(text('SELECT status FROM appointments WHERE phone_number = :p ORDER BY created_at DESC LIMIT 1'), {'p': phone})
            status = res2.scalar()
            print(f"   Estado de la última cita: {status}")

    await orchestrator.shutdown()
    print("\n✅ Prueba completada")

if __name__ == "__main__":
    asyncio.run(test_conversation())
