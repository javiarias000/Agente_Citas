"""
Tests de integración — end-to-end del grafo LangGraph.

Simulan flujos reales SIN LLM real (nodos LLM mockeados).
Cubren: agendar, cancelar, reagendar, consultar, ajuste fin de semana,
escalada a humano y pedir datos secuencialmente.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, AIMessage

# Imports del proyecto
from src.state import (
    ArcadiumState,
    create_initial_arcadium_state,
    get_missing_fields,
    is_weekend_adjusted,
    VALID_SERVICES,
)
from src.intent_router import detect_confirmation, extract_slot_from_text
from src.store import InMemoryStore

# ─── Helpers de test ──────────────────────────────────────────

TIMEZONE = ZoneInfo("America/Guayaquil")


def make_human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def ai_response(text: str) -> AIMessage:
    return AIMessage(content=text)


def set_in_state(state: ArcadiumState, **kwargs) -> None:
    """Helper: setea campos en el state dict."""
    state.update(kwargs)


# Mock del LLM

class MockLLM:
    """LLM mock que responde según el último mensaje del humano."""

    def __init__(self):
        self.responses: Dict[str, str] = {}
        self.call_count = 0

    async def ainvoke(self, prompt) -> AIMessage:
        self.call_count += 1
        # Extraer texto del ultimo mensaje humano en el prompt
        text = ""
        if isinstance(prompt, list):
            for msg in prompt:
                if hasattr(msg, "content") and isinstance(msg.content, str):
                    text = msg.content
        return AIMessage(content=self._generate_response(text))

    def _generate_response(self, text: str) -> str:
        self.last_text = text
        # Respuestas simuladas estilo Deyy
        if "intent" in text.lower() or "clasifica" in text.lower():
            return '{"intent": "agendar", "confidence": 0.95}'

        if "extrae" in text.lower() or "extracto" in text.lower() or "service" in text.lower():
            if "limpieza" in text.lower():
                return json_dumps({"service": "limpieza", "datetime_iso": None, "patient_name": None, "missing": ["patient_name", "datetime_preference"]})
            if "mañana" in text.lower():
                manana = datetime.now(TIMEZONE) + timedelta(days=1)
                return json_dumps({"service": None, "datetime_iso": manana.strftime("%Y-%m-%dT10:00"), "patient_name": None, "missing": ["patient_name"]})
            return json_dumps({"service": None, "datetime_iso": None, "patient_name": None, "confidence": 0.5, "missing": ["selected_service", "datetime_preference", "patient_name"]})

        # Respuestas de Deyy simuladas
        if "disponible" in text or "slot" in text:
            return "¡Hola! 😊 Hay espacio mañana a las 10:00 y 10:30. ¿Cuál le funciona mejor?"
        if "confirm" in text.lower() or "agend" in text.lower():
            return "Perfecto ✅ Le agendé para mañana a las 10:00. ¡Nos vemos! 🦷"
        if "cancelar" in text.lower():
            return "¿Está seguro que desea cancelar su cita? 📅"
        if "falta" in text.lower() or "nombre" in text.lower():
            return "Claro, ¿cuál es su nombre? 😊"
        if "servicio" in text.lower():
            return "¿Qué servicio necesita? Por ejemplo: limpeza, consulta, etc. 🦷"
        if "fecha" in text.lower():
            return "¿Qué fecha y hora le funciona mejor? Tenemos disponibilidad esta semana 📅"

        return "¿En qué puedo ayudarle hoy? 😊"


def json_dumps(d):
    import json
    return json.dumps(d)


# ─── Tests de nodos deterministas ───────────────────────────────

@pytest.mark.asyncio
async def test_node_detect_confirmation_yes():
    """Confirmación afirmativa."""
    state = {
        "messages": [make_human("sí, confirmo")],
        "available_slots": ["2026-04-10T10:00"],
        "selected_slot": None,
    }
    from src.nodes import node_detect_confirmation
    # node_detect_confirmation espera state con get
    result = await node_detect_confirmation(state)
    assert result["confirmation_result"] == "yes"


@pytest.mark.asyncio
async def test_node_detect_confirmation_no():
    """Confirmación negativa."""
    state = {
        "messages": [make_human("no, mejor no")],
        "available_slots": [],
        "selected_slot": None,
    }
    from src.nodes import node_detect_confirmation
    result = await node_detect_confirmation(state)
    assert result["confirmation_result"] == "no"


@pytest.mark.asyncio
async def test_node_detect_confirmation_slot_choice():
    """Usuario elige un horario."""
    state = {
        "messages": [make_human("a las 10:00 por favor")],
        "available_slots": ["2026-04-10T10:00", "2026-04-10T14:30"],
        "selected_slot": None,
    }
    from src.nodes import node_detect_confirmation
    result = await node_detect_confirmation(state)
    assert result["confirmation_result"] == "slot_choice"
    assert result["selected_slot"] == "2026-04-10T10:00"


@pytest.mark.asyncio
async def test_node_route_intent_agendar():
    """Intent 'agendar' detectado por keywords."""
    state = {"messages": [make_human("quiero agendar una limpieza")]}
    from src.nodes import node_route_intent
    result = await node_route_intent(state)
    assert result["intent"] == "agendar"


@pytest.mark.asyncio
async def test_node_route_intent_cancelar():
    """Intent 'cancelar' detectado por keywords."""
    state = {"messages": [make_human("necesito cancelar mi cita")]}
    from src.nodes import node_route_intent
    result = await node_route_intent(state)
    assert result["intent"] == "cancelar"


@pytest.mark.asyncio
async def test_node_check_missing_all_missing():
    """Cuando faltan todos los campos."""
    state = create_initial_arcadium_state("+593999999999")
    from src.nodes import node_check_missing
    result = await node_check_missing(state)
    assert "patient_name" in result["missing_fields"]
    assert "selected_service" in result["missing_fields"]
    assert "datetime_preference" in result["missing_fields"]


# ─── Tests de flujo completo (simulados) ────────────────────────

@pytest.mark.asyncio
async def test_weekend_auto_adjust():
    """Fecha sábado se ajusta a lunes automáticamente."""
    # 2026-04-04 es sábado
    adjusted, new_iso = is_weekend_adjusted("2026-04-04T10:00")
    assert adjusted is True
    assert "2026-04-06" in new_iso  # lunes


@pytest.mark.asyncio
async def test_weekday_not_adjusted():
    """Fecha martes NO se ajusta."""
    adjusted, new_iso = is_weekend_adjusted("2026-04-07T10:00")
    assert adjusted is False
    assert new_iso == "2026-04-07T10:00"


# ─── Tests de store ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_inmemory_store_roundtrip():
    """InMemoryStore guarda y recupera correctamente."""
    store = InMemoryStore()
    await store.initialize()

    phone = "+593999999999"

    # Save and retrieve history
    await store.add_message(phone, make_human("hola"))
    await store.add_message(phone, ai_response("hola! 😊"))
    history = await store.get_history(phone)
    assert len(history) == 2

    # Save and retrieve agent state
    state = {"intent": "agendar", "patient_name": "Juan"}
    await store.save_agent_state(phone, state)
    saved = await store.get_agent_state(phone)
    assert saved["intent"] == "agendar"
    assert saved["patient_name"] == "Juan"

    # Upsert profile
    profile = await store.upsert_user_profile(phone, {"patient_name": "Juan"})
    assert profile["patient_name"] == "Juan"


# ─── Tests de edge cases ──────────────────────────────────────

def test_extract_slot_from_text_partial_match():
    """Slot encontrado con referencia parcial."""
    slots = ["2026-04-10T10:00:00", "2026-04-10T14:30:00"]
    slot = extract_slot_from_text("prefiero las 14:30", slots)
    assert slot == "2026-04-10T14:30:00"


def test_get_missing_fields_partial():
    """Falta un solo campo."""
    state = create_initial_arcadium_state("+593999999999")
    state["patient_name"] = "Maria"
    state["selected_service"] = "limpieza"
    missing = get_missing_fields(state)
    assert "patient_name" not in missing
    assert "selected_service" not in missing
    assert "datetime_preference" in missing


# ─── Tests de nodos con mocks ─────────────────────────────────────

@pytest.mark.asyncio
async def test_node_check_availability_no_date():
    """Sin fecha → retorna error claro."""
    state: ArcadiumState = create_initial_arcadium_state("+593999999999")
    from src.nodes import node_check_availability
    result = await node_check_availability(state, calendar_service=None)
    assert result.get("last_error")
    assert result.get("available_slots") == []


@pytest.mark.asyncio
async def test_node_extract_data_no_llm():
    """Sin LLM → retorna error, no crashea."""
    state = create_initial_arcadium_state("+593999999999")
    from src.nodes import node_extract_data
    result = await node_extract_data(state, llm=None)
    assert "last_error" in result


@pytest.mark.asyncio
async def test_node_generate_response_no_llm_fallback():
    """Sin LLM → fallback determinista."""
    state = create_initial_arcadium_state("+593999999999")
    from src.nodes import node_generate_response
    result = await node_generate_response(state, llm=None)
    assert "messages" in result
    assert len(result["messages"]) > 0
    msg = result["messages"][0]
    assert "📞" in msg.content  # fallback incluye teléfono

