#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ejemplo de uso del Agente Deyy y Divisor de Mensajes

Este ejemplo demonstra cómo:
1. Crear y ejecutar Agente_Deyy
2. Dividir resultado con Divisor_Mensajes
3. Integrar conArcadiumChainBuilder

Requisitos:
- OPENAI_API_KEY configurada en .env
- DATABASE_URL configurada (PostgreSQL)
- SUPABASE_URL y SUPABASE_ANON_KEY configurados (opcional, para knowledge base)
"""

import asyncio
import os
import json
from pathlib import Path

# Añadir directorio raíz al path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from core.config import Settings
from core.state import MemoryStorage, StateManager
from utils.n8n_client import N8nClient, WorkflowExecutor
from chains.arcadium_chains import ArcadiumChainBuilder
from agents.arcadium_agent import get_agent_response
from chains.divisor_chain import dividir_mensaje

# Cargar variables de entorno
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

settings = Settings()


async def example_1_simple_agent():
    """Ejemplo 1: Usar agente directamente"""
    print("\n=== EJEMPLO 1: Agente Deyy simple ===")

    phone = "+34612345678"
    message = "Hola, necesito ayuda para configurar mi cuenta. ¿Podéis ayudarme?"

    print(f"Usuario ({phone}): {message}")

    try:
        response = await get_agent_response(
            phone=phone,
            message=message
        )

        if response['status'] == 'success':
            print(f"Deyy: {response['response']}")
            if response.get('tool_calls'):
                print(f"  (Herramientas usadas: {len(response['tool_calls'])})")
        else:
            print(f"Error: {response.get('error')}")
            print(f"Respuesta fallback: {response['response']}")

    except Exception as e:
        print(f"Excepción: {e}")


async def example_2_agent_with_history():
    """Ejemplo 2: Agente con historial de conversación"""
    print("\n=== EJEMPLO 2: Agente con historial ===")

    phone = "+34612345678"
    message = "¿Y qué documentos necesito exactly?"

    history = [
        {
            "role": "human",
            "content": "Hola, quiero abrir una cuenta corriente."
        },
        {
            "role": "ai",
            "content": "¡Claro! Para abrir una cuenta necesitas: DNI/NIE, número de seguridad social, y un comprobante de domicilio."
        }
    ]

    print(f"Usuario: {message}")
    print(f"(Contexto: {len(history)} mensajes previos)")

    response = await get_agent_response(
        phone=phone,
        message=message,
        conversation_history=history
    )

    print(f"Deyy: {response['response']}")


async def example_3_divisor():
    """Ejemplo 3: Usar Divisor de Mensajes"""
    print("\n=== EJEMPLO 3: Divisor de Mensajes ===")

    mensaje_largo = """
Hola, tengo varias consultas:

1. ¿Cómo puedo resetear mi contraseña?
2. ¿Cuánto tarda una transferencia internacional?
3. ¿Tenéis oficinas en Madrid?

También quería comentar que tuve un problema con la app ayer, se cerraba sola cada dos minutos.
¿Podéis revisarlo?

Gracias.
    """

    print(f"Mensaje original:\n{mensaje_largo[:100]}...")

    resultado = await dividir_mensaje(mensaje_largo)

    print(f"\nDividido en {resultado['total_partes']} partes:")
    for i, parte in enumerate(resultado['partes'], 1):
        print(f"\n  Parte {i}:")
        print(f"    Texto: {parte['parte'][:80]}...")
        print(f"    Categoría: {parte['categoria']}")
        print(f"    Prioridad: {parte['prioridad']}")
        print(f"    Razón: {parte['razonamiento'][:60]}...")

    print(f"\nValidación:")
    val = resultado['validacion']
    print(f"  Calidad: {val['quality_score']:.2f}")
    print(f"  Categorías: {val['category_distribution']}")


async def example_4_full_integration():
    """Ejemplo 4: Integración completa con ArcadiumChainBuilder"""
    print("\n=== EJEMPLO 4: Integración completa ===")

    # Configurar storage
    storage = MemoryStorage()
    state_manager = StateManager(storage)

    # Mock de n8n client (no real)
    from unittest.mock import MagicMock, AsyncMock
    mock_n8n_client = MagicMock()
    mock_n8n_client.execute_webhook = AsyncMock(return_value={
        "status": "success",
        "result": "Workflow ejecutado"
    })

    mock_executor = MagicMock()
    mock_executor.execute_unified_arcadium = AsyncMock(return_value={
        "status": "success",
        "data": {"processed": True}
    })
    # Simular workflow config con nodo Agente_Deyy
    mock_executor._workflow_config = {
        "nodes": [
            {
                "name": "Agente_Deyy",
                "parameters": {
                    "text": "Eres Deyy, asistente de Arcadium. Ayuda con consultas de clientes.",
                    "promptType": "chat",
                    "options": {
                        "model": "gpt-4",
                        "temperature": 0.7
                    }
                }
            }
        ]
    }
    mock_executor.workflow_json_path = None

    # Build unified chain
    print("Construyendo cadena unificada con LangChain...")
    builder = ArcadiumChainBuilder(mock_executor, state_manager)
    chain = builder.build_unified_chain()

    print(f"Construida cadena '{chain.name}' con {len(chain.links)} eslabones")
    print("Eslabones:")
    for i, link in enumerate(chain.links, 1):
        meta = link.metadata or {}
        print(f"  {i}. {link.name} - {meta.get('description', '')}")

    # Payload de prueba
    payload = {
        "telefono": "+34612345678",
        "conversation": "¿Qué tarifas tenéis?",
        "account_id": 1,
        "conversation_id": 999,
        "user_name": "Maria Lopez"
    }

    print(f"\nEjecutando cadena con payload...")
    try:
        result = await chain.execute(payload)
        print(f"✅ Resultado: {result['status']}")
        print(f"   Tiempo total: {result['total_time_ms']:.2f}ms")
        print(f"   Eslabones exitosos: {result['successful_links']}/{result['executed_links']}")

        # Verificar datos del agente
        if 'agent_response' in result['final_data']:
            print(f"   Respuesta agente: {result['final_data']['agent_response'][:50]}...")
        if 'divisor_count' in result['final_data']:
            print(f"   Partes divisor: {result['final_data']['divisor_count']}")

    except Exception as e:
        print(f"❌ Error ejecutando cadena: {e}")


async def main():
    """Ejecutar todos los ejemplos"""
    print("=" * 60)
    print("ARCADEUM AUTOMATION - EJEMPLOS LANGCHAIN")
    print("=" * 60)

    try:
        # Verificar configuración mínima
        if not os.environ.get('OPENAI_API_KEY'):
            print("\n⚠️  ADVERTENCIA: OPENAI_API_KEY no configurada.")
            print("   Algunos ejemplos pueden fallar. Configura en .env\n")

        # Ejecutar ejemplos
        await example_1_simple_agent()
        await example_2_agent_with_history()
        await example_3_divisor()
        await example_4_full_integration()

        print("\n" + "=" * 60)
        print("Ejemplos completados ✓")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\nInterrumpido por usuario")
    except Exception as e:
        print(f"\n❌ Error en ejemplos: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
