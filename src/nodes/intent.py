"""intent — nodos de extracción de intención."""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

from src.state import ArcadiumState, VALID_SERVICES, get_missing_fields
from src.llm_extractors import extract_intent_llm, extract_booking_data
from src.nodes_backup import _last_human_text
from config.calendar_mapping import get_email_for_short_key

logger = structlog.get_logger("langgraph.nodes.intent")


async def node_extract_intent(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Fallback del routing de keywords.
    SOLO se llama cuando route_by_keywords retornó None.
    1 llamada al LLM.
    """
    if not llm:
        return {"last_error": "LLM no disponible para extract_intent"}

    text = _last_human_text(state)
    history = state.get("messages", [])
    intent, confidence = await extract_intent_llm(text, llm, history=history)

    logger.info("intent extraído por LLM", intent=intent, confidence=confidence)
    return {"intent": intent, "current_step": "intent_extracted"}



async def node_extract_data(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Extrae servicio, fecha y nombre del texto libre.
    1 llamada al LLM.
    """
    if not llm:
        return {"last_error": "LLM no disponible para extract_data"}

    missing = get_missing_fields(state)
    if not missing:
        return {}  # Ya tenemos todo

    text = _last_human_text(state)
    context = {
        "fecha_hoy": state.get("fecha_hoy", ""),
        "manana_fecha": state.get("manana_fecha", ""),
        "dia_semana_hoy": state.get("dia_semana_hoy", ""),
        "manana_dia": state.get("manana_dia", ""),
        "missing_fields": missing,
    }

    history = state.get("messages", [])
    data = await extract_booking_data(text, context, llm, history=history)

    prev_calls = state.get("_extract_data_calls", 0)

    updates: Dict[str, Any] = {}
    updates["_extract_data_calls"] = prev_calls + 1

    existing_service = state.get("selected_service")
    if data.get("service"):
        svc = data["service"]
        svc_lower = svc.lower().strip()

        # Guard contra sobreescritura: si ya hay servicio confirmado y el LLM extrae
        # un servicio diferente sin que el usuario lo haya mencionado explícitamente
        # en el mensaje actual, conservar el servicio original.
        # El LLM a veces "inventa" el servicio basado en el historial cuando re-extrae.
        if existing_service and existing_service != svc_lower:
            last_msg = _last_human_text(state).lower()
            service_mentioned_in_msg = any(svc_kw in last_msg for svc_kw in VALID_SERVICES)
            if not service_mentioned_in_msg:
                logger.info(
                    "node_extract_data: ignorando cambio de servicio (no mencionado en msg)",
                    existing=existing_service,
                    extracted=svc_lower,
                )
                svc_lower = existing_service

        if svc_lower in VALID_SERVICES:
            updates["selected_service"] = svc_lower
            updates["service_duration"] = VALID_SERVICES[svc_lower]
        else:
            for known, duration in VALID_SERVICES.items():
                if known in svc_lower or svc_lower in known:
                    updates["selected_service"] = known
                    updates["service_duration"] = duration
                    break
            else:
                updates["selected_service"] = svc_lower
                updates["service_duration"] = 60

    if data.get("datetime_iso"):
        old_datetime = state.get("datetime_preference")
        new_datetime = data["datetime_iso"]
        if old_datetime != new_datetime:
            # Usuario cambió de fecha → limpiar slots para forzar regeneración
            updates["available_slots"] = []
            updates["awaiting_confirmation"] = False
        updates["datetime_preference"] = new_datetime

    if data.get("patient_name"):
        updates["patient_name"] = data["patient_name"]

    # --- Resolución de doctor ---
    _DOCTOR_EMAILS = {
        "jorge": "jorge.arias.amauta@gmail.com",
        "javier": "javiarias000@gmail.com",
    }
    extracted_doctor = data.get("doctor_name")
    existing_doctor_email = state.get("doctor_email")

    if extracted_doctor and extracted_doctor in _DOCTOR_EMAILS:
        # Mención explícita en este turno — siempre sobreescribe
        updates["doctor_email"] = _DOCTOR_EMAILS[extracted_doctor]
    elif not existing_doctor_email:
        # Sin doctor en estado — usar fallback por servicio
        resolved_service = updates.get("selected_service") or state.get("selected_service")
        if resolved_service:
            fallback = get_email_for_short_key(resolved_service)
            if fallback:
                updates["doctor_email"] = fallback
    # else: doctor ya en estado + sin mención → conservar (no tocar)

    # Recalcular missing
    merged = {**state, **updates}
    updates["missing_fields"] = get_missing_fields(merged)

    logger.info(
        "datos extraídos por LLM",
        extracted={k: v for k, v in updates.items() if k != "missing_fields"},
    )
    return updates


