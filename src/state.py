"""
ArcadiumState — TypedDict único como fuente de verdad para el grafo LangGraph.

Este módulo define:
- ArcadiumState: el único estado del grafo
- Funciones helper para crear, validar y manipular el estado
- Constantes: servicios, intents, keywords
- Fecha/hora helpers deterministas (sin LLM)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# ═══════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════

TIMEZONE = ZoneInfo("America/Guayaquil")

BUSINESS_HOURS = (9, 18)  # 9:00–18:00
SLOT_MINUTES = 30  # granularidad de slots

VALID_SERVICES: Dict[str, int] = {
    "consulta": 30,
    "limpieza": 45,
    "empaste": 45,
    "extraccion": 45,
    "endodoncia": 90,
    "ortodoncia": 60,
    "cirugia": 90,
    "implantes": 90,
    "estetica": 60,
    "odontopediatria": 45,
    "blanqueamiento": 60,
    "revision": 30,
}

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "agendar": [
        "agendar", "agendar cita", "reservar", "reservar cita",
        "agendame", "agéndame", "agenda",
        "turno", "me duele", "dolor de",
        "limpieza", "consulta", "revision", "revisar",
        "quiero ir", "necesito ir",
    ],
    "cancelar": [
        "cancelar", "cancelo", "cancela", "cancelar cita",
        "no puedo", "anular", "anulo", "anula", "desagendar",
        "olvidalo", "olvídalo", "mejor no", "no voy",
    ],
    "reagendar": [
        "reagendar", "reagenda", "cambiar cita", "cambiar fecha",
        "cambiar la fecha", "reprogramar", "otra fecha", "otro dia",
        "otro día", "otro horario", "cambiar de fecha", "mover cita",
    ],
    "consultar": [
        "consultar", "disponible", "disponibilidad",
        "hay espacio", "hay lugar", "horarios", "horario",
        "cuando puedo", "cuándo puedo", "mis citas", "proxima cita",
        "próxima cita", "ver mis citas",
    ],
}

CONFIRM_YES: List[str] = [
    "sí", "si", "claro", "confirmo", "confirmo la cita", "dale",
    "ok", "va", "bueno", "perfecto", "excelente", "yes", "de una",
]
CONFIRM_NO: List[str] = [
    "no", "mejor no", "no quiero", "no voy", "cancela",
    "olvidalo", "olvídalo", "mejor luego", "despues", "después",
]

MAX_TURNS = 10
MAX_ERRORS = 3

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


# ═══════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════

class ArcadiumState(TypedDict, total=False):
    """TypedDict como fuente única de verdad del grafo."""

    # --- Sesión ---
    messages: Annotated[List[BaseMessage], add_messages]
    phone_number: str
    project_id: Optional[uuid.UUID]
    conversation_turns: int

    # --- Fechas pre-calculadas (Python, nunca LLM) ---
    fecha_hoy: str
    hora_actual: str
    dia_semana_hoy: str
    manana_fecha: str
    manana_dia: str

    # --- Flujo ---
    intent: Optional[str]
    awaiting_confirmation: bool
    confirmation_type: Optional[Literal["book", "cancel", "reschedule"]]
    current_step: str
    confirmation_result: Optional[str]

    # --- Paciente ---
    patient_name: Optional[str]
    patient_phone: Optional[str]

    # --- Cita ---
    selected_service: Optional[str]
    service_duration: Optional[int]
    datetime_preference: Optional[str]
    datetime_adjusted: bool

    # --- Disponibilidad ---
    available_slots: List[str]
    selected_slot: Optional[str]

    # --- Resultado ---
    appointment_id: Optional[str]
    google_event_id: Optional[str]
    google_event_link: Optional[str]
    confirmation_sent: bool

    # --- Control ---
    missing_fields: List[str]
    last_error: Optional[str]
    errors_count: int
    should_escalate: bool
    _extract_data_calls: int  # counter to prevent extract_data loops


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _next_monday(dt: datetime) -> datetime:
    """Avanza al siguiente lunes (salta sábado+domingo)."""
    days_ahead = 7 - dt.weekday() if dt.weekday() >= 5 else 0
    return dt + timedelta(days=days_ahead) if days_ahead else dt


def create_initial_arcadium_state(
    phone_number: str,
    project_id: Optional[uuid.UUID] = None,
) -> ArcadiumState:
    """Crea el estado inicial para un nuevo ingreso de webhook."""

    now = datetime.now(TIMEZONE)
    manana = now + timedelta(days=1)

    return ArcadiumState(
        messages=[],
        phone_number=phone_number,
        project_id=project_id,
        conversation_turns=0,
        # Fechas calculadas por Python (nunca LLM)
        fecha_hoy=now.strftime("%Y-%m-%d"),
        hora_actual=now.strftime("%H:%M"),
        dia_semana_hoy=DIAS_ES[now.weekday()],
        manana_fecha=manana.strftime("%Y-%m-%d"),
        manana_dia=DIAS_ES[manana.weekday()],
        # Flujo
        intent=None,
        awaiting_confirmation=False,
        confirmation_type=None,
        current_step="entry",
        confirmation_result=None,
        # Paciente
        patient_name=None,
        patient_phone=None,
        # Cita
        selected_service=None,
        service_duration=None,
        datetime_preference=None,
        datetime_adjusted=False,
        # Disponibilidad
        available_slots=[],
        selected_slot=None,
        # Resultado
        appointment_id=None,
        google_event_id=None,
        google_event_link=None,
        confirmation_sent=False,
        # Control
        missing_fields=["patient_name", "selected_service", "datetime_preference"],
        last_error=None,
        errors_count=0,
        should_escalate=False,
    )


def _normalize(text: str) -> str:
    """Lowercase + quita tildes para matching de keywords."""
    import unicodedata
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text


def route_by_keywords(text: str) -> Optional[str]:
    """Detecta intención por keywords (determinista, sin LLM).

    Returns el intent con más coincidencias, o None si no hay match.
    """
    normalized = _normalize(text)
    scores: Dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in normalized)
        if count > 0:
            scores[intent] = count
    if not scores:
        return None
    return max(scores, key=scores.get)


def detect_confirmation(text: str) -> Literal["yes", "no", "slot_choice", "unknown"]:
    """Detecta si el texto es una afirmación, negación o selección de slot."""
    normalized = _normalize(text).strip()
    if not normalized:
        return "unknown"
    if normalized in CONFIRM_YES:
        return "yes"
    if normalized in CONFIRM_NO:
        return "no"
    import re
    if re.search(r"\b(\d{1,2}(:\d{2})?)\b", text):  # "a las 10", "10:00", "las 3"
        return "slot_choice"
    return "unknown"


def extract_slot_from_text(
    text: str,
    available_slots: List[str],
) -> Optional[str]:
    """Mapea una referencia horaria del texto al slot exacto disponible."""
    import re
    times = re.findall(r"(\d{1,2}):(\d{2})", text)
    if times:
        candidate = f"{int(times[0][0]):02d}:{times[0][1]}"
        for slot in available_slots:
            if candidate in slot:
                return slot
    return None


def get_missing_fields(state: ArcadiumState) -> List[str]:
    """Determina qué campos obligatorios faltan."""
    missing = []
    if not state.get("patient_name") and state.get("patient_name") is not False:
        missing.append("patient_name")
    if not state.get("selected_service"):
        missing.append("selected_service")
    if not state.get("datetime_preference"):
        missing.append("datetime_preference")
    return missing


def is_weekend_adjusted(iso_str: str) -> tuple[bool, str]:
    """Si la fecha cae en fin de semana, devuelve (True, lunes_ajustado)."""
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return False, iso_str
    return (dt.weekday() >= 5, _next_monday(dt).isoformat() if dt.weekday() >= 5 else iso_str)
