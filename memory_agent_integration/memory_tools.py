#!/usr/bin/env python3
"""
Herramientas para interactuar con el sistema de memoria semántica (memory-agent).

Incluye:
- upsert_memory_arcadium: Guarda hechos relevantes sobre el usuario en el store vectorial.
"""

import uuid
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.tools import tool, InjectedToolArg
from langgraph.store.base import BaseStore
from pydantic import Field


@tool
async def upsert_memory_arcadium(
    content: str = Field(
        description="El hecho o información a recordar. Sé conciso y específico. "
                    "Ej: 'Usuario prefiere limpieza dental de 45 minutos' "
                    'Ej: "Alérgico a la penicilina" '
                    'Ej: "Tiene miedo a los dentistas"'
    ),
    context: str = Field(
        description="Contexto adicional: cuándo/dónde se mencionó este hecho. "
                    "Ayuda a dar perspectiva. Ej: 'Mencionado durante conversación del 2025-04-07'"
    ),
    memory_id: Optional[str] = Field(
        default=None,
        description="ID de memoria existente para ACTUALIZAR. Solo usar si ya guardaste este hecho antes y necesitas corregirlo."
    ),
    user_id: Annotated[str, InjectedToolArg] = None,
    store: Annotated[BaseStore, InjectedToolArg] = None,
) -> str:
    """
    Guarda o actualiza una memoria semántica sobre el usuario.

    CUÁNDO USAR:
    - El usuario menciona preferencias (comida, horarios, servicios)
    - Datos médicos importantes (alergias, condiciones, miedos)
    - Información personal relevante (nombre, trabajo, familia)
    - Cualquier dato que deba recordarse en futuras conversaciones
    - Cuando el usuario corrige o actualiza información previamente guardada

    CUÁNDO NO USAR:
    - Información de sesión actual ya en el estado (nombre, servicio, fecha)
      para eso está el state machine.
    - Datos que no son relevantes a largo plazo.

    NOTA:
    - user_id y store se inyectan automáticamente desde el grafo (no pasar).
    - Si no se proporciona memory_id, se crea una nueva memoria.
    - El store debe estar configurado con embeddings (ver MEMORY_AGENT_* en .env).
    """
    if user_id is None:
        return "Error: user_id no inyectado"
    if store is None:
        return "Error: store no inyectado"

    try:
        # Preparar datos
        data = {
            "content": content,
            "context": context,
            "user_id": user_id,
            "updated_at": datetime.utcnow().isoformat(),
        }

        if memory_id:
            # Actualizar memoria existente
            await store.ainput(
                ("memories", user_id, memory_id),
                data,
            )
            return f"✅ Memoria actualizada: {content}"
        else:
            # Crear nueva memoria
            memory_id = str(uuid.uuid4())
            await store.aput(
                ("memories", user_id, memory_id),
                data,
            )
            return f"✅ Memoria guardada: {content}"

    except Exception as e:
        logger.error("Error guardando memoria", error=str(e), exc_info=True)
        return f"❌ Error guardando memoria: {str(e)}"
