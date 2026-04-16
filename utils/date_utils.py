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


def find_closest_slot(pref_str: str, available_slots: list, max_delta_minutes: int = 60) -> Optional[str]:
    """
    Busca el slot más cercano a una preferencia de fecha/hora.

    Args:
        pref_str: ISO datetime string de la preferencia del usuario
        available_slots: lista de ISO datetime strings
        max_delta_minutes: máxima diferencia en minutos permitida (default 60)

    Returns:
        El slot más cercano si está dentro de max_delta_minutes, sino None
    """
    pref_dt = normalize_iso_datetime(pref_str)
    if not pref_dt or not available_slots:
        return None

    closest_slot = None
    closest_delta = float("inf")

    for slot_str in available_slots:
        slot_dt = normalize_iso_datetime(slot_str)
        if not slot_dt:
            continue

        # Normalizar ambos a UTC para comparación consistente
        pref_utc = pref_dt.astimezone(datetime.now().astimezone().tzinfo) if pref_dt.tzinfo else pref_dt
        slot_utc = slot_dt.astimezone(datetime.now().astimezone().tzinfo) if slot_dt.tzinfo else slot_dt

        # Si uno tiene tzinfo y otro no, convertir ambos a naive para comparar solo hora/minuto
        try:
            if pref_dt.tzinfo and not slot_dt.tzinfo:
                # Comparar solo hora y minuto
                delta = abs((slot_dt.hour * 60 + slot_dt.minute) - (pref_dt.hour * 60 + pref_dt.minute))
            elif not pref_dt.tzinfo and slot_dt.tzinfo:
                # Comparar solo hora y minuto
                delta = abs((slot_dt.hour * 60 + slot_dt.minute) - (pref_dt.hour * 60 + pref_dt.minute))
            else:
                # Ambos con tzinfo o ambos sin
                delta = abs((slot_dt - pref_dt).total_seconds() / 60)
        except TypeError:
            # Fallback: comparar solo hora y minuto
            delta = abs((slot_dt.hour * 60 + slot_dt.minute) - (pref_dt.hour * 60 + pref_dt.minute))

        if delta < closest_delta:
            closest_delta = delta
            closest_slot = slot_str

    if closest_slot and closest_delta <= max_delta_minutes:
        return closest_slot

    return None
