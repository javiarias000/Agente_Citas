# -*- coding: utf-8 -*-
"""
Utilidades de manejo de fechas y tiempo para Arcadium Automation.
"""

from datetime import datetime
from typing import Optional, Tuple
import structlog

logger = structlog.get_logger("utils.date_utils")

def normalize_iso_datetime(dt_str: str) -> Optional[datetime]:
    """
    Normaliza un string ISO 8601 a un objeto datetime.
    Soporta formatos con 'Z', offsets (+00:00) y strings simples.
    """
    if not dt_str:
        return None
    try:
        # Manejar el caso común de 'Z' que fromisoformat no siempre procesa bien en todas las versiones
        normalized = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception as e:
        logger.debug(f"Error normalizando datetime {dt_str}: {e}")
        return None

def compare_slots(pref_str: str, slot_str: str) -> bool:
    """
    Compara si una preferencia de fecha/hora coincide exactamente con un slot.
    Ignora milisegundos y se enfoca en Año, Mes, Día, Hora y Minuto.
    """
    pref_dt = normalize_iso_datetime(pref_str)
    slot_dt = normalize_iso_datetime(slot_str)

    if not pref_dt or not slot_dt:
        return False

    return (
        pref_dt.year == slot_dt.year and
        pref_dt.month == slot_dt.month and
        pref_dt.day == slot_dt.day and
        pref_dt.hour == slot_dt.hour and
        pref_dt.minute == slot_dt.minute
    )
