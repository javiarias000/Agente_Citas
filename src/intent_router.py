"""
Enrutamiento determinista de intenciones.

NUNCA llama al LLM. Solo keywords, regex, y reglas.
"""

import re
import unicodedata
from typing import Dict, List, Literal, Optional


# ═══════════════════════════════════════════
# KEYWORDS POR INTENT
# ═══════════════════════════════════════════

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

CONFIRM_YES = [
    "sí", "si", "claro", "confirmo", "confirmo la cita", "dale",
    "ok", "va", "bueno", "perfecto", "excelente", "yes",
    # Ecuatorianismos comunes
    "de una", "hágale", "hagale", "dale pues",
]

CONFIRM_NO = [
    "no", "mejor no", "no quiero", "no voy", "cancela",
    "olvidalo", "olvídalo", "mejor luego", "despues", "después",
]


def _normalize(text: str) -> str:
    """Lowercase + strip accents + strip punctuation."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Remove punctuation for matching
    text = re.sub(r'[^\w\s]', '', text)
    return text


def route_by_keywords(text: str) -> Optional[str]:
    """
    Detecta intención por matching de keywords.

    Returns el intent con mayor número de coincidencias, o None.
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
    """
    Clasifica respuesta del usuario tras mostrarle algo.

    Returns:
        "yes"         — confirmó (agendar/cancelar según contexto)
        "no"          — rechazó
        "slot_choice" — eligió un horario ("a las 10", "el de las 3:30")
        "unknown"     — no se pudo determinar
    """
    normalized = _normalize(text).strip()
    if not normalized:
        return "unknown"

    # Check exact matches first
    if normalized in CONFIRM_YES:
        return "yes"
    if normalized in CONFIRM_NO:
        return "no"

    # Check substring matches (e.g., "sí, confirmo" contains "sí")
    if any(kw in normalized for kw in CONFIRM_YES):
        return "yes"
    if any(kw in normalized for kw in CONFIRM_NO):
        return "no"

    # Check for time patterns: "a las 10", "las 3", "10:00", "a las 10:30"
    time_pattern = r"(\b(\d{1,2})(:\d{2})?\b)"
    if re.search(time_pattern, text):
        return "slot_choice"

    return "unknown"


def extract_slot_from_text(
    text: str,
    available_slots: List[str],
) -> Optional[str]:
    """
    Extrae hora del texto y la busca en available_slots.

    available_slots = lista de ISO strings como "2026-04-10T10:00:00"
    Retorna el primer slot match, o None.
    """
    times = re.findall(r"(\d{1,2}):(\d{2})", text)
    if times:
        h, m = int(times[0][0]), int(times[0][1])
        candidate = f"T{h:02d}:{m:02d}"
        for slot in available_slots:
            if candidate in slot:
                return slot
    return None
