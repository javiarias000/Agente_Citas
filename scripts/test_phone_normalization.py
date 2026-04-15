#!/usr/bin/env python3
"""
Prueba de normalización de números de teléfono
"""

import asyncio
import aiohttp
import json

BASE_URL = "http://localhost:8000"

async def test_normalization():
    """Envía mensajes con diferentes formatos de número y verifica que se traten como el mismo session"""
    print("Testing phone number normalization...")
    print("="*60)

    test_cases = [
        ("+34612345678", "Formato internacional"),
        ("34612345678", "Sin + pero con código país"),
        ("612345678", "Solo número nacional (España)"),
        ("+34 612 345 678", "Con espacios"),
        ("+34-612-345-678", "Con guiones"),
    ]

    session = aiohttp.ClientSession()

    for phone, description in test_cases:
        print(f"\n📱 Probando: {description}")
        print(f"   Número: {phone}")

        payload = {
            "sender": phone,
            "message": f"Hola desde {description}",
            "message_type": "text"
        }

        try:
            async with session.post(f"{BASE_URL}/webhook/test", json=payload) as resp:
                result = await resp.json()
                print(f"   ✅ Respuesta: {result.get('status')} - session_id: {result.get('session_id')}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    await session.close()

    print("\n" + "="*60)
    print("Revisar logs para ver si los senderos normalizados son iguales")
    print("Consultar DB: SELECT DISTINCT session_id FROM langchain_memory;")

if __name__ == "__main__":
    asyncio.run(test_normalization())
