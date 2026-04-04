#!/usr/bin/env python3
"""
Simulación EXACTA de la conversación reportada
"""

import asyncio
from core.orchestrator import ArcadiumAutomation

async def main():
    print("=" * 70)
    print("SIMULACIÓN: CONVERSACIÓN DE USUARIO")
    print("=" * 70)
    print("Secuencia:")
    print("  1. 'Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor'")
    print("  2. 'si por favor'")
    print()

    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()

    phone = "+34612345678"

    # Mensaje 1
    payload1 = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "sender": {"phone_number": phone, "name": "Cliente"},
                        "content": "Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor"
                    }
                ]
            }
        },
        "account_id": 1,
        "conversation_id": 1
    }

    print("📨 MENSAJE 1:")
    print(f"   Usuario: {payload1['body']['conversation']['messages'][0]['content']}")

    result1 = await orchestrator.process_webhook(payload1)
    print(f"\n   AI: {result1.get('agent_response', '')[:200]}...")
    print(f"\n   Herramientas usadas: {[t['tool'] for t in result1.get('tools_used', [])]}")

    await asyncio.sleep(0.5)

    # Mensaje 2 (MISMO teléfono)
    payload2 = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "sender": {"phone_number": phone, "name": "Cliente"},
                        "content": "si por favor"
                    }
                ]
            }
        },
        "account_id": 1,
        "conversation_id": 2
    }

    print("\n" + "=" * 70)
    print("📨 MENSAJE 2:")
    print(f"   Usuario: {payload2['body']['conversation']['messages'][0]['content']}")

    result2 = await orchestrator.process_webhook(payload2)
    print(f"\n   AI: {result2.get('agent_response', '')[:200]}...")
    print(f"\n   Herramientas usadas: {[t['tool'] for t in result2.get('tools_used', [])]}")

    # Análisis
    print("\n" + "=" * 70)
    print("🔍 ANÁLISIS DE CONTEXTO")
    print("=" * 70)

    response2 = result2.get('agent_response', '')

    # Verificar si la respuesta del segundo mensaje muestra que recuerda el contexto
    context_clues = [
        "cita",
        "extracción",
        "12:00",
        "hoy",
        "muela",
        "confirmar",
        "agendar"
    ]

    found_clues = [clue for clue in context_clues if clue.lower() in response2.lower()]

    print(f"\nPistas de contexto encontradas en respuesta 2:")
    for clue in found_clues:
        print(f"  ✅ '{clue}'")

    if len(found_clues) >= 2:
        print("\n✅ La respuesta INCLUYE contexto del primer mensaje")
    else:
        print("\n⚠️ La respuesta NO muestra suficiente contexto")
        print("   Posibles razones:")
        print("   1. El historial no se cargó correctamente")
        print("   2. El modelo no está usando el historial (max_tokens limit?)")
        print("   3. El prompt del sistema no enfatiza el uso de historial")

    # Revisar historial en DB
    print("\n📊 Historial en DB (últimos 4 mensajes):")
    from core.orchestrator import Database
    from sqlalchemy import text

    db = Database(orchestrator.settings.DATABASE_URL)
    async with db.get_session() as session:
        res = await session.execute(
            text('''
                SELECT type, content, created_at
                FROM langchain_memory
                WHERE session_id = :sid
                ORDER BY created_at DESC
                LIMIT 4
            '''),
            {'sid': phone}
        )
        rows = res.fetchall()
        for i, (msg_type, content, created_at) in enumerate(reversed(rows), 1):
            print(f"  {i}. [{msg_type}] {content[:70]}{'...' if len(content)>70 else ''}")

    await orchestrator.shutdown()

    print("\n" + "=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
