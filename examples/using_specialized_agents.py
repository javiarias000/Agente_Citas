#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ejemplos de uso de los Agentes Especializados de Arcadium

Este archivo demuestra cómo usar:
- DeyyAgent: Agente principal para gestión de citas
- ArcadiumSupportAgent: Agente de soporte general
- ArcadiumAdminAgent: Agente administrativo
"""

import asyncio
import uuid
import sys
from pathlib import Path

# Agregar directorio raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Ejemplo 1: Usar DeyyAgent para agendar una cita
async def ejemplo_deyy_agent():
    """
    DeyyAgent: Especialista en citas dentales
    """
    print("\n=== EJEMPLO 1: DeyyAgent (Gestión de Citas) ===\n")

    from agents.deyy_agent import DeyyAgent
    from core.store import ArcadiumStore
    from memory.memory_manager import MemoryManager

    # Configuración básica
    session_id = "+593987654321"  # Phone number como session ID
    project_id = uuid.uuid4()  # En producción, obtener de DB

    # Inicializar store y memory manager
    store = ArcadiumStore()
    memory_manager = MemoryManager()
    await memory_manager.initialize()

    # Crear agente Deyy
    deyy = DeyyAgent(
        session_id=session_id,
        store=store,
        project_id=project_id,
        verbose=True
    )

    # Procesar mensaje del usuario
    respuesta = await deyy.process_message(
        message="Hola, quiero agendar una limpieza dental para mañana a las 2:00 PM",
        save_to_memory=True
    )

    print(f"Respuesta de Deyy: {respuesta.get('response')}")
    print(f"Estado: {respuesta.get('status')}")


# Ejemplo 2: Usar ArcadiumSupportAgent para preguntas técnicas
async def ejemplo_support_agent():
    """
    ArcadiumSupportAgent: Soporte técnico y documentación
    """
    print("\n=== EJEMPLO 2: ArcadiumSupportAgent (Soporte) ===\n")

    from agents.support_agent import ArcadiumSupportAgent

    session_id = "support_user_001"

    # Crear agente de soporte
    support = ArcadiumSupportAgent(
        session_id=session_id,
        verbose=True,
        enable_calendar_tools=False  # Sin herramientas de citas
    )

    # Consulta sobre la plataforma
    respuesta = await support.run(
        input_text="¿Cómo configuro el Google Calendar en Arcadium?",
        conversation_history=[]
    )

    print(f"Respuesta de Support: {respuesta.get('response')}")


# Ejemplo 3: Usar ArcadiumAdminAgent para consultar estadísticas
async def ejemplo_admin_agent():
    """
    ArcadiumAdminAgent: Administración del sistema
    """
    print("\n=== EJEMPLO 3: ArcadiumAdminAgent (Admin) ===\n")

    from agents.admin_agent import ArcadiumAdminAgent

    session_id = "admin_user_001"

    # Crear agente admin
    admin = ArcadiumAdminAgent(
        session_id=session_id,
        verbose=True
    )

    # Consulta administrativa
    respuesta = await admin.run(
        input_text="¿Cuántas citas tengo agendadas esta semana?",
        conversation_history=[]
    )

    print(f"Respuesta de Admin: {respuesta.get('response')}")


# Ejemplo 4: Usar herramientas directamente
async def ejemplo_herramientas_directo():
    """
    Usar herramientas de Arcadium sin agente completo
    """
    print("\n=== EJEMPLO 4: Herramientas Directas ===\n")

    from utils.arcadium_tools import (
        consultar_disponibilidad,
        knowledge_base_search,
        think
    )
    from datetime import datetime

    # 1. Consultar disponibilidad
    fecha = datetime.now() + timedelta(days=1)
    fecha_str = fecha.date().isoformat()

    # Las herramientas son StructuredTool, llamar con .ainvoke()
    disponibilidad = await consultar_disponibilidad.ainvoke({
        "date": fecha_str,
        "service_type": "limpieza"
    })

    print(f"Disponibilidad para limpieza el {fecha_str}:")
    print(f"  Total slots: {disponibilidad.get('total_available')}")
    if disponibilidad.get('available_slots'):
        print(f"  Primer slot: {disponibilidad['available_slots'][0]}")

    # 2. Búsqueda en knowledge base
    busqueda = await knowledge_base_search.ainvoke({
        "query": "¿Cuánto cuesta una limpieza dental?",
        "k": 3
    })

    print(f"\nBúsqueda en knowledge base:")
    for i, doc in enumerate(busqueda.get('documents', [])[:3], 1):
        print(f"  {i}. Score: {doc.get('score'):.2f} - {doc.get('content', '')[:100]}...")

    # 3. Razonamiento estructurado
    razonamiento = await think.ainvoke({
        "thought": "¿Debería ofrecer un descuento a un cliente que tiene su primera cita?",
        "context": "Clínica dental, primer visita, objetivo fidelizar",
        "focus_areas": ["marketing", "customer retention", "pricing"]
    })

    print(f"\nRazonamiento:")
    print(razonamiento[:300] + "...")
    print(f"(Longitud total: {len(razonamiento)} caracteres)")


# Ejemplo 5: Enviar WhatsApp
async def ejemplo_whatsapp():
    """
    Enviar mensaje de WhatsApp
    """
    print("\n=== EJEMPLO 5: Enviar WhatsApp ===\n")

    from utils.arcadium_tools import enviar_mensaje_whatsapp

    # NOTA: Requiere WHATSAPP_API_URL y WHATSAPP_INSTANCE_NAME configurados
    # resultado = await enviar_mensaje_whatsapp(
    #     to="+593987654321",
    #     text="Hola! Tu cita está confirmada para mañana a las 2:00 PM.",
    #     buttons=[
    #         {"id": "confirm", "text": "✅ Confirmar"},
    #         {"id": "cancel", "text": "❌ Cancelar"}
    #     ]
    # )
    # print(f"Resultado WhatsApp: {resultado}")

    print("Ejemplo deshabilitado (necesita configuración de WhatsApp API)")


# Ejemplo 6: Gestionar perfil de usuario
async def ejemplo_perfiles():
    """
    Obtener y actualizar perfil de usuario
    """
    print("\n=== EJEMPLO 6: Perfiles de Usuario ===\n")

    from utils.arcadium_tools import obtener_perfil_usuario, actualizar_perfil_usuario

    phone = "+593987654321"

    # 1. Obtener perfil (si existe)
    perfil = await obtener_perfil_usuario.ainvoke({
        "phone_number": phone
    })
    print(f"Perfil encontrado: {perfil.get('found')}")
    if perfil.get('found'):
        print(f"  Datos: {perfil.get('profile')}")

    # 2. Actualizar perfil con nueva información
    actualizacion = await actualizar_perfil_usuario.ainvoke({
        "phone_number": phone,
        "preferences": {"servicio_favorito": "ortodoncia", "horario_preferido": "tarde"},
        "notes": "Cliente con alergia a la anestesia local. Informar antes de procedimientos.",
        "extracted_facts": {"tiene_hijos": True, "miedo_al_dentista": True}
    })

    print(f"Perfil actualizado: {actualizacion.get('success')}")
    if actualizacion.get('success'):
        print(f"  Nuevo perfil: {actualizacion.get('profile')}")


# ============================================
# MAIN
# ============================================

async def main():
    """Ejecutar todos los ejemplos"""

    print("""
╔═══════════════════════════════════════════════════════════════╗
║           Arcadium Automation - Ejemplos de Agentes          ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    # Ejemplo 1: DeyyAgent (requiere DB configurada) - deshabilitado por ahora
    # try:
    #     await ejemplo_deyy_agent()
    # except Exception as e:
    #     print(f"❌ Ejemplo DeyyAgent falló: {e}")

    # Ejemplo 2: SupportAgent (deshabilitado por bug en AgentExecutor)
    # try:
    #     await ejemplo_support_agent()
    # except Exception as e:
    #     print(f"❌ Ejemplo SupportAgent falló: {e}")

    # Ejemplo 3: AdminAgent (similar a Support)
    # try:
    #     await ejemplo_admin_agent()
    # except Exception as e:
    #     print(f"❌ Ejemplo AdminAgent falló: {e}")

    # Ejemplo 4: Herramientas directas
    try:
        await ejemplo_herramientas_directo()
    except Exception as e:
        print(f"❌ Ejemplo Herramientas Directas falló: {e}")

    # Ejemplo 5: WhatsApp (deshabilitado)
    # try:
    #     await ejemplo_whatsapp()
    # except Exception as e:
    #     print(f"❌ Ejemplo WhatsApp falló: {e}")

    # Ejemplo 6: Perfiles
    try:
        await ejemplo_perfiles()
    except Exception as e:
        print(f"❌ Ejemplo Perfiles falló: {e}")

    print("\n" + "="*60)
    print("✅ Ejemplos completados")
    print("="*60)
    print("\nNOTA: Para probar agendar_cita, consultar_disponibilidad, etc.,")
    print("      necesitas inicializar la base de datos primero.")
    print("      Ejecuta: ./run.sh start (y espera que la DB esté lista)")


if __name__ == "__main__":
    asyncio.run(main())
