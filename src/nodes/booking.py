"""booking — módulo de funciones específicas."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import structlog
from src.state import ArcadiumState
from src.nodes_backup import _resolve_calendar_service, _last_human_text

logger = structlog.get_logger("langgraph.nodes")

async def node_book_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
    db_service=None,
    store=None,
) -> Dict[str, Any]:
    """
    Agenda en Google Calendar y DB.
    DETERMINISTA — cero llamadas al LLM.

    INVARIANTE CRÍTICO: NUNCA retorna confirmation_sent=True si google_event_id es None.
    Si no hay evento en Calendar, retorna error. El LLM NO debe confirmar citas falsas.
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    logger.info(
        "[node_book_appointment] iniciando",
        phone=state.get("phone_number", ""),
        service=state.get("selected_service", ""),
        slot=state.get("selected_slot") or state.get("datetime_preference", ""),
        doctor_email=state.get("doctor_email", ""),
        has_calendar_service=calendar_service is not None,
    )

    slot = state.get("selected_slot") or state.get("datetime_preference")
    if not slot:
        logger.error("[node_book_appointment] sin slot para agendar")
        return {"last_error": "No hay slot seleccionado para agendar"}

    # GUARD: calendar_service es OBLIGATORIO — sin él no hay cita real
    if not calendar_service:
        logger.error("[node_book_appointment] calendar_service no disponible — abortando")
        return {
            "last_error": (
                "El servicio de Google Calendar no está disponible en este momento. "
                "Por favor llame a la clínica directamente. 📞"
            ),
            "should_escalate": False,
        }

    try:
        dt = datetime.fromisoformat(slot)
        duration = state.get("service_duration", 60)
        end_dt = dt + timedelta(minutes=duration)

        patient = state.get("patient_name", "Paciente")
        service = state.get("selected_service", "consulta")

        # Si hay cita existente, cancelarla primero (reagendamiento sin ir por prepare_modification)
        old_event_id = state.get("google_event_id")
        old_appt_id = state.get("appointment_id")
        if old_event_id:
            try:
                if calendar_service:
                    await calendar_service.delete_event(old_event_id)
                    logger.info("[node_book_appointment] cita anterior cancelada", event_id=old_event_id)
                if db_service and old_appt_id:
                    await db_service.cancel_appointment(session=None, appointment_id=uuid.UUID(old_appt_id))
            except Exception as e:
                logger.warning("[node_book_appointment] error cancelando cita previa (continuando)", error=str(e))

        # Crear en Google Calendar
        logger.info(
            "[node_book_appointment] llamando create_event",
            patient=patient,
            service=service,
            start=dt.isoformat(),
            end=end_dt.isoformat(),
        )
        # FIX: create_event retorna tuple[str, str] (event_id, html_link).
        # Kwargs correctos: start/end (no start_time/end_time).
        event_id, event_link = await calendar_service.create_event(
            start=dt,
            end=end_dt,
            title=f"{service} - {patient}",
            description=f"Paciente: {patient}\nTeléfono: {state.get('phone_number', '')}",
        )

        # GUARD: verificar que el evento fue realmente creado
        if not event_id:
            logger.error(
                "[node_book_appointment] create_event no devolvió ID — fallo silencioso en Calendar",
            )
            return {
                "last_error": "Error confirmando la cita en Google Calendar (sin ID). Por favor llame a la clínica. 📞",
                "should_escalate": True,
            }

        logger.info(
            "[node_book_appointment] evento creado EXITOSAMENTE en Google Calendar",
            event_id=event_id,
            event_link=event_link,
            patient=patient,
            service=service,
            slot=slot,
        )

        # Crear en DB (opcional — no bloquea el flujo)
        appt_id = None
        if db_service:
            try:
                from db import get_async_session
                async with get_async_session() as session:
                    success, msg, appt = await db_service.create_appointment(
                        session=session,
                        phone_number=state.get("phone_number", ""),
                        appointment_datetime=dt,
                        service_type=service,
                        project_id=state.get("project_id"),
                        metadata={"google_event_id": event_id, "patient_name": patient},
                    )
                    if appt:
                        appt_id = str(appt.id)
            except Exception as e:
                logger.warning("[node_book_appointment] error creando cita en DB (no crítico)", error=str(e))

        # Step 5: Guardar preferencias de paciente (hora preferida)
        try:
            booked_dt = datetime.fromisoformat(slot)
            pref_update = {
                "preferred_hour": booked_dt.hour,
                "preferred_day_of_week": booked_dt.weekday(),
                "preferred_doctor": state.get("doctor_email", "unknown"),
            }
            phone = state.get("phone_number")
            if phone and store and hasattr(store, "upsert_user_profile"):
                await store.upsert_user_profile(
                    phone,
                    {"preferences": pref_update}
                )
                logger.info("Preferencias guardadas", phone=phone, prefs=pref_update)
        except Exception as e:
            logger.warning("Error guardando preferencias (no crítico)", error=str(e))

        return {
            # Usar event_id como fallback para appointment_id si no hay DB
            "appointment_id": appt_id or f"gcal_{event_id}",
            "google_event_id": event_id,
            "google_event_link": event_link,
            # confirmation_sent=True SOLO cuando google_event_id está confirmado
            "confirmation_sent": True,
            "current_step": "resolution",
            # Registrar el slot que se agendó (para contexto del LLM en respuesta)
            "selected_slot": slot,
            # Indicar que hay una cita (la que acaba de crearse)
            "has_appointment": True,
            # Limpiar estado de selección para no reutilizar en próximos turnos
            "awaiting_confirmation": False,
            "available_slots": [],
            "confirmation_type": None,
            # Limpiar errores previos — la cita se creó exitosamente
            "last_error": None,
            "should_escalate": False,
        }

    except Exception as e:
        logger.error(
            "[node_book_appointment] excepción al agendar",
            error=str(e),
            phone=state.get("phone_number", ""),
        )
        return {
            "last_error": f"Error agendando cita: {e}",
            "should_escalate": True,
        }


async def node_detect_confirmation(state: ArcadiumState) -> Dict[str, Any]:
    """
    Detecta si el usuario confirmó, rechazó, o eligió un slot.
    Sin LLM — regex y keywords.

    Overrides contextuales:
    - Cancelar: palabras de intención ("cancela", "cancelo", "anula") se interpretan
      como "unknown" (no como "no"), para que generate_response pida confirmación
      explícita. Sin esto, "cancela mi cita" devuelve "no" y el flujo se rompe.
    - Reagendar sin available_slots: construye el ISO directamente desde la hora
      parseada + la fecha de referencia del estado (mañana).
    - Agendar: "a las N" y "N de la mañana/tarde" ya manejados por extract_slot_from_text.
    """
    from src.intent_router import detect_confirmation, extract_slot_from_text

    text = _last_human_text(state)
    ctype = state.get("confirmation_type")
    result = detect_confirmation(text)

    # ── Override para cancelar ───────────────────────────────────────────────
    # "cancela mi cita" devuelve "no" porque "cancela" está en CONFIRM_NO.
    # Pero en el flujo de cancelación (awaiting_confirmation=True), esas palabras
    # expresan CONFIRMACIÓN de la operación ya anunciada, no rechazo.
    # → tratarlas como "yes" para ejecutar la cancelación.
    # Si awaiting_confirmation=False, son una nueva intención → "unknown".
    if ctype == "cancel" and result == "no":
        intent_cancel_words = ["cancela", "cancelo", "anula", "anulo", "desagendar"]
        text_lower = text.lower()
        if any(kw in text_lower for kw in intent_cancel_words):
            awaiting = state.get("awaiting_confirmation", False)
            result = "yes" if awaiting else "unknown"

    # ── Extracción de slot ───────────────────────────────────────────────────
    available_slots = state.get("available_slots", [])
    selected_slot = None

    if result == "slot_choice":
        # Para reagendar sin slots cargados: construir ISO desde fecha de referencia.
        reference_date = None
        if not available_slots and ctype == "reschedule":
            reference_date = state.get("manana_fecha") or state.get("fecha_hoy")
        selected_slot = extract_slot_from_text(text, available_slots, reference_date)

    elif result == "yes" and available_slots and ctype == "book":
        # Usuario confirmó genéricamente ("sí") sin elegir slot específico.
        # Elegir el slot disponible más cercano a datetime_preference.
        dt_pref = state.get("datetime_preference")
        if dt_pref:
            from utils.date_utils import normalize_iso_datetime
            pref_dt = normalize_iso_datetime(dt_pref)
            if pref_dt:
                def _slot_distance(s: str) -> float:
                    slot_dt = normalize_iso_datetime(s)
                    if slot_dt is None:
                        return float("inf")
                    # Comparar horas en naive para evitar problemas de tz
                    pref_mins = pref_dt.hour * 60 + pref_dt.minute
                    slot_mins = slot_dt.hour * 60 + slot_dt.minute
                    return abs(slot_mins - pref_mins)
                selected_slot = min(available_slots, key=_slot_distance)

    return {
        "confirmation_result": result,
        "selected_slot": selected_slot or state.get("selected_slot"),
        "current_step": "confirmation_detected",
    }


async def node_validate_and_confirm(state: ArcadiumState) -> Dict[str, Any]:
    """
    Valida que hay un slot elegido. No requiere available_slots en estado
    (puede estar vacío si llegamos desde un turno posterior donde los slots
    ya se limpiaron; la validación real ocurrió en extract_slot_from_text).
    """
    selected = state.get("selected_slot")

    if selected:
        return {
            "awaiting_confirmation": True,
            "confirmation_type": "book",
            "current_step": "awaiting_final_confirmation",
        }

    return {
        "last_error": "No se identificó el slot seleccionado. ¿Puede indicar la hora exacta?",
        "should_escalate": False,
    }

