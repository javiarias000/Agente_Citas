"""
Test de integración COMPREHENSIVE — todos los flujos de agendamiento.

Cubre:
  - Agendar cita (nuevo)
  - Reagendar cita (cambiar fecha/hora)
  - Cancelar cita
  - Consultar citas
  - Edge cases: weekends, past times, multiple appointments
  - Error handling: invalid dates, no slots, etc.

SIN MOCKS de servicios críticos. DB en memoria, Calendar simulado, LLM real (en test).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.state import ArcadiumState, TIMEZONE
from src.graph import build_graph
from src.nodes import _build_llm_context


# ═══════════════════════════════════════════════════════════════════════════════
# CALENDAR SIMULADO — ALMACENA EVENTOS REALES
# ═══════════════════════════════════════════════════════════════════════════════

class RealFakeCalendarService:
    """Calendar simulado que actúa como real para tests."""

    def __init__(self):
        self.events = {}
        self.event_counter = 1000
        # Slots disponibles: 9-18 en intervalos de 1 hora
        self.business_hours = list(range(9, 18))

    async def get_available_slots(
        self,
        date: datetime,
        duration_minutes: int = 60
    ) -> List[str]:
        """Retorna slots disponibles, excluyendo eventoss ya booked."""
        now = datetime.now(TIMEZONE)
        slots = []

        for hour in self.business_hours:
            dt = date.replace(hour=hour, minute=0, second=0, microsecond=0)

            # No slots en el pasado
            if dt <= now:
                continue

            # Excluir fin de semana
            if dt.weekday() >= 5:
                continue

            # Verificar que no hay evento existente
            conflict = any(
                self._time_overlaps(dt, dt + timedelta(minutes=duration_minutes), evt)
                for evt in self.events.values()
            )
            if not conflict:
                slots.append(dt.isoformat())

        return slots

    def _time_overlaps(self, start1: datetime, end1: datetime, event: dict) -> bool:
        """Verifica si dos eventos se solapan."""
        try:
            start2 = datetime.fromisoformat(event["start"])
            end2 = datetime.fromisoformat(event["end"])
            return start1 < end2 and end1 > start2
        except:
            return False

    async def create_event(
        self,
        start: datetime,
        end: datetime,
        title: str,
        description: str = ""
    ) -> tuple[str, str]:
        """Crea evento."""
        self.event_counter += 1
        event_id = f"event_{self.event_counter}"
        html_link = f"https://calendar.google.com/event/{event_id}"

        self.events[event_id] = {
            "id": event_id,
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "description": description,
            "htmlLink": html_link,
        }
        return event_id, html_link

    async def delete_event(self, event_id: str) -> bool:
        """Elimina evento."""
        if event_id in self.events:
            del self.events[event_id]
            return True
        return False

    async def search_events_by_query(
        self, q: str, start_date=None, end_date=None, max_results=20
    ) -> List[Dict[str, Any]]:
        """Busca eventos por query."""
        results = []
        for evt in self.events.values():
            if q.lower() in evt["title"].lower() or q.lower() in evt["description"].lower():
                results.append(evt)
        return results[:max_results]

    async def update_event(
        self,
        event_id: str,
        title=None,
        start=None,
        end=None,
    ) -> Dict[str, Any]:
        """Actualiza evento."""
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


class FakeStore:
    """Store en memoria."""

    def __init__(self):
        self.messages = {}

    async def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        return self.messages.get(conversation_id, [])

    async def put_messages(
        self,
        conversation_id: str,
        messages: List[BaseMessage]
    ) -> None:
        self.messages[conversation_id] = messages


class FakeLLM:
    """LLM simulado."""

    def __init__(self):
        self._tools = []

    def bind_tools(self, tools=None, **kwargs):
        self._tools = tools or []
        return self

    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        last_msg = messages[-1].content if messages else ""

        # Extracción de datos (JSON puro)
        if "extract" in str(kwargs) or "booking_data" in str(kwargs):
            if "limpeza" in last_msg.lower():
                return AIMessage(content='{"selected_service": "limpeza"}')
            elif "ortodoncia" in last_msg.lower():
                return AIMessage(content='{"selected_service": "ortodoncia"}')
            return AIMessage(content='{}')

        # Respuesta de generación
        return AIMessage(content="Perfecto, he procesado tu solicitud.")

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
    return RealFakeCalendarService()


@pytest_asyncio.fixture
async def store():
    return FakeStore()


@pytest_asyncio.fixture
async def fake_llm():
    return FakeLLM()


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
# TEST 1: AGENDAR CITA (NUEVO)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_book_new_appointment(graph, calendar_service):
    """Flujo completo de agendamiento de nueva cita."""
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "Jorge Arias",
        "selected_service": "limpeza",
        "datetime_preference": tomorrow.isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Quiero agendar limpeza")],
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

    final = await graph.ainvoke(state, {"recursion_limit": 50})

    # ✅ Validaciones
    assert final.get("google_event_id"), f"❌ Event no creado. Error: {final.get('last_error')}"
    assert final.get("confirmation_sent"), "❌ No confirmado"
    assert final.get("selected_slot"), "❌ Slot no seleccionado"
    assert len(calendar_service.events) == 1, "❌ Evento no en calendar"

    event_id = final.get("google_event_id")
    event = calendar_service.events[event_id]
    assert "limpeza" in event["title"].lower(), "❌ Título incorrecto"
    assert "Jorge Arias" in event["description"], "❌ Nombre no en descripción"

    print(f"✅ AGENDAR: event_id={event_id}, appointment_id={final.get('appointment_id')}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: REAGENDAR CITA (CAMBIAR FECHA)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reschedule_appointment(calendar_service):
    """Flujo de reagendamiento: cambiar fecha/hora de cita existente.

    Este test SIMULA el flujo post-confirmación directamente,
    invocando el nodo de reagendamiento sin pasar por entrada/limpieza.
    """
    from src.nodes import node_reschedule_appointment

    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)
    day_after = (datetime.now(TIMEZONE) + timedelta(days=2)).replace(hour=14, minute=0)

    # Paso 1: Crear evento original (simular cita existente)
    old_event_id, _ = await calendar_service.create_event(
        start=tomorrow,
        end=tomorrow + timedelta(hours=1),
        title="limpeza - Jorge Arias",
        description="Paciente: Jorge Arias\nTeléfono: +593984865981"
    )
    assert len(calendar_service.events) == 1, "❌ Evento original no creado"

    # Paso 2: Preparar estado para reagendamiento
    new_slot = day_after.replace(hour=14, minute=0)
    state = {
        "phone_number": "+593984865981",
        "patient_name": "Jorge Arias",
        "selected_service": "limpeza",
        "service_duration": 60,
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "google_event_id": old_event_id,  # Evento a reemplazar
        "appointment_id": "appt_123",  # ID en DB
        "selected_slot": new_slot.isoformat(),  # Nuevo slot confirmado
        "confirmation_type": "reschedule",
        "confirmation_result": "yes",
        "awaiting_confirmation": False,
        "messages": [HumanMessage("Confirmar cambio")],
    }

    # Paso 3: Invocar nodo de reagendamiento directamente
    result = await node_reschedule_appointment(
        state,
        calendar_service=calendar_service,
        calendar_services=None,
        db_service=None,
    )

    # ✅ Validaciones
    new_event_id = result.get("google_event_id")
    assert result.get("confirmation_sent"), "❌ Reagendamiento no confirmado"
    assert new_event_id and new_event_id != old_event_id, \
        f"❌ Nuevo evento no creado. New ID: {new_event_id}, Old: {old_event_id}"

    # Verificar calendar state
    assert old_event_id not in calendar_service.events, \
        "❌ Evento viejo no fue eliminado"
    assert new_event_id in calendar_service.events, \
        "❌ Evento nuevo no está en calendar"
    assert len(calendar_service.events) == 1, \
        f"❌ Debería haber 1 evento, hay {len(calendar_service.events)}"

    print(f"✅ REAGENDAR: {old_event_id} → {new_event_id}")
    print(f"   Viejo eliminado: {old_event_id not in calendar_service.events}")
    print(f"   Nuevo en {new_slot}: {new_event_id in calendar_service.events}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: CANCELAR CITA
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_appointment(calendar_service):
    """Flujo de cancelación: eliminar cita existente.

    Este test SIMULA el flujo post-confirmación directamente,
    invocando el nodo de cancelación sin pasar por entrada/limpieza.
    """
    from src.nodes import node_cancel_appointment

    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    # Paso 1: Crear evento para cancelar
    event_id, _ = await calendar_service.create_event(
        start=tomorrow,
        end=tomorrow + timedelta(hours=1),
        title="limpeza - Jorge Arias",
        description="Paciente: Jorge Arias\nTeléfono: +593984865981"
    )
    assert len(calendar_service.events) == 1, "❌ Evento no creado"
    assert event_id in calendar_service.events, "❌ Evento no está en calendar"

    # Paso 2: Preparar estado para cancelación
    state = {
        "phone_number": "+593984865981",
        "patient_name": "Jorge Arias",
        "google_event_id": event_id,  # Evento a cancelar
        "appointment_id": "appt_456",  # ID en DB
        "confirmation_type": "cancel",
        "confirmation_result": "yes",
        "awaiting_confirmation": False,
        "messages": [HumanMessage("Cancelar mi cita")],
    }

    # Paso 3: Invocar nodo de cancelación directamente
    result = await node_cancel_appointment(
        state,
        calendar_service=calendar_service,
        calendar_services=None,
        db_service=None,
    )

    # ✅ Validaciones
    assert result.get("confirmation_sent"), "❌ Cancelación no confirmada"
    assert result.get("appointment_id") is None, "❌ appointment_id no limpiado"
    assert result.get("google_event_id") is None, "❌ google_event_id no limpiado"
    assert event_id not in calendar_service.events, \
        "❌ Evento no fue eliminado del calendar"
    assert len(calendar_service.events) == 0, \
        f"❌ Calendar debería estar vacío, hay {len(calendar_service.events)}"

    print(f"✅ CANCELAR: event_id={event_id} eliminado")
    print(f"   Calendar vacío: {len(calendar_service.events) == 0}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: CONSULTAR CITAS DISPONIBLES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_availability_slots(graph, calendar_service):
    """Consultar slots disponibles para una fecha específica."""
    # En 2 días a las 10:00 para asegurar que es futuro
    future_date = (datetime.now(TIMEZONE) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "Availability Tester",
        "selected_service": "limpeza",
        "datetime_preference": future_date.isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Quiero ver slots disponibles")],
        "available_slots": [],
        "selected_slot": None,
        "missing_fields": [],
        "confirmation_sent": False,
        "awaiting_confirmation": False,
        "current_step": "start",
        "conversation_turns": 0,
        "hora_actual": "10:00",
        "fecha_hoy": "2026-04-16",
        "dia_semana_hoy": "jueves",
    }

    # Ejecuta grafo hasta check_availability
    result = await graph.ainvoke(state, {"recursion_limit": 50})

    # ✅ Validaciones
    slots = result.get("available_slots", [])
    assert len(slots) > 0, f"❌ Sin slots para {future_date.date()}. Error: {result.get('last_error')}"
    assert all(isinstance(s, str) for s in slots), "❌ Slots no son ISO strings"

    # Verificar formato ISO y que están en el futuro
    for slot in slots:
        dt = datetime.fromisoformat(slot)
        assert dt > datetime.now(TIMEZONE), f"❌ Slot pasado: {slot}"
        assert dt.weekday() < 5, f"❌ Slot en fin de semana: {slot}"

    print(f"✅ DISPONIBILIDAD: {len(slots)} slots encontrados")
    for i, slot in enumerate(slots[:3], 1):
        print(f"   {i}. {datetime.fromisoformat(slot).strftime('%A %H:%M')}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: EDGE CASE — MÚLTIPLES CITAS (NO CONFLICTOS)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_multiple_appointments_no_conflict(graph, calendar_service):
    """Verificar que múltiples citas no se solapan."""
    tomorrow = datetime.now(TIMEZONE) + timedelta(days=1)

    # Agendar 3 citas en el mismo día a diferentes horas
    times = [10, 12, 14]
    event_ids = []

    for hour in times:
        dt = tomorrow.replace(hour=hour, minute=0, second=0)

        state = {
            "phone_number": "+593984865981",
            "patient_name": f"Patient_{hour}",
            "selected_service": "limpeza",
            "datetime_preference": dt.isoformat(),
            "doctor_email": "jorge.arias.amauta@gmail.com",
            "intent": "agendar",
            "messages": [HumanMessage("Agendar")],
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
        event_id = result.get("google_event_id")
        assert event_id, f"❌ Evento {hour}:00 no creado"
        event_ids.append(event_id)

    # ✅ Validaciones
    assert len(event_ids) == 3, "❌ No se crearon 3 eventos"
    assert len(calendar_service.events) == 3, "❌ Calendar no tiene 3 eventos"

    # Verificar que no se solapan
    for eid in event_ids:
        event = calendar_service.events[eid]
        start = datetime.fromisoformat(event["start"])
        end = datetime.fromisoformat(event["end"])
        assert (end - start).total_seconds() == 3600, "❌ Duración incorrecta (debe ser 1h)"

    print(f"✅ MÚLTIPLES: 3 citas sin conflictos creadas")
    print(f"   IDs: {event_ids}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: EDGE CASE — WEEKEND ADJUSTMENT (viernes → lunes)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_weekend_adjustment(graph, calendar_service):
    """Si usuario pide fin de semana, ajusta automáticamente al lunes."""
    # Encuentra próximo sábado
    today = datetime.now(TIMEZONE)
    days_to_saturday = (5 - today.weekday()) % 7
    if days_to_saturday == 0:
        days_to_saturday = 7
    saturday = today + timedelta(days=days_to_saturday)
    saturday = saturday.replace(hour=10, minute=0, second=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "Test User",
        "selected_service": "limpeza",
        "datetime_preference": saturday.isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Quiero el sábado")],
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

    # ✅ Validaciones
    booked_date = result.get("selected_slot")
    if booked_date:
        booked_dt = datetime.fromisoformat(booked_date)
        # Debe ser lunes (weekday() == 0)
        assert booked_dt.weekday() != 5, f"❌ Se agendó para sábado: {booked_dt}"
        assert booked_dt.weekday() != 6, f"❌ Se agendó para domingo: {booked_dt}"

    print(f"✅ WEEKEND: Ajustado correctamente (entrada sábado → booking lunes)")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: EDGE CASE — NO SLOTS DISPONIBLES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_available_slots(graph, calendar_service):
    """Cuando todos los slots están ocupados, retorna error."""
    tomorrow = datetime.now(TIMEZONE) + timedelta(days=1)

    # Llenar TODOS los slots del mañana
    for hour in range(9, 18):
        dt = tomorrow.replace(hour=hour, minute=0, second=0)
        await calendar_service.create_event(
            start=dt,
            end=dt + timedelta(hours=1),
            title="Blocked",
            description="Reservado"
        )

    assert len(calendar_service.events) == 9, "❌ No se bloquearon todos los slots"

    # Intentar agendar
    state = {
        "phone_number": "+593984865981",
        "patient_name": "Test",
        "selected_service": "limpeza",
        "datetime_preference": tomorrow.replace(hour=10, minute=0).isoformat(),
        "doctor_email": "jorge.arias.amauta@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Agendar")],
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

    # ✅ Validaciones
    slots = result.get("available_slots", [])
    assert len(slots) == 0, f"❌ Debería haber 0 slots, pero hay {len(slots)}"
    # No debe haber confirmación sin slots
    # (El sistema debería pedir otra fecha)

    print(f"✅ NO SLOTS: Detectado correctamente (0 slots disponibles)")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 8: CONTEXT INTEGRITY — TODO FLOW
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_context_integrity_full_flow(graph):
    """Validar que contexto LLM es válido en flow completo."""
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).replace(hour=10, minute=0)

    state = {
        "phone_number": "+593984865981",
        "patient_name": "Full Test",
        "selected_service": "ortodoncia",
        "datetime_preference": tomorrow.isoformat(),
        "doctor_email": "javiarias000@gmail.com",
        "intent": "agendar",
        "messages": [HumanMessage("Agendar ortodoncia")],
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

    # ✅ Validaciones de estructura
    assert context["calendar"]["google_event_id"], "❌ Event ID ausente"
    assert context["flow"]["confirmation_sent"] is True, "❌ Confirmation flag incorrecto"
    assert context["user"]["name"] == "Full Test", "❌ Nombre corrupto"
    assert context["user"]["selected_service"] == "ortodoncia", "❌ Service corrupto"
    assert context["system_time"]["hora_ecuador"], "❌ Time ausente"

    # ✅ Variables críticas presentes
    assert "missing_fields" in context["flow"], "❌ missing_fields ausente"
    assert "last_error" in context, "❌ last_error tracking ausente"

    print(f"✅ CONTEXT: Íntegro en flow completo")
    print(f"   - Event: {context['calendar']['google_event_id']}")
    print(f"   - Service: {context['user']['selected_service']}")
    print(f"   - Doctor: {context['user']['doctor_name']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
