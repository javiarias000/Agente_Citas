#!/usr/bin/env python3
"""
Test script para Admin API
"""

import asyncio
import aiohttp
import json
from datetime import datetime

# Config
BASE_URL = "http://localhost:8000"
API_KEY = "test-key-123"  # Debe coincidir con lo que esté en la BD

async def test_admin_api():
    """Probar todos los endpoints de admin API"""

    headers = {"X-API-Key": API_KEY}

    async with aiohttp.ClientSession() as session:
        print("🧪 Testing Admin API...\n")

        # 1. GET /api/v1/projects/current
        print("1. GET /api/v1/projects/current")
        async with session.get(f"{BASE_URL}/api/v1/projects/current", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"   ✓ Project: {data.get('name')}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 2. GET /api/v1/agent/config
        print("\n2. GET /api/v1/agent/config")
        async with session.get(f"{BASE_URL}/api/v1/agent/config", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                config = await resp.json()
                print(f"   ✓ Agent: {config.get('agent_name')}")
                print(f"   ✓ Tools: {len(config.get('enabled_tools', []))} enabled")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 3. PUT /api/v1/agent/config
        print("\n3. PUT /api/v1/agent/config")
        updates = {
            "system_prompt": "Eres un asistente actualizado...",
            "temperature": 0.8
        }
        async with session.put(f"{BASE_URL}/api/v1/agent/config",
                              json=updates,
                              headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                result = await resp.json()
                print(f"   ✓ {result.get('message')}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 4. GET /api/v1/conversations
        print("\n4. GET /api/v1/conversations")
        async with session.get(f"{BASE_URL}/api/v1/conversations?limit=5", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                convs = data.get('conversations', [])
                print(f"   ✓ Total conversations: {len(convs)}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 5. GET /api/v1/stats
        print("\n5. GET /api/v1/stats")
        async with session.get(f"{BASE_URL}/api/v1/stats", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                stats = await resp.json()
                print(f"   ✓ Active conversations: {stats.get('active_conversations')}")
                print(f"   ✓ Messages today: {stats.get('messages_today')}")
                print(f"   ✓ Scheduled appointments: {stats.get('scheduled_appointments')}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 6. GET /api/v1/tools
        print("\n6. GET /api/v1/tools")
        async with session.get(f"{BASE_URL}/api/v1/tools", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                tools = await resp.json()
                print(f"   ✓ Available tools: {len(tools)}")
                for tool in tools:
                    print(f"     - {tool.get('name')}: {tool.get('description', 'No description')[:50]}...")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 7. GET /api/v1/audit/logs
        print("\n7. GET /api/v1/audit/logs")
        async with session.get(f"{BASE_URL}/api/v1/audit/logs?limit=5", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                logs = await resp.json()
                print(f"   ✓ Audit logs: {len(logs)} entries")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 8. POST /api/v1/appointments (manual creation)
        print("\n8. POST /api/v1/appointments")
        appointment_data = {
            "phone_number": "+573123456789",
            "appointment_datetime": "2026-04-07T10:00:00",  # Monday
            "service_type": "consulta",
            "notes": "Test appointment from API"
        }
        async with session.post(f"{BASE_URL}/api/v1/appointments",
                               json=appointment_data,
                               headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                result = await resp.json()
                print(f"   ✓ {result.get('message')}")
                print(f"   ✓ Appointment ID: {result.get('appointment_id')}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        # 9. GET /api/v1/appointments
        print("\n9. GET /api/v1/appointments")
        async with session.get(f"{BASE_URL}/api/v1/appointments", headers=headers) as resp:
            print(f"   Status: {resp.status}")
            if resp.status == 200:
                appointments = await resp.json()
                print(f"   ✓ Total appointments: {len(appointments)}")
            else:
                error = await resp.text()
                print(f"   ✗ Error: {error}")

        print("\n✅ Test completed!")

if __name__ == "__main__":
    asyncio.run(test_admin_api())
