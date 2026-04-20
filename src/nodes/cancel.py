"""cancel — módulo de funciones específicas."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict
import structlog
from src.state import ArcadiumState, TIMEZONE
from src.nodes_backup import _resolve_calendar_service

logger = structlog.get_logger("langgraph.nodes")

async def node_cancel_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
    db_service=None,
) -> Dict[str, Any]:
    """
    Cancela cita en Google Calendar y DB.
    Step 6: Si cancela, ofrece slots alternativos para reagendar.
    DETERMINISTA — cero LLM.
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    event_id = state.get("google_event_id")
    appt_id = state.get("appointment_id")
    duration = state.get("service_duration", 60)

    try:
        if calendar_service and event_id:
            await calendar_service.delete_event(event_id)

        if db_service and appt_id:
            try:
                import uuid as _uuid

                await db_service.cancel_appointment(
                    session=None,
                    appointment_id=_uuid.UUID(appt_id),
                )
            except Exception as e:
                logger.warning("Error cancelando en DB", error=str(e))

        # Step 6: Buscar slots alternativos en próximos 3 días
        alternative_slots = []
        try:
            now_ec = datetime.now(TIMEZONE)
            for day_offset in [1, 2, 3]:
                next_day = now_ec + timedelta(days=day_offset)
                # Saltar fines de semana
                if next_day.weekday() >= 5:
                    continue

                next_day_midnight = next_day.replace(hour=0, minute=0, second=0, microsecond=0)
                slots = await calendar_service.get_available_slots(
                    date=next_day_midnight,
                    duration_minutes=duration,
                )

                if slots:
                    # Tomar primer slot disponible del día
                    s = slots[0]
                    slot_iso = None
                    if isinstance(s, dict):
                        start = s.get("start")
                        slot_iso = start.isoformat() if isinstance(start, datetime) else str(start)
                    else:
                        slot_iso = str(s)

                    if slot_iso:
                        alternative_slots.append(slot_iso)

                if len(alternative_slots) >= 3:
                    break

        except Exception as e:
            logger.warning("Error buscando slots alternativos (no crítico)", error=str(e))

        return {
            "current_step": "resolution",
            "confirmation_sent": True,
            # Limpiar estado de flujo
            "confirmation_type": "cancel_and_rebook" if alternative_slots else None,
            "awaiting_confirmation": bool(alternative_slots),
            "rebook_after_cancel": bool(alternative_slots),
            # Retornar slots alternativos
            "available_slots": alternative_slots,
            # Limpiar IDs para que el LLM no confunda con una reserva activa
            "appointment_id": None,
            "google_event_id": None,
            "google_event_link": None,
            # Limpiar citas existentes para que el LLM vea que no hay citas
            "existing_appointments": [],
            # Indicar que no hay citas después de cancelar
            "has_appointment": False,
        }

    except Exception as e:
        return {
            "last_error": f"Error cancelando cita: {e}",
        }

