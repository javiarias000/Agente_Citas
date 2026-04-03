#!/usr/bin/env python3
"""
Quickstart - Arcadium Automation
Script demostración para probar el sistema
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Callable, Awaitable, Optional

# Añadir al path
sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import ArcadiumAutomation
from validators.schemas import PhoneNumber
from core.config import Settings


def build_orchestrator(metrics_port: Optional[int] = None) -> ArcadiumAutomation:
    """Factory para crear el orchestrator con configuración inyectada"""
    settings = Settings()
    if metrics_port is not None:
        settings.METRICS_PORT = metrics_port
    return ArcadiumAutomation(settings=settings)


async def run_safe(demo_name: str, demo_func: Callable[[], Awaitable[None]]):
    """Ejecuta demos de forma segura"""
    try:
        await demo_func()
    except Exception as e:
        print(f"\n❌ Error en demo '{demo_name}': {e}")
        import traceback
        traceback.print_exc()
        print()


# =========================
# DEMOS
# =========================

async def demo_basic_chain():
    """Demo: cadena básica de ejemplo"""
    print("=" * 60)
    print("DEMO: Cadena Básica")
    print("=" * 60)

    orchestrator = build_orchestrator(metrics_port=9092)

    try:
        await orchestrator.initialize()
        print("✅ Sistema inicializado")

        # Payload de ejemplo (estructura similar a Chatwoot)
        test_payload = {
            "body": {
                "conversation": {
                    "messages": [
                        {
                            "sender": {
                                "phone_number": "+34612345678",
                                "name": "Cliente Demo"
                            },
                            "content": "Hola, necesito información sobre sus servicios"
                        }
                    ]
                }
            },
            "account_id": 1,
            "conversation_id": 999
        }

        print("\n📦 Payload de prueba:")
        print(json.dumps(test_payload, indent=2, ensure_ascii=False))

        print("\n⚙️ Procesando...")
        result = await orchestrator.process_webhook(test_payload)

        print(f"\n✅ Resultado: {result.get('status', 'UNKNOWN').upper()}")
        if 'total_time_ms' in result:
            print(f"⏱️ Tiempo: {result['total_time_ms']:.2f}ms")
        if 'agent_response' in result:
            print(f"\n💬 Respuesta del agente:")
            print(f"   {result['agent_response'][:200]}...")

    finally:
        await orchestrator.shutdown()

    print("\n")


async def demo_validators():
    """Demo: sistema de validación"""
    print("=" * 60)
    print("DEMO: Validadores")
    print("=" * 60)

    from validators.schemas import WebhookPayload, validate_phone_number, sanitize_text

    # Test validación teléfono
    print("\n📱 Validación de teléfonos:")
    valid_phones = ["+34612345678", "+12125551234"]
    invalid_phones = ["123", "abc123", ""]

    for phone in valid_phones:
        result = validate_phone_number(phone)
        print(f"  {phone}: {'✅' if result else '❌'}")

    for phone in invalid_phones:
        result = validate_phone_number(phone)
        print(f"  {phone}: {'✅' if result else '❌'}")

    # Test sanitización
    print("\n🧹 Sanitización de texto:")
    text = "  Hola   mundo   con   espacios    "
    print(f"  Original: '{text}'")
    print(f"  Sanitizado: '{sanitize_text(text)}'")

    # Test extracción payload
    print("\n📦 Extracción de webhook:")
    payload = WebhookPayload(
        body={
            "conversation": {
                "messages": [
                    {
                        "sender": {
                            "phone_number": "+34612345678",
                            "name": "Usuario"
                        },
                        "content": "Mensaje de prueba"
                    }
                ]
            }
        },
        account_id=1,
        conversation_id=123
    )

    conv = payload.extract_conversation()
    print(f"  Teléfono: {conv.phone}")
    print(f"  Nombre: {conv.user_name}")
    print(f"  Mensaje: {conv.messages[0].content}")

    print("\n")


async def demo_metrics():
    """Demo: métricas del sistema"""
    print("=" * 60)
    print("DEMO: Métricas y Monitoreo")
    print("=" * 60)

    # Usar puerto alternativo para evitar conflicto
    orchestrator = build_orchestrator(metrics_port=9091)

    try:
        await orchestrator.initialize()

        # Simular algunas ejecuciones
        print("\n📊 Simulando ejecuciones...")
        for i in range(3):
            payload = {
                "body": {
                    "conversation": {
                        "messages": [
                            {
                                "sender": {
                                    "phone_number": f"+346{i:09}",
                                    "name": f"Usuario {i}"
                                },
                                "content": f"Mensaje {i}"
                            }
                        ]
                    }
                },
                "account_id": 1,
                "conversation_id": i
            }

            try:
                await orchestrator.process_webhook(payload, chain_type='unified')
            except Exception as e:
                print(f"⚠️ Error en simulación {i}: {e}")

        stats = await orchestrator.get_system_stats()
        print("\n📈 Estadísticas del Sistema:")
        print(json.dumps(stats, indent=2, ensure_ascii=False))

        health = await orchestrator.get_health_status()
        print("\n🏥 Estado de Salud:")
        print(json.dumps(health, indent=2, ensure_ascii=False))

    finally:
        await orchestrator.shutdown()

    print("\n")


async def demo_state():
    """Demo: gestión de estado"""
    print("=" * 60)
    print("DEMO: Gestión de Estado")
    print("=" * 60)

    from core.state import StateManager, MemoryStorage, StateKeys

    storage = MemoryStorage()
    state = StateManager(storage)

    phone = "+34612345678"

    # Guardar estado
    print(f"\n💾 Guardando estado para {phone}...")
    conversation_data = {
        "messages": [{"text": "Hola"}, {"text": "OK"}],
        "last_update": "2025-04-01T10:00:00"
    }

    await state.set(StateKeys.conversation(phone), conversation_data, ttl=3600)
    print("  ✅ Guardado")

    # Recuperar
    print(f"\n📖 Recuperando estado...")
    recovered = await state.get(StateKeys.conversation(phone))
    print(f"  ✅ Recuperado: {len(recovered['messages'])} mensajes")

    # Verificar existencia
    exists = await state.exists(StateKeys.conversation(phone))
    print(f"  ✅ Existe: {exists}")

    # Listar claves
    print("\n🔑 Claves activas:")
    keys = await state.keys("conversation:*")
    for key in keys[:5]:
        print(f"  - {key}")

    print("\n")


# =========================
# MAIN
# =========================

async def main():
    print("\n" + "=" * 60)
    print("ARCADIUM AUTOMATION - DEMOSTRACIÓN")
    print("=" * 60)
    print()

    demos = [
        ("Validadores", demo_validators),
        ("Gestión de Estado", demo_state),
        ("Métricas", demo_metrics),
        ("Cadena Básica", demo_basic_chain),
    ]

    for name, demo in demos:
        await run_safe(name, demo)

    print("=" * 60)
    print("DEMO FINALIZADA")
    print("=" * 60)
    print("\nPara usar el sistema completo:")
    print("  1. Configurar variables de entorno en .env")
    print("  2. Ejecutar: python -m arcadium_automation start")
    print("  3. O procesar webhooks: python -m arcadium_automation process --file payload.json")
    print()


if __name__ == "__main__":
    asyncio.run(main())
