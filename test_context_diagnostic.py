#!/usr/bin/env python3
"""
Script de diagnóstico para probar la persistencia de memoria.
Envía dos mensajes consecutivos al webhook de test y monitorea los logs.
"""

import asyncio
import json
import aiohttp
from datetime import datetime

# Configuración
BASE_URL = "http://localhost:8000"
TEST_SENDER = "+34612345678"  # Formato normalizado

async def send_test_message(sender: str, message: str, message_num: int):
    """Envía un mensaje de prueba al webhook"""
    print(f"\n{'='*60}")
    print(f"📤 MENSAJE {message_num}")
    print(f"{'='*60}")
    print(f"From: {sender}")
    print(f"Message: {message}")
    print(f"Timestamp: {datetime.now().isoformat()}")

    payload = {
        "sender": sender,
        "message": message,
        "message_type": "text"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{BASE_URL}/webhook/test",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                result = await response.json()
                print(f"\n📥 RESPUESTA:")
                print(f"Status: {response.status}")
                print(f"Body: {json.dumps(result, indent=2, ensure_ascii=False)}")
                return result
        except Exception as e:
            print(f"❌ Error: {e}")
            return None

async def check_database():
    """Consulta la base de datos para ver el estado de la memoria"""
    print(f"\n{'='*60}")
    print(f"🔍 VERIFICANDO BASE DE DATOS")
    print(f"{'='*60}")

    from db import get_async_session
    from memory.postgres_memory import PostgresMemory
    from langchain_core.messages import HumanMessage, AIMessage

    try:
        async with get_async_session() as session:
            from db.models import LangchainMemory
            from sqlalchemy import select

            stmt = select(LangchainMemory).where(
                LangchainMemory.session_id == TEST_SENDER
            ).order_by(LangchainMemory.created_at)

            result = await session.execute(stmt)
            records = result.scalars().all()

            print(f"📊 Registros encontrados para {TEST_SENDER}: {len(records)}")

            if len(records) > 0:
                print("\n📋 Últimos 4 mensajes:")
                for i, record in enumerate(records[-4:], 1):
                    print(f"  {i}. [{record.type}] {record.created_at.isoformat()}")
                    print(f"     Content: {record.content[:100]}...")
            else:
                print("⚠️  No hay mensajes en la base de datos para este session_id")
    except Exception as e:
        print(f"❌ Error consultando DB: {e}")
        import traceback
        traceback.print_exc()

async def main():
    """Ejecuta la prueba de diagnóstico"""
    print(f"\n{'#'*60}")
    print(f"# DIAGNÓSTICO DE PÉRDIDA DE CONTEXTO")
    print(f"#")
    print(f"# Session ID: {TEST_SENDER}")
    print(f"{'#'*60}")

    # Mensaje 1
    result1 = await send_test_message(
        sender=TEST_SENDER,
        message="Quiero agendar una cita para hoy, para sacarme una muela. A las 12 del dia, por favor",
        message_num=1
    )

    # Pequeña pausa
    await asyncio.sleep(2)

    # Mensaje 2 (continuación)
    result2 = await send_test_message(
        sender=TEST_SENDER,
        message="si porplease",
        message_num=2
    )

    # Verificar base de datos
    await check_database()

    # Resumen
    print(f"\n{'='*60}")
    print(f"📊 RESUMEN")
    print(f"{'='*60}")
    print(f"Session ID usado: {TEST_SENDER}")
    print(f"¿Agentes en cache? Revisar logs (CACHE HIT vs CACHE MISS)")
    print(f"¿Historial cargado en mensaje 2? Revisar logs")
    print(f"\n🔎 Para analizar:")
    print(f"1. En los logs, buscar 'Historial cargado' en mensaje 2")
    print(f"2. Debería mostrar message_count >= 2 si el contexto se preserva")
    print(f"3. Si muestra 0 o 1, hay pérdida de contexto")

if __name__ == "__main__":
    asyncio.run(main())
