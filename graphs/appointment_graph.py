#!/usr/bin/env python3
"""
State Graph para AppointmentAgent.
Grafo de estado que controla el flujo de agendado.
Cada nodo es una función que recibe estado y devuelve Command.
El LLM solo se usa para generar mensajes naturales, no para decidir el flujo.
"""

from typing import Dict, Any, List, Literal, Annotated
from datetime import datetime, timedelta
from pydantic import Field
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END

from agents.tools_state_machine import (
    consultar_disponibilidad,
    agendar_cita,
    record_patient_name,
    get_current_phone
)
from utils.logger import get_logger

logger = get_logger("graph.appointment")


# ============================================
# ESTADO DEL GRAFO DE CITAS
# ============================================

class AppointmentState(Dict[str, Any]):
    """Estado compartido entre todos los nodos."""
    # Datos del usuario
    patient_name: Optional[str] = None
    phone_number: Optional[str] = None

    # Datos de la cita
    selected_service: Optional[str] = None
    datetime_preference: Optional[str] = None  # ISO string
    selected_slot: Optional[str] = None  # "HH:MM"

    # Resultados de herramientas
    available_slots: Optional[List[Dict]] = None

    # Control de flujo
    current_step: str = "intake"  # intake → validate → require_name → check_availability → confirm → schedule → done

    # Mensajes
    messages: List[Dict] = []


# ============================================
# NODOS DEL GRAFO
# ============================================

async def intake_node(state: AppointmentState) -> Command:
    """
    Nodo inicial: extrae servicio del mensaje del usuario.
    """
    user_message = state.get("user_message", "")

    # Extraer servicio por keywords
    service_keywords = {
        "limpieza": "limpieza",
        "consulta": "consulta",
        "ortodoncia": "ortodoncia",
        "empaste": "empaste",
        "extracción": "extraccion",
        "extraccion": "extraccion",
        "cirugía": "cirugia",
        "cirugia": "cirugia",
        "implante": "implantes",
        "implantes": "implantes",
        "blanqueamiento": "blanqueamiento",
        "revisión": "revision",
        "revision": "revision"
    }

    detected_service = None
    for keyword, service in service_keywords.items():
        if keyword in user_message.lower():
            detected_service = service
            break

    # Extraer hora
    import re
    time_match = re.search(r'(\d{1,2}):?(\d{0,2})', user_message)
    detected_time = None
    if time_match:
        hour = time_match.group(1).zfill(2)
        minute = time_match.group(2) or "00"
        minute = minute.ljust(2, '0')[:2]
        detected_time = f"{hour}:{minute}"

    updates = {
        "selected_service": detected_service,
        "_temp_hour": detected_time,
        "current_step": "validate" if detected_service else "intake"
    }

    logger.info("Intake processed", service=detected_service, time=detected_time)
    return Command(update=updates)


async def validate_date_node(state: AppointmentState) -> Command:
    """
    Valida y ajusta fecha (fin de semana → lunes).
    """
    user_message = state.get("user_message", "")
    today = datetime.now()
    target_date = today + timedelta(days=1)  # Por defecto: mañana

    # Ajuste fin de semana
    if target_date.weekday() >= 5:  # Sábado o Domingo
        days_to_monday = 7 - target_date.weekday()
        target_date = target_date + timedelta(days=days_to_monday)

    hour = state.get("_temp_hour") or "10:00"
    try:
        dt_combined = datetime(
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
            hour=int(hour.split(":")[0]),
            minute=int(hour.split(":")[1])
        )
        iso_datetime = dt_combined.isoformat()
    except Exception as e:
        logger.error("Error parsing datetime", error=str(e))
        iso_datetime = None

    updates = {
        "datetime_preference": iso_datetime,
        "current_step": "require_name"
    }

    logger.info("Date validated", iso=iso_datetime, weekend_adjust=target_date.weekday() >= 5)
    return Command(update=updates)


async def require_name_node(state: AppointmentState) -> Command:
    """
    Verifica si tiene nombre. Si no, pide.
    """
    if state.get("patient_name"):
        return Command(update={"current_step": "check_availability"})

    # Marcar que necesita nombre
    return Command(update={"current_step": "require_name"})


async def check_availability_node(state: AppointmentState) -> Command:
    """
    Prepara para consultar disponibilidad.
    En lugar de ejecutar la tool aquí, marcamos que debe consultarse.
    El LLM ejecutará la tool cuando reciba este estado.
    """
    fecha = state.get("datetime_preference")
    servicio = state.get("selected_service")

    if not fecha or not servicio:
        logger.warning("Missing data for availability")
        return Command(update={"current_step": "end"})

    return Command(update={
        "current_step": "confirm",
        "_need_to_check_availability": True
    })


async def confirm_node(state: AppointmentState) -> Command:
    """
    Marca que debe pedirse confirmación.
    """
    return Command(update={"current_step": "awaiting_confirmation"})


async def schedule_node(state: AppointmentState) -> Command:
    """
    Marca que debe ejecutarse agendar_cita.
    """
    return Command(update={
        "current_step": "resolution",
        "_schedule_requested": True
    })


async def done_node(state: AppointmentState) -> Command:
    """
    Nodo final.
    """
    return Command(update={"current_step": "end"})


# ============================================
# CREACIÓN DEL GRAFO
# ============================================

def create_appointment_graph() -> StateGraph:
    """
    Crea y retorna el state graph compilado para AppointmentAgent.
    """
    workflow = StateGraph(AppointmentState)

    # Nodos
    workflow.add_node("intake", intake_node)
    workflow.add_node("validate", validate_date_node)
    workflow.add_node("require_name", require_name_node)
    workflow.add_node("check_availability", check_availability_node)
    workflow.add_node("confirm", confirm_node)
    workflow.add_node("schedule", schedule_node)
    workflow.add_node("done", done_node)

    # Punto de entrada
    workflow.set_entry_point("intake")

    # Transiciones
    workflow.add_conditional_edges(
        "intake",
        lambda state: "validate" if state.get("selected_service") else "intake"
    )
    workflow.add_edge("validate", "require_name")

    workflow.add_conditional_edges(
        "require_name",
        lambda state: "check_availability" if state.get("patient_name") else "require_name"
    )

    workflow.add_edge("check_availability", "confirm")
    workflow.add_conditional_edges(
        "confirm",
        lambda state: "schedule" if state.get("confirmation_received") else "awaiting_confirmation"
    )

    workflow.add_edge("schedule", "done")
    workflow.add_edge("done", END)

    return workflow.compile()
