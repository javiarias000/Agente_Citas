"""
Tool de memoria tipada para el agente V2.
Wrapper limpio sobre PatientMemoryService — sin InjectedToolArg.
El phone_number llega como argumento explícito del LLM.
"""

from __future__ import annotations
from typing import Literal
from langchain_core.tools import tool
import structlog

logger = structlog.get_logger("tools.memory")


def make_save_patient_memory_tool():
    """
    Retorna el tool save_patient_memory.
    El phone_number lo pasa el LLM como argumento — viene del contexto del sistema.
    """
    @tool
    async def save_patient_memory(
        type: Literal["user", "feedback", "project", "reference"],
        name: str,
        description: str,
        body: str,
        phone_number: str,
    ) -> str:
        """
        Guarda una memoria tipada del paciente en la base de datos.
        Persiste entre sesiones. Llámala SILENCIOSAMENTE (sin anunciar al paciente).

        Tipos:
          user      → perfil permanente: alergias, condiciones médicas, nombre real,
                      preferencias de horario. Ej: name='alergia_lidocaina'
          feedback  → correcciones y patrones: 'no le gustan los lunes', 'prefiere
                      mensajes cortos'. Incluir Por qué: y Cómo aplicar: en body.
          project   → tratamientos activos: ortodoncia iniciada, implante pendiente.
                      Incluir fecha inicio, estado, próximos pasos.
          reference → IDs externos: google_event_id de última cita, número expediente.

        Cuándo usarla:
          - Paciente menciona alergias o condiciones → type='user'
          - Paciente corrige o revela preferencia → type='feedback'
          - Se confirma inicio/fin de tratamiento → type='project'
          - Se crea una cita → type='reference' con el event_id

        No guardar: datos de la sesión actual que ya están en el estado del sistema.

        Args:
            type:         Tipo de memoria (user/feedback/project/reference)
            name:         Identificador único snake_case. Ej: 'alergia_penicilina'
            description:  Una línea descriptiva. Máx 120 chars.
            body:         Contenido completo. Para feedback incluir 'Por qué:' y 'Cómo aplicar:'.
            phone_number: Teléfono del paciente (viene del contexto del sistema).
        """
        try:
            from db import get_async_session
            from services.patient_memory_service import PatientMemoryService

            async with get_async_session() as session:
                svc = PatientMemoryService(session)
                await svc.upsert(
                    phone=phone_number,
                    type=type,
                    name=name,
                    description=description,
                    body=body,
                )

            logger.info(
                "save_patient_memory OK",
                phone=phone_number[:8] + "...",
                type=type,
                name=name,
            )
            return f"✅ Memoria '{name}' ({type}) guardada."

        except Exception as e:
            logger.error("save_patient_memory error", error=str(e))
            return f"❌ Error guardando memoria: {e}"

    return save_patient_memory
