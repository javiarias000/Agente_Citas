#!/usr/bin/env python3
"""
Prueba CON NUMERO REAL (pero inventado)
Usa tu propio número para probar en WhatsApp real
"""

import asyncio
from core.orchestrator import ArcadiumAutomation

async def main():
    print("=" * 70)
    print("PRUEBA CON NÚMERO REAL")
    print("=" * 70)
    print("⚠️  Usa un número REAL que tengas controlado")
    print("   (o inventado pero que sepas que no existe para evitar confusiones)")
    print()

    # CAMBIA ESTO por tu número REAL (con código país)
    MI_NUMERO = "+34612345678"  # <-- CAMBIA esto

    orchestrator = ArcadiumAutomation()
    await orchestrator.initialize()

    print(f"📱 Usando número: {MI_NUMERO}")
    print()

    # Mensaje 1
    payload1 = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "sender": {"phone_number": MI_NUMERO, "name": "Yo"},
                        "content": "Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor"
                    }
                ]
            }
        },
        "account_id": 1,
        "conversation_id": 100
    }

    print("📨 Enviando MENSAJE 1...")
    result1 = await orchestrator.process_webhook(payload1)
    print(f"   Respuesta: {result1.get('agent_response', '')[:100]}...")
    print()

    await asyncio.sleep(1)

    # Mensaje 2
    payload2 = {
        "body": {
            "conversation": {
                "messages": [
                    {
                        "sender": {"phone_number": MI_NUMERO, "name": "Yo"},
                        "content": "si por favor"
                    }
                ]
            }
        },
        "account_id": 1,
        "conversation_id": 101
    }

    print("📨 Enviando MENSAJE 2...")
    result2 = await orchestrator.process_webhook(payload2)
    print(f"   Respuesta: {result2.get('agent_response', '')[:100]}...")
    print()

    # Analizar respuesta
    print("=" * 70)
    print("🔍 ANÁLISIS")
    print("=" * 70)

    response2 = result2.get('agent_response', '')
    if any(word in response2.lower() for word in ["cita", "muela", "12:00", "extracción", "confirm"]):
        print("✅ La respuesta 2 INCLUYE contexto del mensaje 1")
    else:
        print("❌ La respuesta 2 NO incluye contexto")
        print("   El sistema está reiniciando la conversación")

    # Ver logs para session_id
    print("\n📊 Revisa los logs del servidor:")
    print("   ./run.sh logs | grep -E 'session_id|sender'")

    await orchestrator.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
