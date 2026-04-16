"""Utilidades compartidas para el agente."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Guayaquil")


def adjust_to_next_business_day(dt: datetime) -> tuple[datetime, bool]:
    """
    Si dt cae en fin de semana (sábado=5, domingo=6), avanza al lunes.
    Retorna (fecha_ajustada, fue_ajustado).
    """
    weekday = dt.weekday()
    if weekday == 5:  # sábado
        return dt + timedelta(days=2), True
    if weekday == 6:  # domingo
        return dt + timedelta(days=1), True
    return dt, False
