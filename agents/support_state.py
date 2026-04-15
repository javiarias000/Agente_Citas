#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Schema de estado para State Machine de Arcadium.
Define la estructura completa de SupportState con todos los campos
que se van poblando durante el workflow de agendamiento de citas.
"""

from typing import Literal, NotRequired, Optional, List, Dict, Any
from typing_extensions import TypedDict
from datetime import datetime


# ============================================
# ENUMS - Estados e intenciones
# ============================================

SupportStep = Literal[
    "reception",           # Paso 1: Identificación de intención
    "info_collector",      # Paso 2: Recolección de datos (servicio, fecha)
    "scheduler",           # Paso 3: Consulta disponibilidad y agendado
    "resolution"           # Paso 4: Gestión posterior (confirmación, cancel, reagend)
]

Intent = Literal[
    "agendar",            # Quiere reservar nueva cita
    "consultar",          # Solo quiere ver disponibilidad
    "cancelar",           # Quiere eliminar cita existente
    "reagendar",          # Quiere modificar fecha/hora
    "otro"                # Otro motivo / no clasificado
]

ServiceType = Literal[
    "consulta",           # Consulta dental (60 min)
    "limpieza",           # Limpieza dental (60 min)
    "empaste",            # Empaste/res filling (60 min)
    "extraccion",         # Extracción dental (60 min)
    "endodoncia",         # Conducto (60-90 min)
    "ortodoncia",         # Ortodoncia (60 min)
    "cirugia",            # Cirugía (60-90 min)
    "implantes",          # Implante dental (90 min)
    "estetica",           # Estética dental (60 min)
    "odontopediatria"     # Niños (60 min)
]


# ============================================
# SCHEMA PRINCIPAL
# ============================================

class SupportState(TypedDict):
    """
    Estado completo de la state machine de agendamiento de citas.

    Cada campo se va poblando según avanza el workflow.
    Todos los campos son opcionales (NotRequired) porque se building progresivamente.
    """

    # === CAMPO REQUERIDO (controla flujo) ===
    current_step: NotRequired[SupportStep]

    # === METADATA ===
    conversation_turns: NotRequired[int]           # Número de turnos en este flujo
    last_tool_used: NotRequired[str]              # Última herramienta ejecutada
    errors_encountered: NotRequired[List[str]]    # Errores recuperables

    # === RECEPCIÓN ===
    intent: NotRequired[Intent]                   # Intención detectada

    # === INFORMACIÓN ===
    patient_name: NotRequired[str]                # Nombre del paciente (opcional)
    patient_phone: NotRequired[str]              # Teléfono del paciente (opcional, diferente al session_id?)
    selected_service: NotRequired[ServiceType]   # Servicio dental elegido
    service_duration: NotRequired[int]           # Duración en minutos (60, 90)
    datetime_preference: NotRequired[str]        # Fecha/hora preferida (ISO: "2025-12-25T14:30")
    datetime_alternatives: NotRequired[List[str]]  # Alternativas si no disponible

    # === COORDINACIÓN ===
    availability_checked: NotRequired[bool]       # Ya consultó disponibilidad
    available_slots: NotRequired[List[str]]      # Lista de slots libres encontrados (ISO strings)
    selected_slot: NotRequired[str]              # Slot elegido por usuario (ISO)
    appointment_id: NotRequired[str]             # UUID de cita en DB
    google_event_id: NotRequired[str]            # ID evento en Google Calendar
    google_event_link: NotRequired[str]          # Enlace al evento

    # === RESOLUCIÓN ===
    confirmation_sent: NotRequired[bool]         # Confirmación enviada
    appointment_details: NotRequired[Dict[str, Any]]  # Detalles completos de la cita
    follow_up_needed: NotRequired[bool]          # Requiere seguimiento posterior


# ============================================
# HELPERS PARA VALIDACIONES
# ============================================

def is_valid_state(state: Dict[str, Any]) -> bool:
    """
    Valida que el estado tenga al menos current_step definido.
    """
    return "current_step" in state and state["current_step"] in ["reception", "info_collector", "scheduler", "resolution"]


def get_required_fields_for_step(step: SupportStep) -> List[str]:
    """
    Devuelve lista de campos requeridos para poder transitar DESDE un estado.
    (No para entrar, sino para poder avanzar al siguiente)

    Ejemplo:
    - En info_collector: necesita selected_service y datetime_preference para passar a scheduler
    """
    requirements = {
        "reception": ["intent"],  # Necesita intención para saber a dónde ir
        "info_collector": ["selected_service", "datetime_preference"],  # Para poder agendar/consultar
        "scheduler": ["selected_service"],  # Necesita al menos el servicio para consultar/agendar
        "resolution": []  # Puede terminar o reiniciar sin requisitos
    }
    return requirements.get(step, [])


def get_optional_fields_for_step(step: SupportStep) -> List[str]:
    """
    Devuelve campos opcionales que pueden o no estar presentes en un estado.
    """
    optionals = {
        "reception": [],
        "info_collector": ["patient_name", "patient_phone"],
        "scheduler": ["datetime_preference", "availability_checked", "available_slots"],
        "resolution": ["appointment_id", "google_event_link"]
    }
    return optionals.get(step, [])


def can_transition_from(step: SupportStep, to_step: SupportStep) -> bool:
    """
    Valida si una transición es permitida según el diagrama de estados.

    Transiciones válidas:
    - reception → info_collector (agendar)
    - reception → scheduler (consultar)
    - reception → resolution (cancelar/reagendar)
    - info_collector → scheduler (completo)
    - info_collector → reception (cambiar opinión)
    - scheduler → resolution (agendado / solo consulta)
    - scheduler → info_collector (no disponible, cambiar fecha)
    - resolution → info_collector (reagendar)
    - resolution → reception (cancelar)
    """
    valid_transitions = {
        "reception": ["info_collector", "scheduler", "resolution"],
        "info_collector": ["scheduler", "reception"],
        "scheduler": ["resolution", "info_collector"],
        "resolution": ["info_collector", "reception"]
    }
    return to_step in valid_transitions.get(step, [])


def get_step_name(step: SupportStep) -> str:
    """Devuelve nombre legible del estado."""
    names = {
        "reception": "Recepción",
        "info_collector": "Recolección de Información",
        "scheduler": "Coordinación de Agenda",
        "resolution": "Resolución"
    }
    return names.get(step, step)


# ============================================
# CONSTANTES
# ============================================

DURATION_BY_SERVICE: Dict[ServiceType, int] = {
    "consulta": 60,
    "limpieza": 60,
    "empaste": 60,
    "extraccion": 60,
    "endodoncia": 60,  # Podría ser 90 dependiendo complejidad
    "ortodoncia": 60,
    "cirugia": 90,
    "implantes": 90,
    "estetica": 60,
    "odontopediatria": 60  # Niño
}

# Servicios que requieren más de 30 minutos
LONG_DURATION_SERVICES = ["endodoncia", "cirugia", "implantes"]

# Horario laboral (parámetros configurables)
BUSINESS_HOURS_START = 9   # 9:00 AM
BUSINESS_HOURS_END = 18    # 6:00 PM
BUSINESS_DAYS = [0, 1, 2, 3, 4]  # Lun-Vie (0=Monday)


# ============================================
# FUNCIONES DE UTILIDAD PARA EL STATE MACHINE
# ============================================

def create_initial_state(step: SupportStep = "reception") -> Dict[str, Any]:
    """
    Crea un estado inicial vacío para una nueva conversación.

    Args:
        step: Estado inicial (default: reception)

    Returns:
        Diccionario con current_step y resto de campos vacíos/None
    """
    return {
        "current_step": step,
        "conversation_turns": 0,
        "errors_encountered": []
    }


def increment_turns(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Incrementa el contador de turnos en el estado.
    Se debe llamar después de cada interacción del usuario.
    """
    state["conversation_turns"] = state.get("conversation_turns", 0) + 1
    return state


def add_error(state: Dict[str, Any], error: str) -> List[str]:
    """
    Registra un error recuperable en el estado y devuelve la lista actualizada.
    NOTA: La función muta state['errors_encountered'] y devuelve la lista para
    facilitar asignación en Command.update (que espera el valor, no la mutación).
    """
    errors = state.get("errors_encountered", [])
    errors.append(error)
    state["errors_encountered"] = errors
    return errors


def clear_errors(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Limpia la lista de errores (ej: después de transición exitosa).
    """
    state["errors_encountered"] = []
    return state


def get_service_duration(service: ServiceType) -> int:
    """
    Devuelve duración en minutos para un servicio dado.
    """
    return DURATION_BY_SERVICE.get(service, 30)  # Default 30 min


def is_complete_for_step(step: SupportStep, state: Dict[str, Any]) -> bool:
    """
    Determina si el estado actual tiene toda la información necesaria
    para poder transitar al siguiente estado.

    No todas las transiciones requieren todos los campos - depende del contexto.
    """
    required = get_required_fields_for_step(step)

    for field in required:
        if field not in state or state[field] is None:
            return False

    # Validaciones específicas por step:
    if step == "info_collector":
        # Para transitar a scheduler, necesita al menos servicio y fecha
        if not state.get("selected_service") or not state.get("datetime_preference"):
            return False

    elif step == "scheduler":
        # Para poder transitar a resolution (o volver atrás), necesita:
        # - selected_service (siempre)
        # - appointment_id si la intención es agendar o reagendar
        if not state.get("selected_service"):
            return False
        intent = state.get("intent")
        if intent in ("agendar", "reagendar") and not state.get("appointment_id"):
            return False

    return True


# ============================================
# EJEMPLOS DE ESTADOS COMPLETOS
# ============================================

def example_reception_state() -> Dict[str, Any]:
    """Estado después de clasificar intención"""
    return {
        "current_step": "info_collector",
        "intent": "agendar",
        "conversation_turns": 1
    }


def example_info_collector_state() -> Dict[str, Any]:
    """Estado después de tener servicio y fecha"""
    return {
        "current_step": "scheduler",
        "intent": "agendar",
        "selected_service": "limpieza",
        "service_duration": 60,
        "datetime_preference": "2025-12-25T14:30",
        "conversation_turns": 3
    }


def example_scheduler_state() -> Dict[str, Any]:
    """Estado después de agendar exitosamente"""
    return {
        "current_step": "resolution",
        "intent": "agendar",
        "selected_service": "limpieza",
        "datetime_preference": "2025-12-25T14:30",
        "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
        "google_event_id": "google_event_123",
        "google_event_link": "https://calendar.google.com/event?eid=...",
        "conversation_turns": 5
    }


def example_resolution_state() -> Dict[str, Any]:
    """Estado de resolución (cita agendada)"""
    return {
        "current_step": "resolution",
        "intent": "agendar",
        "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
        "appointment_details": {
            "fecha": "2025-12-25T14:30",
            "servicio": "limpieza",
            "duracion": 60,
            "estado": "confirmed"
        },
        "confirmation_sent": True,
        "conversation_turns": 6
    }
