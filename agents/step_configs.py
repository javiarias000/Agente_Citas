#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuración de prompts y herramientas por estado (step).
Cada estado tiene su propio prompt, conjunto de herramientas, y requisitos.
"""

from typing import Dict, Any, List
from langchain_core.prompts import ChatPromptTemplate

from agents.support_state import SupportStep


# ============================================
# PROMPTS
# ============================================

RECEPTION_PROMPT = """You are Deyy, a dental appointment assistant.

CURRENT STEP: RECEPTION (Intent classification)

GOAL: Use the classify_intent tool to determine what the user wants.

AVAILABLE TOOL: classify_intent(user_message=...)

INSTRUCTIONS:
- Call classify_intent with the user's message
- DO NOT respond with text
- DO NOT use other tools

The system will handle transition automatically after classify_intent.
"""

INFO_COLLECTOR_PROMPT = """You are Deyy, a dental appointment assistant.

CURRENT STEP: **INFO COLLECTOR** (Gathering patient data)

CURRENT DATE: {current_date}

INTENT: {intent}

CURRENT DATA:
- Service: {selected_service}
- Date/Time: {datetime_preference}

TOOLS AVAILABLE:
- record_service_selection(service: str) - when user mentions a dental service
- record_datetime_pref(fecha: str) - when user mentions a date/time (ISO format: YYYY-MM-DDTHH:MM)

VALID DENTAL SERVICES (use exactly these names):
- consulta (general checkup)
- limpieza (cleaning)
- empaste (filling)
- extraccion (extraction)
- endodoncia (root canal)
- ortodoncia (orthodontics)
- cirugia (surgery)
- implantes (implants)
- estetica (cosmetic)
- odontopediatria (pediatric)

CRITICAL RULES:
1. IF user mentions a service AND selected_service is empty → CALL record_service_selection IMMEDIATELY with the service name (choose from valid list, or closest match)
2. IF user mentions a date/time AND datetime_preference is empty → CALL record_datetime_pref IMMEDIATELY with an ISO datetime
   - Use current_date as reference for relative dates like "tomorrow", "Friday", "next week"
   - Example: if today is 2026-04-04 (Friday) and user says "Friday at 3pm", that means TODAY (2026-04-04) if time is in the future, OR next Friday (2026-04-11) if time already passed
   - Always produce a future date/time (>= current datetime)
   - Business hours: Monday-Friday 9:00-18:00 only. Do NOT schedule on weekends.
3. ⚠️ NEVER call record_datetime_pref UNLESS the user EXPLICITLY mentions a date or time
4. ⚠️ NEVER guess, invent, or assume a date. Wait for the user to provide it.
5. ⚠️ Call ONLY ONE tool per turn, unless the user provides BOTH service and date in the SAME message.
6. DO NOT respond with text. ONLY use tool calls.
7. The system will auto-transition to SCHEDULER once both service and date are recorded

IMPORTANT: The fecha must be in ISO format (e.g., "2026-04-11T15:00") and must be in the future, on a weekday (Mon-Fri).
"""

SCHEDULER_PROMPT = """Eres Deyy, asistente de gestión de citas dentales.

ESTADO ACTUAL: **COORDINACIÓN** (Consulta y agendado)

CONTEXTO:
- Servicio: {selected_service} ({service_duration} min)
- Fecha preferida: {datetime_preference}
- Disponibilidad consultada: {availability_checked}
- Slots libres: {available_slots}

FLUJO:
1. Si NO has consultado disponibilidad → usa consultar_disponibilidad()
2. Muestra los slots disponibles
3. Pregunta cuál prefiere el usuario
4. Cuando elija → usa agendar_cita(fecha="ISO_FORMAT")
5. Al agendar, transita automáticamente a RESOLUCIÓN

HERRAMIENTAS: {tool_names}

INSTRUCCIONES CRÍTICAS:
✅ NUNCA agendes sin confirmación EXPLÍCITA
✅ SIEMPRE muestra slots antes de agendar
✅ Valida horario laboral (Lun-Vie 9-18)
"""

RESOLUTION_PROMPT = """Eres Deyy, asistente de gestión de citas dentales.

ESTADO ACTUAL: **RESOLUCIÓN** (Gestión posterior)

CONTEXTO:
- Cita: {appointment_id}
- Fecha: {selected_date}
- Servicio: {selected_service}
- Intención original: {intent}

FLUJO:
1. Muestra resumen de la cita (si hay)
2. Ofrece enlace de Google Calendar (si disponible)
3. Pregunta: "¿Necesitas algo más?"

HERRAMIENTAS: {tool_names}

ACCIONES DISPONIBLES:
- Si quiere modificar: reagendar_cita()
- Si quiere cancelar: cancelar_cita()
- Si quiere ver citas: obtener_citas_cliente()
- Si terminó: despedida amable
"""


# ============================================
# STEP CONFIGURATIONS (sin herramientas - se llenan después)
# ============================================

STEP_CONFIGS: Dict[SupportStep, Dict[str, Any]] = {
    "reception": {
        "name": "Recepción",
        "description": "Identificación de intención",
        "prompt": ChatPromptTemplate.from_template(RECEPTION_PROMPT),
        "tools": [],  # Se llenará en initialize_step_tools()
        "requires": ["intent"],  # Necesita intención para transitar
        "can_transition_to": ["info_collector", "scheduler", "resolution"],
        "next_step_map": {
            "agendar": "info_collector",
            "consultar": "scheduler",
            "cancelar": "resolution",
            "reagendar": "resolution",
            "otro": "reception"
        }
    },

    "info_collector": {
        "name": "Recolección de Información",
        "description": "Recopilar datos: servicio, fecha",
        "prompt": ChatPromptTemplate.from_template(INFO_COLLECTOR_PROMPT),
        "tools": [],  # Se llenará
        "requires": ["selected_service", "datetime_preference"],  # Necesita servicio y fecha para transitar
        "can_transition_to": ["scheduler", "reception"]
    },

    "scheduler": {
        "name": "Coordinación de Agenda",
        "description": "Consultar disponibilidad y agendar",
        "prompt": ChatPromptTemplate.from_template(SCHEDULER_PROMPT),
        "tools": [],  # Se llenará
        "requires": ["selected_service"],  # Necesita al menos el servicio
        "can_transition_to": ["resolution", "info_collector"],
        "auto_transition_on": ["appointment_id"]  # Si agendar_cita() pone esto
    },

    "resolution": {
        "name": "Resolución",
        "description": "Gestión posterior",
        "prompt": ChatPromptTemplate.from_template(RESOLUTION_PROMPT),
        "tools": [],  # Se llenará
        "requires": [],  # Sin requisitos estrictos
        "can_transition_to": ["info_collector", "reception"]
    }
}


# ============================================
# FUNCIONES DE AYUDA
# ============================================

def get_config_for_step(step: SupportStep) -> Dict[str, Any]:
    """Devuelve la configuración completa para un estado."""
    return STEP_CONFIGS.get(step, {})


def get_prompt_for_step(step: SupportStep):
    """Devuelve el prompt (ChatPromptTemplate) para un estado."""
    config = get_config_for_step(step)
    return config.get("prompt")


def get_tools_for_step(step: SupportStep) -> List:
    """Devuelve la lista de herramientas disponibles en un estado."""
    config = get_config_for_step(step)
    return config.get("tools", [])


def get_required_fields(step: SupportStep) -> List[str]:
    """Devuelve campos requeridos para un estado."""
    config = get_config_for_step(step)
    return config.get("requires", [])


def get_next_step(from_step: SupportStep, intent: str = None) -> SupportStep:
    """
    Determina el siguiente estado basado en reglas.

    Args:
        from_step: Estado actual
        intent: Intención (necesaria para reception)

    Returns:
        Siguiente SupportStep
    """
    config = get_config_for_step(from_step)

    if from_step == "reception" and intent:
        next_map = config.get("next_step_map", {})
        return next_map.get(intent, "reception")

    # Para otros estados, devolver el primero en can_transition_to
    # (en la práctica, lo determinan las tools con Command)
    return config.get("can_transition_to", ["reception"])[0]


def validate_transition(from_step: SupportStep, to_step: SupportStep) -> bool:
    """Valida si una transición está permitida."""
    config = get_config_for_step(from_step)
    allowed = config.get("can_transition_to", [])
    return to_step in allowed


def initialize_step_tools():
    """
    Inicializa las listas de herramientas en STEP_CONFIGS.
    Debe llamarse después de importar STATE_MACHINE_TOOLS completos.
    """
    from agents.tools_state_machine import STATE_MACHINE_TOOLS

    # Mapeo tool.name -> tool
    tools_by_name = {tool.name: tool for tool in STATE_MACHINE_TOOLS}

    # Recepción: SOLO classify_intent (forzar clasificación first)
    STEP_CONFIGS["reception"]["tools"] = [
        tools_by_name["classify_intent"]
    ]

    # Info Collector: NO incluimos record_service_selection porque lo manejamos via fallback determinista
    STEP_CONFIGS["info_collector"]["tools"] = [
        tools_by_name["record_datetime_pref"],
        tools_by_name["transition_to"],
        tools_by_name["go_back_to"],
        tools_by_name["consultar_disponibilidad"]  # Por si quiere consultar antes
    ]

    # Scheduler
    STEP_CONFIGS["scheduler"]["tools"] = [
        tools_by_name["consultar_disponibilidad"],
        tools_by_name["agendar_cita"],
        tools_by_name["record_appointment"],
        tools_by_name["go_back_to"],
        tools_by_name["transition_to"]
    ]

    # Resolution
    STEP_CONFIGS["resolution"]["tools"] = [
        tools_by_name["obtener_citas_cliente"],
        tools_by_name["cancelar_cita"],
        tools_by_name["reagendar_cita"],
        tools_by_name["go_back_to"],
        tools_by_name["transition_to"]
    ]


# NOTA: Se debe llamar a initialize_step_tools() explícitamente
# después de importar todos los módulos. Lo hace StateMachineAgent.initialize()

