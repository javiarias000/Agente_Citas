#!/usr/bin/env python3
"""
Ejemplo de test del webhook
Simula envío de mensaje desde WhatsApp
"""

import asyncio
import json
import httpx

async def test_webhook():
    """Envía payload de prueba al webhook"""

    # Payload formato Evolution API
    payload = {
        "sender": "1234567890",
        "message": "Hola, quiero agendar una cita",
        "message_type": "text"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/webhook/whatsapp",
            json=payload
        )

        print("Status:", response.status_code)
        print("Response:", json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(test_webhook())
