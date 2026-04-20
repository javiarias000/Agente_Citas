"""reschedule — módulo de funciones específicas."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict
import structlog
from zoneinfo import ZoneInfo
from src.state import ArcadiumState
from src.nodes_backup import _resolve_calendar_service

logger = structlog.get_logger("langgraph.nodes")

async def node_reschedule_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
    db_service=None,
) -> Dict[str, Any]:
    """
    Reagenda una cita: cancela el evento anterior y crea uno nuevo.
    DETERMINISTA — cero llamadas al LLM.
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    new_slot = state.get("selected_slot") or state.get("datetime_preference")
    if not new_slot:
        return {"last_error": "No hay nuevo slot para reagendar"}

    old_event_id = state.get("google_event_id")
    old_appt_id = state.get("appointment_id")

    try:
        dt = datetime.fromisoformat(new_slot)
        duration = state.get("service_duration", 60)
        end_dt = dt + timedelta(minutes=duration)
        patient = state.get("patient_name", "Paciente")
        service = state.get("selected_service", "consulta")

        # 1. Crear nuevo evento en Google Calendar PRIMERO (R1 — Create-before-Delete).
        # Si falla la creación, el evento viejo sigue intacto. No se pierde la cita.
        new_event_id = None
        new_event_link = None
        if calendar_service:
            new_event_id, new_event_link = await calendar_service.create_event(
                start=dt,
                end=end_dt,
                title=f"{service} - {patient}",
                description=f"Paciente: {patient}\nTeléfono: {state.get('phone_number', '')}",
            )

        if not new_event_id:
            return {
                "last_error": "Error creando nuevo evento en Calendar. La cita anterior sigue vigente.",
                "should_escalate": True,
            }

        logger.info("Nuevo evento creado antes de eliminar viejo", new_event_id=new_event_id)

        # 2. Cancelar evento anterior en Google Calendar (solo si nuevo existe)
        # Además, cancelar TODOS los eventos futuros del paciente del mismo servicio
        # para limpiar múltiples citas huérfanas (ej. 10:00, 11:00, 12:00)
        if calendar_service:
            if old_event_id:
                try:
                    await calendar_service.delete_event(old_event_id)
                    logger.info("Evento anterior eliminado", event_id=old_event_id)
                except Exception as e:
                    logger.warning(
                        "Error eliminando evento anterior (nuevo evento OK)",
                        old_event_id=old_event_id,
                        new_event_id=new_event_id,
                        error=str(e),
                    )

            # Limpiar citas huérfanas: buscar TODOS los eventos del paciente del mismo servicio
            # en los próximos 7 días y cancelar aquellos que NO sean el nuevo
            try:
                phone = state.get("phone_number", "")
                if phone:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo("America/Guayaquil")
                    now = datetime.now(tz)
                    future = now + timedelta(days=7)

                    orphaned = await calendar_service.search_events_by_query(
                        q=phone, start_date=now, end_date=future
                    )
                    for ev in orphaned:
                        ev_id = ev.get("id")
                        # No cancelar el evento nuevo que acaba de crearse
                        if ev_id and ev_id != new_event_id:
                            # Verificar que sea del mismo servicio (en el summary)
                            summary = ev.get("summary", "").lower()
                            if service.lower() in summary:
                                try:
                                    await calendar_service.delete_event(ev_id)
                                    logger.info("Cita huérfana cancelada", event_id=ev_id)
                                except Exception as e:
                                    logger.warning("Error cancelando cita huérfana", event_id=ev_id, error=str(e))
            except Exception as e:
                logger.warning("Error limpiando citas huérfanas (no crítico)", error=str(e))

        # 3. Cancelar cita anterior en DB
        if db_service and old_appt_id:
            try:
                import uuid as _uuid
                await db_service.cancel_appointment(
                    session=None,
                    appointment_id=_uuid.UUID(old_appt_id),
                )
            except Exception as e:
                logger.warning("Error cancelando cita anterior en DB", error=str(e))

        # 4. Crear nueva cita en DB
        new_appt_id = None
        if db_service:
            try:
                from db import get_async_session
                async with get_async_session() as session:
                    _, __, appt = await db_service.create_appointment(
                        session=session,
                        phone_number=state.get("phone_number", ""),
                        appointment_datetime=dt,
                        service_type=service,
                        project_id=state.get("project_id"),
                        metadata={"google_event_id": new_event_id, "patient_name": patient},
                    )
                    if appt:
                        new_appt_id = str(appt.id)
            except Exception as e:
                logger.warning("Error creando nueva cita en DB", error=str(e))

        logger.info(
            "Cita reagendada",
            patient=patient,
            service=service,
            new_slot=new_slot,
            old_event_id=old_event_id,
            new_event_id=new_event_id,
        )

        return {
            "appointment_id": new_appt_id or "pending_db",
            "google_event_id": new_event_id,
            "google_event_link": new_event_link,
            "confirmation_sent": True,
            "current_step": "resolution",
            # Limpiar estado de selección.
            # IMPORTANTE: confirmation_type se mantiene como "reschedule" para que
            # node_generate_response_with_tools identifique correctamente el mensaje de éxito
            # ("Su cita ha sido reagendada" en vez de "agendada").
            # Se limpiará en la siguiente sesión cuando awaiting_confirmation=False.
            "awaiting_confirmation": False,
            "available_slots": [],
        }

    except Exception as e:
        return {
            "last_error": f"Error reagendando cita: {e}",
            "should_escalate": True,
        }


async def node_prepare_modification(state: ArcadiumState) -> Dict[str, Any]:
    """
    Nodo determinista que prepara el estado para flujos de cancelación/reagendamiento.
    Se ejecuta cuando el intent es "cancelar" o "reagendar".

    IMPORTANTE: SIEMPRE actualiza confirmation_type según el intent actual,
    incluso si hay un valor anterior seteado. Esto previene que confirmation_type
    de una conversación anterior contamine la nueva (ej. anterior intent="cancelar"
    dejó confirmation_type="cancel", nueva intent="reagendar" debe actualizar a "reschedule").
    """
    intent = state.get("intent")
    if intent == "cancelar":
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "cancel",
            "current_step": "awaiting_cancel_confirmation",
        }
    elif intent == "reagendar":
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "reschedule",
            "current_step": "awaiting_reschedule_details",
        }
    return {}

