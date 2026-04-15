"""
Schemas Pydantic V2 para el agente ReAct (graph_v2).

Reemplaza el parsing frágil de JSON con modelos tipados y validados.
Los tools retornan estas clases directamente; node_execute_tools
las serializa con .model_dump_json() para los ToolMessages.
"""

from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


# ── Resultados de tools de calendario ────────────────────────────────────────

class SlotInfo(BaseModel):
    iso: str          # "2026-04-15T10:00:00-05:00"
    display: str      # "miércoles 15/04 a las 10:00"


class CheckAvailabilityResult(BaseModel):
    success: bool
    slots: List[SlotInfo] = Field(default_factory=list)
    duration_minutes: int = 60
    date_adjusted: bool = False   # True si sábado/domingo → lunes
    error: Optional[str] = None


class BookAppointmentResult(BaseModel):
    success: bool
    event_id: Optional[str] = None
    event_link: Optional[str] = None
    appointment_id: Optional[str] = None
    slot_iso: Optional[str] = None
    service: Optional[str] = None
    patient_name: Optional[str] = None
    error: Optional[str] = None


class CancelAppointmentResult(BaseModel):
    success: bool
    event_id: Optional[str] = None
    error: Optional[str] = None


class AppointmentInfo(BaseModel):
    event_id: str
    title: str
    start_iso: str
    end_iso: str
    display: str


class LookupAppointmentsResult(BaseModel):
    success: bool
    appointments: List[AppointmentInfo] = Field(default_factory=list)
    total_found: int = 0
    error: Optional[str] = None


class RescheduleAppointmentResult(BaseModel):
    success: bool
    old_event_id: Optional[str] = None
    new_event_id: Optional[str] = None
    new_event_link: Optional[str] = None
    new_slot_iso: Optional[str] = None
    service: Optional[str] = None
    patient_name: Optional[str] = None
    error: Optional[str] = None


# ── Mapa de nombres de tool → updates de estado ──────────────────────────────

def extract_state_updates(tool_name: str, result) -> dict:
    """
    Extrae los campos del ArcadiumState que deben actualizarse
    según el resultado de un tool call.

    Centraliza la lógica que antes estaba dispersa en cada nodo determinista.
    """
    updates: dict = {}

    if tool_name == "check_availability":
        if isinstance(result, CheckAvailabilityResult):
            if result.success and result.slots:
                updates["available_slots"] = [s.iso for s in result.slots]
                updates["service_duration"] = result.duration_minutes
                updates["awaiting_confirmation"] = True
                updates["confirmation_type"] = "book"
            else:
                updates["available_slots"] = []
                updates["last_error"] = result.error or "No hay slots disponibles"

    elif tool_name == "book_appointment":
        if isinstance(result, BookAppointmentResult):
            if result.success and result.event_id:
                updates["google_event_id"] = result.event_id
                updates["google_event_link"] = result.event_link
                updates["appointment_id"] = result.appointment_id or f"gcal_{result.event_id}"
                updates["confirmation_sent"] = True
                updates["awaiting_confirmation"] = False
                updates["confirmation_type"] = None
                updates["available_slots"] = []
                updates["selected_slot"] = result.slot_iso
                if result.service:
                    updates["selected_service"] = result.service
                if result.patient_name:
                    updates["patient_name"] = result.patient_name
            else:
                updates["last_error"] = result.error or "Error agendando cita"
                updates["should_escalate"] = True

    elif tool_name == "cancel_appointment":
        if isinstance(result, CancelAppointmentResult):
            if result.success:
                updates["confirmation_sent"] = True
                updates["awaiting_confirmation"] = False
                updates["confirmation_type"] = None
                updates["google_event_id"] = None
                updates["google_event_link"] = None
                updates["appointment_id"] = None
            else:
                updates["last_error"] = result.error or "Error cancelando cita"

    elif tool_name == "lookup_appointments":
        if isinstance(result, LookupAppointmentsResult):
            updates["calendar_lookup_done"] = True
            updates["calendar_appointment_found"] = result.total_found > 0
            if result.appointments:
                updates["existing_appointments"] = [
                    {
                        "event_id": a.event_id,
                        "summary": a.title,
                        "start": a.start_iso,
                        "html_link": "",
                        "description": "",
                    }
                    for a in result.appointments
                ]
                # Cargar el event_id del más reciente para cancelar/reagendar
                updates["google_event_id"] = result.appointments[0].event_id
                updates["awaiting_confirmation"] = True
                updates["confirmation_type"] = "cancel"

    elif tool_name == "reschedule_appointment":
        if isinstance(result, RescheduleAppointmentResult):
            if result.success and result.new_event_id:
                updates["google_event_id"] = result.new_event_id
                updates["google_event_link"] = result.new_event_link
                updates["appointment_id"] = f"gcal_{result.new_event_id}"
                updates["confirmation_sent"] = True
                updates["awaiting_confirmation"] = False
                updates["confirmation_type"] = None
                updates["selected_slot"] = result.new_slot_iso
                if result.service:
                    updates["selected_service"] = result.service
            else:
                updates["last_error"] = result.error or "Error reagendando cita"
                updates["should_escalate"] = True

    return updates
