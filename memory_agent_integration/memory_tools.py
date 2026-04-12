#!/usr/bin/env python3
"""
Herramientas de memoria para el agente Arcadium.

Incluye:
- upsert_memory_arcadium: memoria semántica vectorial (legado, sin tipos)
- save_patient_memory: memoria estructurada y tipada (estilo Claude Code)
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

import structlog
from langchain_core.tools import tool, InjectedToolArg
from langgraph.store.base import BaseStore
from pydantic import Field

logger = structlog.get_logger("memory_tools")


# ─── Tool tipado (nuevo — estilo Claude Code) ─────────────────────────────────

@tool
async def save_patient_memory(
    type: Literal["user", "feedback", "project", "reference"] = Field(
        description=(
            "Tipo de memoria:\n"
            "  user      → perfil permanente del paciente: nombre real, alergias, "
            "condiciones médicas, preferencias de horario, datos de contacto adicionales.\n"
            "  feedback  → correcciones y patrones detectados: 'no le gustan los lunes', "
            "'prefiere mensajes cortos', 'se pone ansioso en consultas largas'.\n"
            "  project   → tratamientos en curso o notas clínicas: 'tratamiento de "
            "ortodoncia iniciado 2026-01, revisión cada 3 meses', 'extracción muela "
            "del juicio pendiente'.\n"
            "  reference → punteros a sistemas externos: google_event_id de última cita, "
            "número de expediente, ID en sistema externo."
        )
    ),
    name: str = Field(
        description=(
            "Identificador corto y único para esta memoria. "
            "Snake_case, sin espacios. Ej: 'alergia_penicilina', 'prefiere_mananas', "
            "'tratamiento_ortodoncia', 'ultima_cita_id'."
        )
    ),
    description: str = Field(
        description=(
            "Una sola línea describiendo el contenido. Máx 120 caracteres. "
            "Se usa como índice para decidir relevancia. "
            "Ej: 'Alérgico a la penicilina — reacción severa confirmada'"
        )
    ),
    body: str = Field(
        description=(
            "Contenido completo de la memoria. "
            "Para feedback: incluir regla + 'Por qué:' + 'Cómo aplicar:'. "
            "Para project: incluir fecha de inicio, estado actual y próximos pasos. "
            "Para user/reference: datos directos sin formato especial. "
            "Ejemplo feedback: "
            "'No ofrecer slots los lunes.\\nPor qué: el paciente mencionó que trabaja "
            "doble turno los lunes.\\nCómo aplicar: en check_availability filtrar lunes.'"
        )
    ),
) -> str:
    """
    Guarda o actualiza una memoria tipada sobre el paciente en la base de datos.
    Persiste entre sesiones. Equivalente al sistema de memoria de Claude Code.

    CUÁNDO USAR:
    - El paciente menciona alergias, condiciones médicas, miedos → type='user'
    - El paciente corrige algo o revela una preferencia → type='feedback'
    - Se inicia o completa un tratamiento → type='project'
    - Se crea una cita y se quiere guardar el ID → type='reference'

    CUÁNDO NO USAR:
    - Datos de la sesión actual (nombre, servicio, fecha de la cita en curso)
      → esos van en el state machine, no en memoria.
    - Información que ya está en el estado del sistema.

    NOTA: La ejecución real ocurre en node_execute_memory_tools con el phone del estado.
    """
    # Este cuerpo no se ejecuta directamente — node_execute_memory_tools
    # intercepta el tool call y lo ejecuta con el phone del estado.
    return f"✅ Memoria '{name}' ({type}) registrada"


# ─── Tool vectorial legado (sin tipos — mantener compatibilidad) ───────────────

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
    Guarda o actualiza una memoria semántica sobre el usuario en el vector store.

    PREFERIR save_patient_memory para nuevas memorias — tiene tipos estructurados
    y persiste en base de datos relacional. Este tool es legado para compatibilidad
    con el vector store existente.

    CUÁNDO USAR:
    - Cuando se quiere búsqueda semántica vectorial además de DB estructurada.

    NOTA: user_id y store se inyectan automáticamente desde el grafo.
    """
    if user_id is None:
        return "Error: user_id no inyectado"
    if store is None:
        return "Error: store no inyectado"

    try:
        data = {
            "content": content,
            "context": context,
            "user_id": user_id,
            "updated_at": datetime.utcnow().isoformat(),
        }

        if memory_id:
            await store.ainput(
                ("memories", user_id, memory_id),
                data,
            )
            return f"✅ Memoria actualizada: {content}"
        else:
            memory_id = str(uuid.uuid4())
            await store.aput(
                ("memories", user_id, memory_id),
                data,
            )
            return f"✅ Memoria guardada: {content}"

    except Exception as e:
        logger.error("Error guardando memoria vectorial", error=str(e), exc_info=True)
        return f"❌ Error guardando memoria: {str(e)}"
