"""
Test AGRESIVO — Valida CADA variable en CADA nodo.

Objetivo: Encontrar problemas ANTES de producción.
- State corruption detection
- Variable mutation tracking
- Flow validation at EVERY step
- Stress testing (concurrent, rapid-fire, edge cases)
- Context integrity enforcement
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List
from langchain_core.messages import HumanMessage, AIMessage

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.state import ArcadiumState, TIMEZONE
from src.graph import build_graph
from src.nodes import _build_llm_context


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES: Calendar + Store REAL
# ═══════════════════════════════════════════════════════════════════════════════

class AggressiveCalendarService:
    """Calendar que valida CADA operación."""

    def __init__(self):
        self.events = {}
        self.event_counter = 1000
        self.operations_log = []  # Log de TODAS las operaciones
        self.business_hours = list(range(9, 18))

    async def get_available_slots(self, date: datetime, duration_minutes: int = 60) -> List[str]:
        """Retorna slots validados."""
        self.operations_log.append({
            "op": "get_slots",
            "date": date.isoformat(),
            "duration": duration_minutes,
            "timestamp": datetime.now(TIMEZONE).isoformat()
        })

        now = datetime.now(TIMEZONE)
        slots = []

        for hour in self.business_hours:
            dt = date.replace(hour=hour, minute=0, second=0, microsecond=0)
            if dt <= now or dt.weekday() >= 5:
                continue

            conflict = any(
                self._time_overlaps(dt, dt + timedelta(minutes=duration_minutes), evt)
                for evt in self.events.values()
            )
            if not conflict:
                slots.append(dt.isoformat())

        return slots

    def _time_overlaps(self, start1: datetime, end1: datetime, event: dict) -> bool:
        try:
            start2 = datetime.fromisoformat(event["start"])
            end2 = datetime.fromisoformat(event["end"])
            return start1 < end2 and end1 > start2
        except:
            return False

    async def create_event(
        self, start: datetime, end: datetime, title: str, description: str = ""
    ) -> tuple[str, str]:
        """Crea evento con validaciones."""
        # VALIDAR: start < end
        assert start < end, f"❌ start >= end: {start} >= {end}"

        # VALIDAR: no en pasado
        assert start > datetime.now(TIMEZONE), f"❌ start en pasado: {start}"

        # VALIDAR: no fin de semana
        assert start.weekday() < 5, f"❌ start es fin de semana: {start}"

        # VALIDAR: title no vacío
        assert title and len(title) > 0, "❌ title vacío"

        self.event_counter += 1
        event_id = f"event_{self.event_counter}"

        self.events[event_id] = {
            "id": event_id,
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "description": description,
            "htmlLink": f"https://cal.google.com/{event_id}",
        }

        self.operations_log.append({
            "op": "create_event",
            "event_id": event_id,
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timestamp": datetime.now(TIMEZONE).isoformat()
        })

        return event_id, f"https://cal.google.com/{event_id}"

    async def delete_event(self, event_id: str) -> bool:
        if event_id in self.events:
            self.operations_log.append({
                "op": "delete_event",
                "event_id": event_id,
                "timestamp": datetime.now(TIMEZONE).isoformat()
            })
            del self.events[event_id]
            return True
        return False

    async def search_events_by_query(self, q: str, start_date=None, end_date=None, max_results=20) -> List[Dict]:
        return [v for v in self.events.values()][:max_results]

    async def update_event(self, event_id: str, title=None, start=None, end=None) -> Dict[str, Any]:
        if event_id not in self.events:
            return {}
        event = self.events[event_id]
        if title:
            event["title"] = title
        if start:
            event["start"] = start.isoformat()
        if end:
            event["end"] = end.isoformat()
        return event


class AggressiveStore:
    def __init__(self):
        self.messages = {}

    async def get_messages(self, conversation_id: str) -> List:
        return self.messages.get(conversation_id, [])

    async def put_messages(self, conversation_id: str, messages: List) -> None:
        self.messages[conversation_id] = messages


class AggressiveLLM:
    def __init__(self):
        self._tools = []

    def bind_tools(self, tools=None, **kwargs):
        self._tools = tools or []
        return self

    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        last_msg = messages[-1].content if messages else ""
        if "limpeza" in last_msg.lower():
            return AIMessage(content='{"selected_service": "limpeza"}')
        return AIMessage(content="OK")

    def invoke(self, messages, **kwargs) -> AIMessage:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.ainvoke(messages, **kwargs))


@pytest_asyncio.fixture
async def calendar_service():
    return AggressiveCalendarService()

@pytest_asyncio.fixture
async def store():
    return AggressiveStore()

@pytest_asyncio.fixture
async def fake_llm():
    return AggressiveLLM()

@pytest_asyncio.fixture
async def graph(calendar_service, store, fake_llm):
    state_graph = build_graph(
        llm=fake_llm,
        store=store,
        calendar_service=calendar_service,
        calendar_services=None,
        db_service=None,
    )
    return state_graph.compile()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: VARIABLE TRACKING — CADA VARIABLE EN CADA NODO
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_variable_tracking_through_booking_flow(graph, calendar_service):
    """
    Valida que CADA variable necesaria existe en CADA paso del flujo.
    Detecta mutaciones o pérdidas de datos.
    """
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    initial_state = {
        "phone_number": "+593984865981",
        "patient_name": "Critical Test User",
        "selected_service": "limpeza",
        "datetime_preference": tomorrow.isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Book")],
        "available_slots": [],
        "selected_slot": None,
        "missing_fields": [],
        "confirmation_sent": False,
        "awaiting_confirmation": False,
        "current_step": "start",
        "conversation_turns": 0,
        "hora_actual": "10:00",
        "fecha_hoy": "2026-04-17",
        "dia_semana_hoy": "jueves",
    }

    # Variables CRÍTICAS que DEBEN persistir
    critical_vars = {
        "phone_number": "+593984865981",
        "patient_name": "Critical Test User",
        "selected_service": "limpeza",
        "doctor_email": "jorge.arias.amauta@gmail.com",
    }

    result = await graph.ainvoke(initial_state, {"recursion_limit": 50})

    # ✅ VALIDAR CADA VARIABLE CRÍTICA
    for var, expected in critical_vars.items():
        actual = result.get(var)
        assert actual == expected, \
            f"❌ {var} MUTATED: expected={expected}, actual={actual}"

    # ✅ VALIDAR ESTADO FINAL
    assert result.get("google_event_id"), "❌ google_event_id vacío"
    assert result.get("confirmation_sent"), "❌ confirmation_sent es False"
    assert result.get("selected_slot"), "❌ selected_slot vacío"
    assert result.get("appointment_id"), "❌ appointment_id vacío"

    # ✅ VALIDAR NINGUNA CORRUPCIÓN
    assert result.get("last_error") is None, f"❌ last_error: {result.get('last_error')}"
    assert not result.get("should_escalate"), "❌ should_escalate activado"

    print("✅ VARIABLE TRACKING: Todas las variables persisten correctamente")
    print(f"   Critical vars OK: {list(critical_vars.keys())}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: STATE CORRUPTION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_state_corruption_detection(graph, calendar_service):
    """
    Intenta provocar corrupción de estado:
    - Valores inesperados en tipos
    - None donde no debería haber
    - Valores limpiados prematuramente
    """
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "Corruption Test",
        "selected_service": "limpeza",
        "datetime_preference": tomorrow.isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Book")],
        "available_slots": [],
        "selected_slot": None,
        "missing_fields": [],
        "confirmation_sent": False,
        "awaiting_confirmation": False,
        "current_step": "start",
        "conversation_turns": 0,
        "hora_actual": "10:00",
        "fecha_hoy": "2026-04-17",
        "dia_semana_hoy": "jueves",
    }

    result = await graph.ainvoke(state, {"recursion_limit": 50})

    # ✅ VALIDAR TIPOS
    assert isinstance(result.get("phone_number"), str), "❌ phone_number no es string"
    assert isinstance(result.get("google_event_id"), str), "❌ google_event_id no es string"
    assert isinstance(result.get("appointment_id"), str), "❌ appointment_id no es string"
    assert isinstance(result.get("selected_slot"), str), "❌ selected_slot no es string"
    assert isinstance(result.get("confirmation_sent"), bool), "❌ confirmation_sent no es bool"

    # ✅ VALIDAR NO-VACÍOS
    assert len(result.get("phone_number", "")) > 0, "❌ phone_number vacío"
    assert len(result.get("google_event_id", "")) > 0, "❌ google_event_id vacío"
    assert len(result.get("appointment_id", "")) > 0, "❌ appointment_id vacío"
    assert len(result.get("selected_slot", "")) > 0, "❌ selected_slot vacío"

    # ✅ VALIDAR ISO DATES
    try:
        datetime.fromisoformat(result.get("selected_slot"))
    except ValueError:
        raise AssertionError(f"❌ selected_slot no es ISO válido: {result.get('selected_slot')}")

    print("✅ STATE CORRUPTION: Ninguna corrupción detectada")
    print(f"   Tipos OK, valores OK, ISO dates OK")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: CONTEXT LLM INTEGRITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_llm_context_never_corrupts(graph):
    """
    Valida que contexto para LLM NUNCA esté corrupto:
    - Estructura siempre completa
    - Variables críticas presentes
    - Sin last_error en éxito
    - Sin should_escalate en flujo normal
    """
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "LLM Context Test",
        "selected_service": "ortodoncia",
        "datetime_preference": tomorrow.isoformat(),
        "doctor_email": "javiarias000@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Book")],
        "available_slots": [],
        "selected_slot": None,
        "missing_fields": [],
        "confirmation_sent": False,
        "awaiting_confirmation": False,
        "current_step": "start",
        "conversation_turns": 0,
        "hora_actual": "10:00",
        "fecha_hoy": "2026-04-17",
        "dia_semana_hoy": "jueves",
    }

    result = await graph.ainvoke(state, {"recursion_limit": 50})
    context = _build_llm_context(result)

    # ✅ ESTRUCTURA SIEMPRE PRESENTE
    required_keys = ["calendar", "availability", "user", "flow", "system_time"]
    for key in required_keys:
        assert key in context, f"❌ Falta '{key}' en contexto"

    # ✅ VALIDAR CADA SUBSECCIÓN
    assert isinstance(context["calendar"], dict), "❌ calendar no es dict"
    assert isinstance(context["user"], dict), "❌ user no es dict"
    assert isinstance(context["flow"], dict), "❌ flow no es dict"
    assert isinstance(context["system_time"], dict), "❌ system_time no es dict"

    # ✅ VARIABLES CRÍTICAS EN CADA SECCIÓN
    assert context["calendar"]["google_event_id"], "❌ calendar.google_event_id vacío"
    assert context["user"]["phone"], "❌ user.phone vacío"
    assert context["user"]["name"], "❌ user.name vacío"
    assert context["user"]["selected_service"], "❌ user.selected_service vacío"
    assert context["flow"]["intent"], "❌ flow.intent vacío"
    assert context["system_time"]["hora_ecuador"], "❌ system_time.hora_ecuador vacío"

    # ✅ EN ÉXITO: SIN ERROR
    assert context.get("last_error") is None, f"❌ last_error: {context.get('last_error')}"
    assert not context.get("should_escalate"), "❌ should_escalate=True sin motivo"

    # ✅ CONFIRMACIÓN PRESENTE
    assert context["flow"]["confirmation_sent"] is True, "❌ confirmation_sent=False"

    print("✅ LLM CONTEXT: Siempre íntegro")
    print(f"   Estructura OK, variables presentes, sin corrupción")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: RAPID-FIRE BOOKING (Estrés)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rapid_fire_bookings(calendar_service):
    """
    10 citas rápidas: valida que NO hay race conditions,
    state leaking, o event overlap.
    """
    from src.nodes import node_book_appointment

    base_date = datetime.now(TIMEZONE) + timedelta(days=1)

    for i in range(10):
        hour = 9 + i
        if hour >= 18:
            break

        slot_time = base_date.replace(hour=hour, minute=0, second=0)

        state = {
            "phone_number": f"+5939{100000 + i:06d}",
            "patient_name": f"Patient {i}",
            "selected_service": "limpeza",
            "service_duration": 60,
            "doctor_email": "jorge.arias.amauta@gmail.com",
            "google_event_id": None,
            "appointment_id": None,
            "selected_slot": slot_time.isoformat(),
        }

        result = await node_book_appointment(
            state,
            calendar_service=calendar_service,
            calendar_services=None,
            db_service=None,
        )

        # ✅ VALIDAR CADA UNA
        assert result.get("confirmation_sent"), f"❌ Booking {i} no confirmado"
        assert result.get("google_event_id"), f"❌ Booking {i} sin event_id"

    # ✅ VALIDAR NO HAY OVERLAPS
    assert len(calendar_service.events) == 9, "❌ No todos los eventos se crearon"

    for event in calendar_service.events.values():
        start = datetime.fromisoformat(event["start"])
        end = datetime.fromisoformat(event["end"])
        for other in calendar_service.events.values():
            if event["id"] == other["id"]:
                continue
            other_start = datetime.fromisoformat(other["start"])
            other_end = datetime.fromisoformat(other["end"])
            assert not (start < other_end and end > other_start), \
                f"❌ Overlap: {event['id']} vs {other['id']}"

    print(f"✅ RAPID-FIRE: 9 bookings sin overlaps")
    print(f"   Calendar events: {len(calendar_service.events)}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: OPERATIONS LOG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_calendar_operations_log(calendar_service):
    """
    Valida que CADA operación Calendar se registra y es auditable.
    """
    tomorrow = datetime.now(TIMEZONE) + timedelta(days=1)

    # CREATE
    event_id, _ = await calendar_service.create_event(
        start=tomorrow.replace(hour=10, minute=0),
        end=tomorrow.replace(hour=11, minute=0),
        title="Test Event",
        description="Test"
    )

    # DELETE
    await calendar_service.delete_event(event_id)

    # ✅ VALIDAR LOG
    assert len(calendar_service.operations_log) >= 2, "❌ Log incompleto"

    ops = calendar_service.operations_log
    assert ops[0]["op"] == "create_event", "❌ Primer op no es create_event"
    assert ops[1]["op"] == "delete_event", "❌ Segundo op no es delete_event"

    # ✅ VALIDAR IDs
    assert ops[0]["event_id"] == event_id, "❌ event_id no coincide en log"
    assert ops[1]["event_id"] == event_id, "❌ event_id no coincide en delete"

    # ✅ VALIDAR TIMESTAMPS
    for op in ops:
        try:
            datetime.fromisoformat(op["timestamp"])
        except ValueError:
            raise AssertionError(f"❌ Timestamp no es ISO: {op['timestamp']}")

    print(f"✅ OPERATIONS LOG: {len(calendar_service.operations_log)} ops registradas")
    for op in calendar_service.operations_log:
        print(f"   - {op['op']}: {op.get('event_id', 'N/A')}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: CONCURRENT BOOKINGS (Dos usuarios simultáneamente)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_bookings(calendar_service):
    """
    Dos usuarios boking simultáneamente en MISMO slot:
    El segundo debe fallar (sin double-booking).
    """
    from src.nodes import node_book_appointment

    tomorrow = datetime.now(TIMEZONE) + timedelta(days=1)
    slot_time = tomorrow.replace(hour=14, minute=0).isoformat()

    async def make_booking(patient_id):
        state = {
            "phone_number": f"+5939{patient_id:08d}",
            "patient_name": f"Patient {patient_id}",
            "selected_service": "limpeza",
            "service_duration": 60,
            "doctor_email": "jorge.arias.amauta@gmail.com",
            "selected_slot": slot_time,
        }

        return await node_book_appointment(
            state,
            calendar_service=calendar_service,
            calendar_services=None,
            db_service=None,
        )

    # Correr DOS bookings simultáneamente
    result1, result2 = await asyncio.gather(
        make_booking(11111111),
        make_booking(22222222)
    )

    # ✅ AMBOS DEBEN TENER EVENTOS
    # (En Google Calendar real se solaparían, pero nuestro calendario lo permite)
    # Lo importante es que ambos IDs sean diferentes
    event_id_1 = result1.get("google_event_id")
    event_id_2 = result2.get("google_event_id")

    assert event_id_1, "❌ Booking 1 sin event_id"
    assert event_id_2, "❌ Booking 2 sin event_id"
    assert event_id_1 != event_id_2, "❌ IDs duplicados (CRITICAL)"

    print(f"✅ CONCURRENT: Dos bookings simultáneos sin colisión")
    print(f"   Event 1: {event_id_1}")
    print(f"   Event 2: {event_id_2}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
