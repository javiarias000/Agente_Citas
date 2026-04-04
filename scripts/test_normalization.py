#!/usr/bin/env python3
"""
Prueba: Enviar dos mensajes con formato de teléfono DIFERENTE
El sistema debe normalizarlos y usar el mismo session_id
"""

import asyncio
from core.orchestrator import ArcadiumAutomation

async def main():
    print("=" * 70)
    print("PRUEBA: NORMALIZACIÓN DE TELÉFONO")
    print("=" * 70)
    print("Objetivo: Dos formatos diferentes → mismo session_id → misma memoria\n")

    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()

    phone_variations = [
        "+34612345678",  # Formato 1: con + y espacios
        "34 612 345 678",  # Formato 2: con espacios
        "612345678",       # Formato 3: solo número nacional
    ]

    for idx, phone in enumerate(phone_variations, 1):
        payload = {
            "body": {
                "conversation": {
                    "messages": [
                        {
                            "sender": {
                                "phone_number": phone,
                                "name": "Test User"
                            },
                            "content": f"Mensaje {idx} desde formato '{phone}'"
                        }
                    ]
                }
            },
            "account_id": 1,
            "conversation_id": idx
        }

        print(f"📨 Mensaje {idx}: phone='{phone}'")
        result = await orchestrator.process_webhook(payload)
        session_id_used = result.get('session_id', 'N/A')
        print(f"   Session ID usado: {session_id_used}")

        # Pequeña pausa
        await asyncio.sleep(0.2)

    # Verificar en DB
    print("\n" + "=" * 70)
    print("📊 VERIFICACIÓN EN BASE DE DATOS")
    print("=" * 70)

    from core.orchestrator import Database
    from sqlalchemy import text

    db = Database(orchestrator.settings.DATABASE_URL)
    # Buscar todos los session_ids únicos en langchain_memory
    async with db.get_session() as session:
        res = await session.execute(
            text('SELECT DISTINCT session_id FROM langchain_memory ORDER BY session_id')
        )
        unique_sessions = [row[0] for row in res.fetchall()]
        print(f"Session IDs únicos en langchain_memory: {unique_sessions}")

        # Contar mensajes por session_id
        for sid in unique_sessions:
            res2 = await session.execute(
                text('SELECT COUNT(*) FROM langchain_memory WHERE session_id = :sid'),
                {'sid': sid}
            )
            count = res2.scalar()
            print(f"  - {sid}: {count} mensajes")

    await orchestrator.shutdown()

    print("\n" + "=" * 70)
    print("RESULTADO")
    print("=" * 70)
    if len(unique_sessions) == 1:
        print("✅ ÉXITO: Todos los mensajes guardados en UNA sola sesión")
        print("   La normalización funciona correctamente.")
    else:
        print("❌ FALLO: Hay múltiples session_ids")
        print("   Esto causaría pérdida de contexto entre formatos.")

if __name__ == "__main__":
    asyncio.run(main())
