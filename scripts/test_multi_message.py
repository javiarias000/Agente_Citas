#!/usr/bin/env python3
"""
Prueba de conversación multi-mensaje para verificar que el contexto se mantiene
"""

import asyncio
import aiohttp
import json
from datetime import datetime

async def test_conversation():
    """Envía dos mensajes consecutivos y verifica que el contexto se mantiene"""

    base_url = "http://localhost:8000"

    # Mensaje 1
    print("\n" + "="*60)
    print(" MENSAJE 1: Quiero agendar una cita para hoy")
    print("="*60)

    payload1 = {
        "sender": "+34612345678",
        "message": "Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor",
        "message_type": "text"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base_url}/webhook/test", json=payload1) as resp:
            response1 = await resp.json()
            print(f"Status: {resp.status}")
            print(f"Response: {json.dumps(response1, indent=2, ensure_ascii=False)}")

    # Esperar un momento
    await asyncio.sleep(2)

    # Mensaje 2 (mismo sender)
    print("\n" + "="*60)
    print(" MENSAJE 2: sí, confirmo")
    print("="*60)

    payload2 = {
        "sender": "+34612345678",
        "message": "sí, confirmo",
        "message_type": "text"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base_url}/webhook/test", json=payload2) as resp:
            response2 = await resp.json()
            print(f"Status: {resp.status}")
            print(f"Response: {json.dumps(response2, indent=2, ensure_ascii=False)}")

    print("\n" + "="*60)
    print(" PRUEBA COMPLETADA")
    print("="*60)
    print("\nAhora verifica en los logs:")
    print("  1. Que ambos mensajes usan el mismo session_id")
    print("  2. Que el Historial cargado tiene al menos 2 mensajes")
    print("  3. Que la respuesta del mensaje 2 incluye contexto del 1")
    print("\nConsulta la DB:")
    print("  SELECT session_id, type, content, created_at")
    print("  FROM langchain_memory")
    print("  WHERE session_id = '+34612345678'")
    print("  ORDER BY created_at;")

if __name__ == "__main__":
    asyncio.run(test_conversation())
