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
        # Frases naturales con "cita"
        "cita", "una cita", "quiero cita", "necesito cita",
        "quiero una cita", "necesito una cita", "sacar cita",
        "pedir cita", "solicitar cita", "hacer cita",
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
    # Frases adicionales de confirmación
    "listo", "de acuerdo", "acepto", "va bien", "está bien",
    "esta bien", "me parece bien", "adelante", "cuando gusten",
    "cuando puedan", "por favor", "procede", "sigue", "confirma",
    "eso", "esa", "la primera", "la segunda", "la tercera", "esa hora",
    "esa opcion", "esa opción",
]

CONFIRM_NO = [
    "no", "mejor no", "no quiero", "no voy", "cancela",
    "olvidalo", "olvídalo", "mejor luego", "despues", "después",
    "no gracias", "no por ahora", "en otro momento", "otro día",
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

    # Check for time patterns BEFORE word-boundary matches.
    # "A las 9 esta bien" contains both a time AND a confirm word.
    # Explicit hour wins: if user named a time, that's a slot choice.
    time_pattern = r"(\b(\d{1,2})(:\d{2})?\b)"
    if re.search(time_pattern, text):
        return "slot_choice"

    # Check word-boundary matches (e.g., "sí, confirmo" contains "sí" as a word)
    # Se usa \b para no confundir "no" dentro de "noche", "uno", etc.
    # ni "si" dentro de "servicio".
    if any(re.search(r"\b" + re.escape(kw) + r"\b", normalized) for kw in CONFIRM_YES):
        return "yes"
    if any(re.search(r"\b" + re.escape(kw) + r"\b", normalized) for kw in CONFIRM_NO):
        return "no"

    return "unknown"


def extract_slot_from_text(
    text: str,
    available_slots: List[str],
    reference_date: Optional[str] = None,
) -> Optional[str]:
    """
    Extrae hora del texto y la busca en available_slots.

    available_slots = lista de ISO strings como "2026-04-10T10:00:00"
    reference_date  = fecha ISO "2026-04-11" para construir el slot cuando
                      available_slots está vacío (flujo reagendar).

    Prioridad 1: formato "10:30" (con colon)
    Prioridad 2: formato "a las 10" / "las 10" (sin colon)
    Si available_slots está vacío y reference_date está dado, construye
    el ISO directamente sin necesidad de matchear contra una lista.
    """
    text_lower = text.lower()
    is_pm = any(w in text_lower for w in ("tarde", "noche", "pm"))

    def _apply_pm(h: int) -> int:
        return h + 12 if is_pm and 1 <= h <= 11 else h

    def _find_in_slots(h: int, m: int = 0) -> Optional[str]:
        """
        Busca el slot con hora h:m en available_slots.
        Si no encuentra y h < 9 (antes del horario laboral), intenta h+12 (PM).
        Esto resuelve "a las 4" → intenta T04 → no encontrado → intenta T16.
        """
        candidate = f"T{h:02d}:{m:02d}"
        for slot in available_slots:
            if candidate in slot:
                return slot
        # Fallback PM: si la hora no tiene contexto y está fuera del horario (< 9)
        if h < 9 and not is_pm:
            h_pm = h + 12
            candidate_pm = f"T{h_pm:02d}:{m:02d}"
            for slot in available_slots:
                if candidate_pm in slot:
                    return slot
        return None

    # ── Prioridad 1: "10:30" ────────────────────────────────
    times = re.findall(r"(\d{1,2}):(\d{2})", text)
    if times:
        h, m = _apply_pm(int(times[0][0])), int(times[0][1])
        found = _find_in_slots(h, m)
        if found:
            return found
        if reference_date:
            return f"{reference_date}T{h:02d}:{m:02d}:00"

    # ── Prioridad 2: "a las 10" / "las 10" ─────────────────
    hour_matches = re.findall(r"(?:a las|las)\s+(\d{1,2})", text_lower)
    if hour_matches:
        h = _apply_pm(int(hour_matches[0]))
        if 0 <= h <= 23:
            found = _find_in_slots(h)
            if found:
                return found
            if reference_date:
                return f"{reference_date}T{h:02d}:00:00"

    # ── Prioridad 3: "10 de la mañana" / "10 de la tarde" ──
    # Cubre el caso donde el usuario dice la hora ANTES de "de la mañana/tarde/noche"
    morning_matches = re.findall(
        r"(\d{1,2})\s+de\s+la\s+(?:mañana|manana|tarde|noche)", text_lower
    )
    if morning_matches:
        h = _apply_pm(int(morning_matches[0]))
        if 0 <= h <= 23:
            found = _find_in_slots(h)
            if found:
                return found
            if reference_date:
                return f"{reference_date}T{h:02d}:00:00"

    return None
