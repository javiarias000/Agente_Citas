"""availability — nodo de disponibilidad y búsqueda de citas."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import structlog

from zoneinfo import ZoneInfo

from src.state import ArcadiumState, TIMEZONE, get_missing_fields, is_weekend_adjusted
from src.nodes_backup import (
    _resolve_calendar_service,
    _parse_event_start,
    _event_to_dict,
    _extract_patient_name_from_description,
    _compute_slots_available,
    _phone_in_text,
    _name_in_text,
    _service_in_text,
    _no_appointment_found,
)

logger = structlog.get_logger("langgraph.nodes.availability")


async def node_check_availability(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
) -> Dict[str, Any]:
    """
    Consulta slots disponibles vía Google Calendar.
    Convierte los dicts de slot a ISO strings para que sean serializables
    y compatibles con extract_slot_from_text.
    Sin LLM.
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    dt_iso = state.get("datetime_preference")
    duration = state.get("service_duration", 60)

    if not dt_iso or not calendar_service:
        return {
            "available_slots": [],
            "last_error": "No hay fecha para consultar disponibilidad"
            if not dt_iso
            else "Calendar service no disponible",
        }

    try:
        dt = datetime.fromisoformat(dt_iso)
        # Ajustar fin de semana si no se hizo ya
        if dt.weekday() >= 5:
            days = 7 - dt.weekday()
            dt = dt + timedelta(days=days)

        # Pasar SOLO la fecha (midnight) — get_available_slots busca slots para todo el día.
        # La hora específica en datetime_preference se usa después para recomendar el slot más cercano.
        dt_date = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        slots = await calendar_service.get_available_slots(
            date=dt_date,
            duration_minutes=duration,
        )

        logger.info(
            "node_check_availability: raw slots del calendar",
            date=dt.date().isoformat(),
            slots_count=len(slots),
            first_slot=str(slots[0]) if slots else "ninguno",
            service_duration=duration,
            doctor_email=state.get("doctor_email", "unknown"),
        )

        # Hora actual en Ecuador para filtrar slots ya pasados
        now_ec = datetime.now(TIMEZONE)

        # Normalizar a ISO strings para facilitar comparación y serialización,
        # filtrando slots que ya pasaron (solo relevante cuando la fecha es hoy).
        slots_iso = []
        for s in slots:
            if isinstance(s, dict):
                start = s.get("start")
                if isinstance(start, datetime):
                    slot_iso = start.isoformat()
                    slot_dt = start
                else:
                    slot_iso = str(start)
                    try:
                        slot_dt = datetime.fromisoformat(str(start))
                    except ValueError:
                        slot_dt = None
            else:
                slot_iso = str(s)
                try:
                    slot_dt = datetime.fromisoformat(str(s))
                except ValueError:
                    slot_dt = None

            # Filtrar slots pasados: si el slot no tiene timezone, lo comparamos
            # como naive (asumiendo Ecuador local). Si tiene timezone, comparamos aware.
            if slot_dt is not None:
                if slot_dt.tzinfo is not None:
                    if slot_dt <= now_ec:
                        continue  # slot ya pasó
                else:
                    if slot_dt <= now_ec.replace(tzinfo=None):
                        continue  # slot ya pasó (naive comparison)

            slots_iso.append(slot_iso)

        if not slots_iso:
            # Fallback: intentar próximos 5 días hábiles
            for days_ahead in range(1, 6):
                try:
                    next_dt = dt + timedelta(days=days_ahead)
                    # Saltar fines de semana
                    if next_dt.weekday() >= 5:
                        days_to_add = 7 - next_dt.weekday()
                        next_dt = next_dt + timedelta(days=days_to_add)

                    next_dt_date = next_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    next_slots = await calendar_service.get_available_slots(
                        date=next_dt_date,
                        duration_minutes=duration,
                    )

                    next_slots_iso = []
                    for s in next_slots:
                        if isinstance(s, dict):
                            start = s.get("start")
                            slot_iso = start.isoformat() if isinstance(start, datetime) else str(start)
                        else:
                            slot_iso = str(s)
                        try:
                            slot_dt = datetime.fromisoformat(str(slot_iso))
                            if slot_dt.tzinfo is not None:
                                if slot_dt <= now_ec:
                                    continue
                            else:
                                if slot_dt <= now_ec.replace(tzinfo=None):
                                    continue
                        except:
                            pass
                        next_slots_iso.append(slot_iso)

                    if next_slots_iso:
                        logger.info(
                            "node_check_availability: fallback encontró slots",
                            original_date=dt.date().isoformat(),
                            fallback_date=next_dt.date().isoformat(),
                            days_ahead=days_ahead,
                            slots_found=len(next_slots_iso),
                        )
                        return {
                            "available_slots": next_slots_iso,
                            "_slots_checked": True,
                            "datetime_preference": next_dt_date.isoformat(),
                            "current_step": "awaiting_selection",
                            "awaiting_confirmation": True,
                            "confirmation_type": "book",
                            "last_error": f"Sin disponibilidad en {dt.strftime('%d/%m')}. Opciones disponibles el {next_dt.strftime('%d/%m')}.",
                        }
                except Exception as fallback_err:
                    logger.warning(
                        "node_check_availability: fallback intento falló",
                        days_ahead=days_ahead,
                        error=str(fallback_err),
                    )
                    continue

            # Sin slots en próximos 5 días
            return {
                "available_slots": [],
                "_slots_checked": True,
                "last_error": "Sin disponibilidad en próximos días. Por favor intente otra fecha o llame a la clínica.",
            }

        # Ordenar slots por preferencia de hora del paciente (Step 5)
        prefs = state.get("patient_preferences", {})
        if prefs.get("preferred_hour"):
            try:
                preferred_hour = int(prefs["preferred_hour"])
                slots_iso.sort(
                    key=lambda s: abs(
                        datetime.fromisoformat(s).hour - preferred_hour
                    )
                    if s else 1000
                )
            except Exception:
                pass  # Si hay error, mantener orden original

        logger.info(
            "node_check_availability: slots filtrados",
            date=dt.date().isoformat(),
            total_from_calendar=len(slots),
            future_slots=len(slots_iso),
            now_ec=now_ec.strftime("%H:%M"),
        )

        return {
            "available_slots": slots_iso,
            "_slots_checked": True,  # check_availability corrió; hay slots disponibles
            "current_step": "awaiting_selection",
            # Marcar que esperamos selección → el siguiente turno va a detect_confirmation
            "awaiting_confirmation": True,
            "confirmation_type": "book",
        }
    except Exception as e:
        return {
            "available_slots": [],
            "_slots_checked": True,  # corrió pero falló
            "last_error": f"Error consultando disponibilidad: {e}",
        }



async def node_match_closest_slot(state: ArcadiumState) -> Dict[str, Any]:
    """
    Después de check_availability: si no hay match exacto, buscar closest slot.

    Si datetime_preference no coincide exactamente con ningún slot disponible,
    pero hay un slot dentro de 60 minutos, setear selected_slot = closest_slot
    para que se intente el booking automático.

    Solo hace matching para intent "agendar". Para "consultar", solo lista slots.
    Sin LLM. Determinista.
    """
    from utils.date_utils import compare_slots, find_closest_slot

    logger.info("NODE_MATCH_CLOSEST_SLOT: Iniciando nodo")

    available_slots = state.get("available_slots", [])
    datetime_pref = state.get("datetime_preference")
    intent = state.get("intent")

    # Solo SALTEAR matching para intent "consultar" (check availability sin booking)
    # Para "agendar", None, o cualquier otro intent, hacer matching automático
    if intent == "consultar":
        logger.info("NODE_MATCH_CLOSEST_SLOT: intent es consultar, no seleccionar slot")
        return {}

    if not datetime_pref or not available_slots:
        # Sin preferencia o sin slots, no hay nada que hacer
        return {}

    # Buscar match exacto primero
    for s in available_slots:
        if compare_slots(datetime_pref, s):
            # Match exacto encontrado
            logger.info(
                "node_match_closest_slot: match exacto",
                pref=datetime_pref,
                slot=s,
            )
            return {"selected_slot": s}

    # No hay match exacto, buscar closest dentro de 60 minutos
    closest = find_closest_slot(datetime_pref, available_slots, max_delta_minutes=60)

    if closest:
        logger.info(
            "node_match_closest_slot: closest slot encontrado",
            pref=datetime_pref,
            closest=closest,
        )
        return {
            "selected_slot": closest,
            "preference_adjusted": True,
        }

    # Si intent es "agendar" y no hay slot dentro de 60 min, aceptar el más cercano
    # Para otros intents, mantener strict 60-minute limit
    if intent == "agendar":
        closest_any = find_closest_slot(datetime_pref, available_slots, max_delta_minutes=None)
        if closest_any:
            logger.info(
                "node_match_closest_slot: closest slot sin tiempo límite (intent=agendar)",
                pref=datetime_pref,
                closest=closest_any,
            )
            return {
                "selected_slot": closest_any,
                "preference_adjusted": True,
            }

    logger.info(
        "node_match_closest_slot: sin closest slot en rango",
        pref=datetime_pref,
        slots_count=len(available_slots),
    )
    return {}



async def node_check_missing(state: ArcadiumState) -> Dict[str, Any]:
    """
    Evalúa qué campos obligatorios faltan.
    Determina el siguiente paso sin llamadas externas.
    """
    missing = get_missing_fields(state)
    return {
        "missing_fields": missing,
        "current_step": "missing_checked",
    }



async def node_adjust_weekend(state: ArcadiumState) -> Dict[str, Any]:
    """
    Si datetime_preference cae en fin de semana, ajusta al lunes.
    Determinista puro.
    """
    dt_iso = state.get("datetime_preference")
    if not dt_iso:
        return {}

    adjusted, new_iso = is_weekend_adjusted(dt_iso)
    if adjusted:
        logger.info(
            "Fecha ajustada fin de semana → lunes", original=dt_iso, adjusted=new_iso
        )
        return {
            "datetime_preference": new_iso,
            "datetime_adjusted": True,
        }
    return {"datetime_adjusted": False}



async def node_check_existing_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
) -> Dict[str, Any]:
    """
    Forcing tool — SIEMPRE se ejecuta cuando el intent es agendar, cancelar o reagendar.

    Estrategia de búsqueda (DOS capas):
      1. list_events en los próximos 60 días → filtrar localmente por teléfono
      2. search_events_by_query con el nombre del paciente → resultados de la API de Google

    Filtros aplicados (sin falsos positivos):
      - PACIENTE: phone en description  OR  patient_name en summary/description
      - SERVICIO: si selected_service está en el estado, refinar la lista con ese servicio
      - DÍA: si datetime_preference está en el estado, filtrar para el día exacto
        (solo en el resultado "servicio+día", no en la búsqueda amplia de "cancelar")

    Retorna:
      calendar_appointment_found  → True SOLO si hay coincidencia real por paciente
      existing_appointments       → lista de citas del paciente (máx 3)
      calendar_total_for_patient  → total de citas encontradas del paciente
      calendar_slots_available    → slots libres aproximados en el día solicitado (si hay fecha)
      calendar_first_match        → primer evento que coincide con paciente+servicio+día
      google_event_id / link      → del evento más reciente del paciente (para cancel/reschedule)

    DETERMINISTA — cero LLM.
    """
    # ── Guard: sin calendar_service ──────────────────────────────────────────
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    if not calendar_service:
        logger.warning("node_check_existing_appointment: sin calendar_service")
        return _no_appointment_found()

    phone = state.get("phone_number", "")
    patient_name = (state.get("patient_name") or "").strip()
    service = (state.get("selected_service") or "").strip().lower()
    dt_pref = state.get("datetime_preference")
    intent = state.get("intent", "")

    # ── Guard: sin ningún identificador del paciente ─────────────────────────
    if not phone and not patient_name:
        logger.warning("node_check_existing_appointment: sin teléfono ni nombre")
        return _no_appointment_found()

    try:
        tz = ZoneInfo("America/Guayaquil")
        now = datetime.now(tz)
        future = now + timedelta(days=60)

        # ── Resolver el día de la cita solicitada (si aplica) ─────────────────
        # Se usa para: (a) filtrar eventos del mismo día, (b) calcular slots libres.
        requested_day: Optional[datetime] = None
        if dt_pref:
            try:
                dt_parsed = datetime.fromisoformat(dt_pref)
                requested_day = dt_parsed.replace(tzinfo=tz) if dt_parsed.tzinfo is None else dt_parsed
            except ValueError:
                pass  # dt_pref con formato inválido — se ignora sin romper el flujo

        # ── Estrategia 1: búsqueda por teléfono vía search_events_by_query ────
        # list_events no existe en el wrapper — search_events_by_query hace búsqueda
        # de texto libre que encuentra el teléfono almacenado en la descripción del evento.
        all_events: list = []
        found_by_phone: list = []
        if phone:
            try:
                # Buscar con el número normalizado y sin "+" para mayor cobertura
                phone_results = await calendar_service.search_events_by_query(
                    q=phone,
                    start_date=now,
                    end_date=future,
                )
                # También buscar sin el prefijo "+" por si fue guardado así
                phone_nplus = phone.lstrip("+")
                if phone_nplus != phone:
                    phone_results2 = await calendar_service.search_events_by_query(
                        q=phone_nplus,
                        start_date=now,
                        end_date=future,
                    )
                    # Deduplicar
                    seen = {ev.get("id") for ev in phone_results}
                    phone_results += [ev for ev in phone_results2 if ev.get("id") not in seen]
                found_by_phone = phone_results
            except Exception as e:
                logger.warning("node_check_existing_appointment: error búsqueda por teléfono", error=str(e))

        # ── Estrategia 2: búsqueda por nombre vía API de Google (q=) ─────────
        # Solo si hay nombre con suficientes caracteres para evitar resultados ruido.
        found_by_name: list[Dict[str, Any]] = []
        if patient_name and len(patient_name) >= 3:
            found_by_name = await calendar_service.search_events_by_query(
                q=patient_name,
                start_date=now,
                end_date=future,
            )

        # ── Combinar y deduplicar por event_id ───────────────────────────────
        seen_ids: set[str] = set()
        patient_events: list[Dict[str, Any]] = []
        for ev in found_by_phone + found_by_name:
            eid = ev.get("id")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                patient_events.append(ev)

        # ── Filtrar eventos pasados ───────────────────────────────────────────
        # Google Calendar API con q= ignora timeMin/timeMax en búsquedas de texto.
        # Filtramos manualmente para que solo queden citas futuras.
        future_events: list[Dict[str, Any]] = []
        for ev in patient_events:
            ev_start = _parse_event_start(ev, tz)
            if ev_start is not None and ev_start > now:
                future_events.append(ev)
        patient_events = future_events

        # ── Sin coincidencia real → NO hay cita ──────────────────────────────
        # NOTA: El fallback que usaba all_events[:1] fue eliminado porque en una
        # clínica con múltiples pacientes causaba cancelar/reagendar la cita de
        # la persona EQUIVOCADA (el primer evento del calendario).
        if not patient_events:
            logger.info(
                "node_check_existing_appointment: sin citas para el paciente",
                phone=phone,
                patient_name=patient_name,
                total_events_in_range=len(all_events),
            )
            # Calcular slots_available del día solicitado aunque no haya cita del paciente
            slots_avail = _compute_slots_available(all_events, requested_day, service, tz, state)
            return {
                **_no_appointment_found(),
                "calendar_slots_available": slots_avail,
            }

        # ── Refinar: coincidencia con servicio+día solicitado ─────────────────
        # Esto responde la pregunta "¿ya tiene una cita para ESTE servicio en ESTE día?"
        # Solo se aplica cuando ambos están disponibles (intent "agendar" normalmente).
        first_exact_match: Optional[Dict[str, Any]] = None
        if service and requested_day:
            for ev in patient_events:
                # Verificar que el evento es del mismo día calendario
                ev_start = _parse_event_start(ev, tz)
                if ev_start is None:
                    continue
                same_day = ev_start.date() == requested_day.date()
                # Verificar que el servicio aparece en el summary (formato: "servicio - Nombre")
                has_service = _service_in_text(service, ev.get("summary") or "")
                if same_day and has_service:
                    first_exact_match = _event_to_dict(ev)
                    break

        # ── Construir lista de citas del paciente (máx 3) ─────────────────────
        existing = [_event_to_dict(ev) for ev in patient_events[:3]]
        first = existing[0]

        # Auto-fill paciente name si no se tiene y existe en la descripción del evento
        extracted_name = _extract_patient_name_from_description(first.get("description", ""))
        if extracted_name and not patient_name:
            patient_name = extracted_name

        # ── Calcular slots libres aproximados en el día solicitado ────────────
        slots_avail = _compute_slots_available(all_events, requested_day, service, tz, state)

        logger.info(
            "node_check_existing_appointment: citas del paciente encontradas",
            phone=phone,
            patient_name=patient_name,
            intent=intent,
            total_patient_events=len(patient_events),
            exact_match_found=first_exact_match is not None,
            matched_by_phone=len(found_by_phone) > 0,
            matched_by_name=len(found_by_name) > 0,
            slots_available=slots_avail,
        )

        # ── Clasificar el hallazgo según intent ──────────────────────────────
        #
        # Para "cancelar" / "reagendar": siempre queremos el event_id para operar.
        #
        # Para "agendar": solo hay un conflicto REAL si la cita existente es el MISMO
        # día que el slot solicitado. Una cita en otro día NO debe bloquear la nueva
        # reserva; solo debe informarse como contexto.
        # Si aún no se conoce la fecha solicitada (datetime_preference=None) se asume
        # sin conflicto y se deja que check_missing recoja la fecha primero.
        if intent in ("cancelar", "reagendar"):
            return {
                "calendar_lookup_done": True,
                "calendar_appointment_found": True,
                "existing_appointments": existing,
                "calendar_total_for_patient": len(patient_events),
                "calendar_slots_available": slots_avail,
                "calendar_first_match": first_exact_match,
                "google_event_id": first["event_id"],
                "google_event_link": first["html_link"],
                "patient_name": patient_name,  # Auto-filled from existing appointment
            }

        # intent == "agendar" (o cualquier otro): comprobar si hay conflicto de día
        same_day_conflict = False
        if requested_day:
            for ev_dict in existing:
                ev_start_str = ev_dict.get("start", "")
                if not ev_start_str:
                    continue
                try:
                    ev_dt = datetime.fromisoformat(ev_start_str)
                    ev_dt = ev_dt.replace(tzinfo=tz) if ev_dt.tzinfo is None else ev_dt
                    if ev_dt.date() == requested_day.date():
                        same_day_conflict = True
                        break
                except ValueError:
                    pass

        if same_day_conflict:
            # Conflicto real: el paciente ya tiene cita ESE día → prepare_modification
            logger.info(
                "node_check_existing_appointment: conflicto mismo día",
                requested=requested_day.date().isoformat(),
                existing_start=first["start"],
            )
            return {
                "calendar_lookup_done": True,
                "calendar_appointment_found": True,
                "existing_appointments": existing,
                "calendar_total_for_patient": len(patient_events),
                "calendar_slots_available": slots_avail,
                "calendar_first_match": first_exact_match,
                "google_event_id": first["event_id"],
                "google_event_link": first["html_link"],
                "patient_name": patient_name,  # Auto-filled from existing appointment
            }

        # Sin conflicto de día (cita en otro día o fecha aún desconocida):
        # NO establecer calendar_appointment_found=True ni google_event_id.
        # Se mantiene existing_appointments para que el LLM informe "tiene una cita
        # el [día X]" como contexto, pero sin bloquear el nuevo agendamiento.
        logger.info(
            "node_check_existing_appointment: cita en otro día — sin conflicto para agendar",
            requested=requested_day.date().isoformat() if requested_day else "desconocida",
            existing_start=first["start"],
        )
        return {
            "calendar_lookup_done": True,
            "calendar_appointment_found": False,   # no hay conflicto para la nueva cita
            "existing_appointments": existing,     # mantener para contexto informativo
            "calendar_total_for_patient": len(patient_events),
            "calendar_slots_available": slots_avail,
            "calendar_first_match": None,
            "google_event_id": None,               # no tocar el event_id del booking actual
            "google_event_link": None,
            "patient_name": patient_name,  # Auto-filled from existing appointment
        }

    except Exception as e:
        logger.error("node_check_existing_appointment: error", error=str(e))
        return _no_appointment_found()



async def node_lookup_appointment(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
) -> Dict[str, Any]:
    """
    Busca la cita real del cliente en Google Calendar.
    DETERMINISTA — cero LLM.

    Siempre consulta Google Calendar (no confía en memoria/estado).
    Busca en los próximos 60 días eventos cuya descripción contenga el número de teléfono.
    Actualiza el estado con el evento encontrado (google_event_id, datetime_preference, etc.)
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    if not calendar_service:
        logger.warning("node_lookup_appointment: sin calendar_service, saltando")
        return {}

    phone = state.get("phone_number", "")
    if not phone:
        logger.warning("node_lookup_appointment: sin phone_number")
        return {}

    try:
        from datetime import date
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Guayaquil")
        now = datetime.now(tz)
        future = now + timedelta(days=60)

        # list_events no existe en el wrapper — usar search_events_by_query
        events = await calendar_service.search_events_by_query(
            q=phone,
            start_date=now,
            end_date=future,
        )

        # Buscar eventos que contengan el teléfono del cliente en la descripción
        found = None
        for event in events:
            desc = event.get("description", "") or ""
            summary = event.get("summary", "") or ""
            if phone in desc or phone.lstrip("+") in desc:
                found = event
                break

        if not found:
            logger.info(
                "node_lookup_appointment: no se encontró cita para el cliente",
                phone=phone,
                events_checked=len(events),
            )
            # Limpiar estado de cita anterior para que el LLM no use datos viejos
            return {
                "google_event_id": None,
                "google_event_link": None,
                "appointment_id": None,
                "datetime_preference": None,
                "calendar_lookup_done": True,
                "calendar_appointment_found": False,
            }

        # Extraer datos del evento encontrado
        event_id = found.get("id")
        event_link = found.get("htmlLink")
        start_str = found.get("start", {}).get("dateTime") or found.get("start", {}).get("date")
        summary = found.get("summary", "")

        # Parsear datetime del evento
        dt_str = None
        if start_str:
            dt_str = start_str.split("+")[0].split("Z")[0]  # quitar timezone suffix

        logger.info(
            "node_lookup_appointment: cita encontrada en Calendar",
            phone=phone,
            event_id=event_id,
            start=dt_str,
            summary=summary,
        )

        return {
            "google_event_id": event_id,
            "google_event_link": event_link,
            "datetime_preference": dt_str,
            "calendar_lookup_done": True,
            "calendar_appointment_found": True,
        }

    except Exception as e:
        logger.error("node_lookup_appointment: error consultando Calendar", error=str(e))
        return {
            "calendar_lookup_done": True,
            "calendar_appointment_found": False,
        }


