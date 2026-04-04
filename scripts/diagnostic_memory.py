#!/usr/bin/env python3
"""
Diagnóstico de Memoria y Contexto
Prueba: Enviar 2 mensajes consecutivos y verificar que el historial se preserve
"""

import asyncio
import json
from core.orchestrator import ArcadiumAutomation

async def main():
    print("=" * 70)
    print("DIAGNÓSTICO: MEMORIA Y CONTEXTO")
    print("=" * 70)

    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()
    print("✅ Sistema inicializado\n")

    phone = "+34612345678"
    conversation_id = "diagnostic_test"

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

    # Mensaje 1
    print("📨 MENSAJE 1")
    print("-" * 70)
    payload1 = make_payload("Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor", 1)
    print(f"Contenido: {payload1['body']['conversation']['messages'][0]['content']}")

    result1 = await orchestrator.process_webhook(payload1)
    print(f"\nRespuesta (primeros 150 chars):\n  {result1.get('agent_response', '')[:150]}...")
    print(f"Session ID: {result1.get('session_id')}")
    print(f"Herramientas usadas: {[t['tool'] for t in result1.get('tools_used', [])]}")

    # Pequeña pausa para separar timestamps
    await asyncio.sleep(0.5)

    # Mensaje 2 (MISMO phone)
    print("\n\n📨 MENSAJE 2 (Mismo remitente)")
    print("-" * 70)
    payload2 = make_payload("si por favor", 2)
    print(f"Contenido: {payload2['body']['conversation']['messages'][0]['content']}")

    result2 = await orchestrator.process_webhook(payload2)
    print(f"\nRespuesta (primeros 150 chars):\n  {result2.get('agent_response', '')[:150]}...")
    print(f"Session ID: {result2.get('session_id')}")
    print(f"Herramientas usadas: {[t['tool'] for t in result2.get('tools_used', [])]}")

    # Verificación en base de datos
    print("\n\n📊 VERIFICACIÓN EN BASE DE DATOS")
    print("-" * 70)
    from core.orchestrator import Database
    from sqlalchemy import text

    db = Database(orchestrator.settings.DATABASE_URL)
    async with db.get_session() as session:
        # Contar mensajes en langchain_memory
        res = await session.execute(
            text('SELECT COUNT(*) FROM langchain_memory WHERE session_id = :sid'),
            {'sid': phone}
        )
        total_mem = res.scalar()
        print(f"  Total mensajes en langchain_memory para {phone}: {total_mem}")

        # Mostrar los últimos mensajes
        res2 = await session.execute(
            text('''
                SELECT type, content, created_at
                FROM langchain_memory
                WHERE session_id = :sid
                ORDER BY created_at DESC
                LIMIT 4
            '''),
            {'sid': phone}
        )
        rows = res2.fetchall()
        print(f"\n  Últimos {len(rows)} mensajes en memoria:")
        for i, (msg_type, content, created_at) in enumerate(reversed(rows), 1):
            print(f"    {i}. [{msg_type}] {content[:80]}{'...' if len(content) > 80 else ''}")

        # Contar mensajes en Message (WhatsApp)
        res3 = await session.execute(
            text('SELECT COUNT(*) FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE phone_number = :p)'),
            {'p': phone}
        )
        total_msg = res3.scalar()
        print(f"\n  Total mensajes WhatsApp (tabla messages) para {phone}: {total_msg}")

        # Verificar agentes en caché
        print(f"\n  Agentes en caché del orchestrator: {len(orchestrator._agents)}")
        print(f"  Session IDs cacheados: {list(orchestrator._agents.keys())}")

    await orchestrator.shutdown()

    print("\n" + "=" * 70)
    print("RESULTADO DIAGNÓSTICO")
    print("=" * 70)
    print(f"✓ Mensaje 1 Session ID: {result1.get('session_id')}")
    print(f"✓ Mensaje 2 Session ID: {result2.get('session_id')}")
    print(f"✓ Coinciden: {result1.get('session_id') == result2.get('session_id')}")
    print(f"✓ Historial guardado: {total_mem} mensajes")
    print("\n🔎 INSIGHTS:")
    if result1.get('session_id') != result2.get('session_id'):
        print("  ❌ SESSION_ID DIFIERE entre mensajes → Esto causa la pérdida de contexto")
    else:
        print("  ✅ Session_id consistente")

    if total_mem >= 4:
        print("  ✅ Memoria contiene al menos 4 mensajes (2 round-trip)")
    else:
        print(f"  ⚠️ Memoria solo tiene {total_mem} mensajes (esperado ≥4)")

    print("\nRevisa los logs con LOG_LEVEL=DEBUG para más detalles")

if __name__ == "__main__":
    asyncio.run(main())
