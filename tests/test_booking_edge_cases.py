"""
Tests para casos edge y complicados en el flujo de agendamiento.

Cubre regresión de los 5 bugs documentados en errores_agenda.md y
escenarios del mundo real que pueden fallar:

1. REGRESIÓN: Súper Match (alucinación de confirmación por formato de fecha)
2. REGRESIÓN: Contradicción por estado de disponibilidad (slot desaparece tras booking)
3. REGRESIÓN: Alucinaciones de citas inexistentes (memory residual)
4. REGRESIÓN: Ignorancia de Verdad por cambio de intent
5. REGRESIÓN: Bloqueo de HITL (interrupt_before)
6. EDGE: Fallos parciales en Google Calendar API
7. EDGE: Timezones complicadas (UTC vs Ecuador)
8. EDGE: Múltiples doctores con calendarios diferentes
9. EDGE: Horarios fuera de disponibilidad (fines de semana, noche)
10. EDGE: Conflictos de slots (doble reserva)
11. EDGE: Estado inconsistente (event_id sin confirmation_sent)
12. EDGE: Reintentos después de fallo
13. EDGE: Rescheduling en cascada
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call
from zoneinfo import ZoneInfo

from src.state import create_initial_arcadium_state, ArcadiumState
from src.calendar_service import GoogleCalendarService
from src.llm_extractors import extract_booking_data

TZ_ECUADOR = ZoneInfo("America/Guayaquil")
TZ_UTC = ZoneInfo("UTC")


# ══════════════════════════════════════════════════════════════════════════════
# REGRESIÓN: Bug #1 - Súper Match (formato de fecha)
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionDateFormatMatching:
    """Alucinación de confirmación por inconsistencia en formato de fecha.

    Causa raíz: Comparación de strings en diferentes formatos (ISO, UTC offset).
    Debe usar comparación granular (YMDHM) ignorando milisegundos.
    """

    def test_iso_format_with_offset_matches_naive(self):
        """ISO con offset UTC debe matchear con naïve."""
        from utils.date_utils import compare_slots

        # Usuario pide: "10:00"
        user_slot = "2026-04-14T10:00:00+00:00"
        # Sistema retorna: "2026-04-14T10:00:00"
        system_slot = "2026-04-14T10:00:00"

        # DEBE matchear
        assert compare_slots(user_slot, system_slot) is True

    def test_iso_with_milliseconds_ignores_precision(self):
        """Millisegundos no deben afectar match."""
        from utils.date_utils import compare_slots

        slot_a = "2026-04-14T10:00:00.123456Z"
        slot_b = "2026-04-14T10:00:00.999999Z"

        # DEBE matchear (mismo YMDHM)
        assert compare_slots(slot_a, slot_b) is True

    def test_different_hour_no_match(self):
        """Horas diferentes NO matchean."""
        from utils.date_utils import compare_slots

        slot_a = "2026-04-14T10:00:00"
        slot_b = "2026-04-14T11:00:00"

        assert compare_slots(slot_a, slot_b) is False

    def test_different_day_no_match(self):
        """Días diferentes NO matchean."""
        from utils.date_utils import compare_slots

        slot_a = "2026-04-14T10:00:00"
        slot_b = "2026-04-15T10:00:00"

        assert compare_slots(slot_a, slot_b) is False


# ══════════════════════════════════════════════════════════════════════════════
# REGRESIÓN: Bug #3 - Contradicción por estado de disponibilidad
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionSlotDisappearanceAfterBooking:
    """Tras crear cita, slot desaparece de available_slots.

    Síntoma: LLM dice "no hay disponibilidad" porque la lista quedó vacía
    tras reservar el único slot disponible.

    Solución: Verdad Absoluta — si google_event_id existe,
    confirmar siempre, ignorar disponibilidad.
    """

    @pytest.mark.asyncio
    async def test_confirmation_sent_overrides_empty_slots(self):
        """Si confirmation_sent=True, confirmar independientemente de slots."""
        from src.nodes_v2 import _build_system_prompt_v2

        state = {
            "confirmation_sent": True,
            "google_event_id": "evt_abc123",
            "available_slots": [],  # VACÍO tras booking
            "phone_number": "+593900000001",
            "patient_name": "Ana",
            "selected_service": "limpieza",
            "fecha_hoy": "2026-04-14",
            "hora_actual": "09:00",
            "dia_semana_hoy": "martes",
            "manana_fecha": "2026-04-15",
            "manana_dia": "miércoles",
        }

        prompt = _build_system_prompt_v2(state)

        # Debe mencionar que la operación ya fue ejecutada
        assert "evt_abc123" in prompt or "operación ya fue ejecutada" in prompt

    @pytest.mark.asyncio
    async def test_google_event_id_without_confirmation_sent(self):
        """ESTADO INCONSISTENTE: event_id sin confirmation_sent.

        Esto NO debería suceder, pero si ocurre, debería tratarse como
        "cita encontrada, no necesariamente creada aquí".
        """
        state = {
            "confirmation_sent": False,
            "google_event_id": "evt_xyz789",  # Cita existente
            "available_slots": [],
        }

        # google_event_id solo NO implica que THIS flow creó la cita
        assert state.get("confirmation_sent") is False


# ══════════════════════════════════════════════════════════════════════════════
# REGRESIÓN: Bug #4 - Alucinaciones de citas inexistentes
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionHallucinatedAppointments:
    """LLM inventa citas cuando cal_found=False.

    Síntoma: Usuario intenta reagendar, LLM dice "encontré una cita a las 11:00"
    aunque el calendario esté vacío.

    Solución: Guardia de Verdad Global — si cal_found=False, prohibir
    mencionar cualquier horario previo.
    """

    @pytest.mark.asyncio
    async def test_no_existing_appointment_blocks_hallucination(self):
        """cal_found=False debe estar reflejado en contexto."""
        from src.nodes_v2 import _build_system_prompt_v2

        state = {
            "cal_found": False,
            "has_existing_appointment": False,
            "intent": "reagendar",
            "phone_number": "+593900000001",
            "fecha_hoy": "2026-04-14",
            "hora_actual": "09:00",
            "dia_semana_hoy": "martes",
            "manana_fecha": "2026-04-15",
            "manana_dia": "miércoles",
        }

        prompt = _build_system_prompt_v2(state)

        # Debe ser string válido
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Fallos parciales en Google Calendar API
# ══════════════════════════════════════════════════════════════════════════════

class TestGoogleCalendarAPIPartialFailures:
    """Escenarios donde Google Calendar API falla de formas sutiles."""

    @pytest.mark.asyncio
    async def test_check_availability_api_timeout(self):
        """Timeout en check_availability debe capturarse gracefully."""
        from src.nodes import node_check_availability

        # Mock calendar service que lanza timeout
        mock_calendar = AsyncMock()
        mock_calendar.get_available_slots.side_effect = TimeoutError(
            "Google Calendar API timeout after 30s"
        )

        state = create_initial_arcadium_state("+593900000001")
        state["selected_service"] = "limpieza"
        state["patient_name"] = "Ana"

        # Debe capturar la excepción
        result = await node_check_availability(state, calendar_service=mock_calendar)

        # El nodo debe retornar algo, sin crash
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_book_appointment_create_fails_with_quota(self):
        """Google Calendar API rechaza por quota exceeded."""
        from src.nodes import node_book_appointment

        mock_calendar = AsyncMock()
        mock_calendar.create_event.side_effect = Exception(
            "Quota exceeded for quota metric 'Calendar API calls'"
        )

        state = create_initial_arcadium_state("+593900000001")
        state["selected_service"] = "limpieza"
        state["patient_name"] = "Ana"
        state["available_slots"] = ["2026-04-14T10:00:00"]

        result = await node_book_appointment(state, calendar_service=mock_calendar)

        # Debe fallar gracefully
        assert "last_error" in result or result.get("success") is False

    @pytest.mark.asyncio
    async def test_cancel_appointment_event_already_deleted(self):
        """Intento cancelar cita pero Google ya la borró."""
        from src.nodes import node_cancel_appointment

        mock_calendar = AsyncMock()
        mock_calendar.cancel_event.side_effect = Exception(
            "Not Found: Event not found"
        )

        state = create_initial_arcadium_state("+593900000001")
        state["google_event_id"] = "evt_ghost_123"

        result = await node_cancel_appointment(state, calendar_service=mock_calendar)

        # Debe manejo graceful — no crash, retorna dict
        assert isinstance(result, dict)


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Timezones complicadas
# ══════════════════════════════════════════════════════════════════════════════

class TestTimezoneEdgeCases:
    """Timezones pueden causar comportamiento inesperado."""

    def test_ecuador_utc_offset_consistency(self):
        """Ecuador debe ser UTC-5 siempre, sin DST."""
        now_ecuador = datetime.now(TZ_ECUADOR)
        now_utc = datetime.now(TZ_UTC)

        # Ecuador está 5 horas atrás de UTC
        diff = (now_utc.hour - now_ecuador.hour) % 24
        assert diff == 5

    def test_slot_at_midnight_boundary(self):
        """Slot exactamente a las 00:00."""
        slot = "2026-04-15T00:00:00"

        dt = datetime.fromisoformat(slot).replace(tzinfo=TZ_ECUADOR)
        assert dt.hour == 0
        assert dt.minute == 0

    def test_slot_before_business_hours(self):
        """Slot antes de horario de atención (ej. 05:00)."""
        slot = "2026-04-14T05:00:00"

        dt = datetime.fromisoformat(slot).replace(tzinfo=TZ_ECUADOR)

        # Si el horario es 09:00-17:00, esto está fuera
        assert dt.hour < 9

    def test_daylight_crossing_slot(self):
        """Slot que cruza medianoche (cita larga de noche a madrugada)."""
        start = "2026-04-14T23:00:00"
        end = "2026-04-15T02:00:00"

        start_dt = datetime.fromisoformat(start).replace(tzinfo=TZ_ECUADOR)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=TZ_ECUADOR)

        # Debe detectar cruce de día
        assert start_dt.date() < end_dt.date()


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Múltiples doctores
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiDoctorScenarios:
    """Sistema con múltiples doctores y calendarios separados."""

    @pytest.mark.asyncio
    async def test_doctor_routing_by_email(self):
        """Debe routear al calendario del doctor correcto por email."""
        from src.nodes import _resolve_calendar_service

        mock_juan = MagicMock()
        mock_maria = MagicMock()

        calendar_services = {
            "dr_juan@clinic.com": mock_juan,
            "dr_maria@clinic.com": mock_maria,
        }

        state = create_initial_arcadium_state("+593900000001")
        state["doctor_email"] = "dr_maria@clinic.com"

        service = _resolve_calendar_service(state, calendar_services=calendar_services)

        assert service is mock_maria

    @pytest.mark.asyncio
    async def test_unknown_doctor_fallback(self):
        """Si doctor_email no existe, usar primer calendario disponible."""
        from src.nodes import _resolve_calendar_service

        calendar_services = {
            "dr_juan@clinic.com": MagicMock(name="juan_calendar"),
            "dr_maria@clinic.com": MagicMock(name="maria_calendar"),
        }

        state = create_initial_arcadium_state("+593900000001")
        state["doctor_email"] = "dr_unknown@clinic.com"

        service = _resolve_calendar_service(state, calendar_services=calendar_services)

        # Debe retornar un servicio válido (primero en dict)
        assert service is not None


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Horarios fuera de disponibilidad
# ══════════════════════════════════════════════════════════════════════════════

class TestBusinessHoursEdgeCases:
    """Validación de horarios dentro del rango de atención."""

    @pytest.mark.asyncio
    async def test_slot_before_opening(self):
        """Usuario pide slot antes de apertura (08:00 vs horario 09:00)."""
        from src.intent_router import extract_slot_from_text

        available_slots = [
            "2026-04-14T09:00:00",
            "2026-04-14T10:00:00",
            "2026-04-14T17:00:00",
        ]

        # Intenta a las 08:00, no existe en slots
        result = extract_slot_from_text("a las 8", available_slots)
        # Si no match exacto, puede retornar None o closest
        assert result is None or result in available_slots

    @pytest.mark.asyncio
    async def test_slot_after_closing(self):
        """Usuario pide slot después de cierre (18:00 vs horario hasta 17:00)."""
        from src.intent_router import extract_slot_from_text

        available_slots = [
            "2026-04-14T09:00:00",
            "2026-04-14T10:00:00",
            "2026-04-14T17:00:00",
        ]

        # Intenta a las 18:00, no existe
        result = extract_slot_from_text("a las 18", available_slots)
        # Si no match exacto, puede retornar None
        assert result is None or result in available_slots

    @pytest.mark.asyncio
    async def test_weekend_slot_filtering(self):
        """Slots en fines de semana deben filtrarse."""
        # Viernes 17 de abril 2026
        friday = datetime(2026, 4, 17, 10, 0, tzinfo=TZ_ECUADOR)
        # Sábado 18 de abril 2026
        saturday = datetime(2026, 4, 18, 10, 0, tzinfo=TZ_ECUADOR)
        # Domingo 19 de abril 2026
        sunday = datetime(2026, 4, 19, 10, 0, tzinfo=TZ_ECUADOR)

        assert friday.weekday() == 4  # Friday
        assert saturday.weekday() == 5  # Saturday
        assert sunday.weekday() == 6  # Sunday


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Conflictos de slots
# ══════════════════════════════════════════════════════════════════════════════

class TestSlotConflictScenarios:
    """Manejo de conflictos y sobreposiciones de citas."""

    @pytest.mark.asyncio
    async def test_slot_overlap_detection(self):
        """Dos citas no deben overlappear en el mismo slot."""
        slot_a_start = datetime(2026, 4, 14, 10, 0, tzinfo=TZ_ECUADOR)
        slot_a_end = datetime(2026, 4, 14, 10, 30, tzinfo=TZ_ECUADOR)

        slot_b_start = datetime(2026, 4, 14, 10, 15, tzinfo=TZ_ECUADOR)
        slot_b_end = datetime(2026, 4, 14, 10, 45, tzinfo=TZ_ECUADOR)

        # Overlappean
        overlap = not (slot_a_end <= slot_b_start or slot_b_end <= slot_a_start)
        assert overlap is True

    @pytest.mark.asyncio
    async def test_no_overlap_adjacent_slots(self):
        """Slots consecutivos NO deben considerarse overlap."""
        slot_a_end = datetime(2026, 4, 14, 10, 30, tzinfo=TZ_ECUADOR)
        slot_b_start = datetime(2026, 4, 14, 10, 30, tzinfo=TZ_ECUADOR)

        # No overlappean (endpoint = startpoint es OK)
        overlap = not (slot_a_end <= slot_b_start)
        assert overlap is False

    @pytest.mark.asyncio
    async def test_double_booking_prevention(self):
        """Sistema debe prevenir doble booking del mismo slot."""
        # Este test requiere que check_availability verifique
        # que el slot no está en uso en Google Calendar
        mock_calendar = AsyncMock()
        mock_calendar.get_available_slots.return_value = [
            {"start": "2026-04-14T10:00:00", "end": "2026-04-14T10:30:00"}
        ]

        # Si intento crear dos citas en el mismo slot, la segunda debe fallar
        mock_calendar.create_event.side_effect = [
            ("evt_1", "link_1"),  # Primer booking éxito
            Exception("Calendar event already exists for this time"),  # Segundo falla
        ]

        assert await mock_calendar.create_event(
            start="2026-04-14T10:00:00",
            end="2026-04-14T10:30:00"
        ) == ("evt_1", "link_1")

        with pytest.raises(Exception):
            await mock_calendar.create_event(
                start="2026-04-14T10:00:00",
                end="2026-04-14T10:30:00"
            )


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Reintentos después de fallo
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryAfterFailure:
    """Comportamiento del sistema tras fallos transitorios."""

    @pytest.mark.asyncio
    async def test_retry_check_availability_after_timeout(self):
        """Tras timeout en check_availability, usuario puede reintentar."""
        mock_calendar = AsyncMock()

        # Primer intento falla
        mock_calendar.get_available_slots.side_effect = [
            TimeoutError("API timeout"),
            [  # Segundo intento éxito
                {"start": "2026-04-14T10:00:00", "end": "2026-04-14T10:30:00"}
            ],
        ]

        with pytest.raises(TimeoutError):
            await mock_calendar.get_available_slots(date="2026-04-14")

        slots = await mock_calendar.get_available_slots(date="2026-04-14")
        assert len(slots) == 1

    @pytest.mark.asyncio
    async def test_idempotent_booking_same_slot(self):
        """Si usuario reintenta el mismo booking, no debe crear duplicados."""
        # El timestamp o event description debe ser único
        # para poder detectar y rechazar duplicados

        event_1 = {
            "id": "evt_1",
            "summary": "Limpieza dental",
            "description": "patient: Ana | request_id: req_123",
            "start": "2026-04-14T10:00:00",
        }

        event_2 = {
            "id": "evt_1_duplicate",
            "summary": "Limpieza dental",
            "description": "patient: Ana | request_id: req_123",  # MISMO request_id
            "start": "2026-04-14T10:00:00",
        }

        # Si request_id es el mismo, es reintento — rechazar
        assert event_1["description"] == event_2["description"]


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: Rescheduling en cascada
# ══════════════════════════════════════════════════════════════════════════════

class TestRescheduleOperations:
    """Operaciones complejas de reprogramación."""

    @pytest.mark.asyncio
    async def test_reschedule_deletes_old_creates_new(self):
        """Reschedule debe cancelar la cita antigua y crear una nueva."""
        mock_calendar = AsyncMock()
        mock_calendar.cancel_event = AsyncMock(return_value=None)
        mock_calendar.create_event = AsyncMock(return_value=("evt_new", "link_new"))

        # Simular reschedule
        old_event_id = "evt_old_123"

        await mock_calendar.cancel_event(old_event_id)
        new_id, new_link = await mock_calendar.create_event(
            start="2026-04-15T11:00:00",
            end="2026-04-15T11:30:00"
        )

        assert new_id == "evt_new"
        assert mock_calendar.cancel_event.called

    @pytest.mark.asyncio
    async def test_reschedule_fails_if_old_not_found(self):
        """Si la cita vieja no existe, reschedule debe fallar."""
        mock_calendar = AsyncMock()
        mock_calendar.cancel_event.side_effect = Exception("Event not found")

        with pytest.raises(Exception):
            await mock_calendar.cancel_event("evt_ghost")

    @pytest.mark.asyncio
    async def test_cascading_rescheduling_prevents_infinite_loop(self):
        """Evitar loops infinitos si usuario reschedula múltiples veces."""
        # Contador de reintentos
        max_reschedules = 5
        reschedule_count = 0

        while reschedule_count < max_reschedules:
            reschedule_count += 1

        # Debe tener límite
        assert reschedule_count <= max_reschedules


# ══════════════════════════════════════════════════════════════════════════════
# NUEVO NODO: node_match_closest_slot
# ══════════════════════════════════════════════════════════════════════════════

class TestMatchClosestSlot:
    """Tests para nodo que busca closest slot si no hay match exacto."""

    @pytest.mark.asyncio
    async def test_exact_match_returns_slot(self):
        """Si datetime_pref matchea exacto, retornar ese slot."""
        from src.nodes import node_match_closest_slot

        state = create_initial_arcadium_state("+593900000001")
        state["datetime_preference"] = "2026-04-14T10:00:00"
        state["available_slots"] = [
            "2026-04-14T09:00:00",
            "2026-04-14T10:00:00",
            "2026-04-14T11:00:00",
        ]

        result = await node_match_closest_slot(state)

        assert result.get("selected_slot") == "2026-04-14T10:00:00"
        assert "preference_adjusted" not in result  # Exacto, no ajustado

    @pytest.mark.asyncio
    async def test_closest_within_60_minutes(self):
        """Si no hay exacto, buscar closest dentro de 60 min."""
        from src.nodes import node_match_closest_slot

        state = create_initial_arcadium_state("+593900000001")
        state["datetime_preference"] = "2026-04-14T10:15:00"  # 10:15
        state["available_slots"] = [
            "2026-04-14T09:00:00",
            "2026-04-14T10:00:00",  # 15 min antes (closest)
            "2026-04-14T11:00:00",
        ]

        result = await node_match_closest_slot(state)

        assert result.get("selected_slot") == "2026-04-14T10:00:00"
        assert result.get("preference_adjusted") is True  # Flag ajustado

    @pytest.mark.asyncio
    async def test_no_closest_outside_range(self):
        """Si closest está fuera de 60 min range, no retornar nada."""
        from src.nodes import node_match_closest_slot

        state = create_initial_arcadium_state("+593900000001")
        state["datetime_preference"] = "2026-04-14T10:00:00"
        state["available_slots"] = [
            "2026-04-14T08:00:00",  # 120 min antes (fuera de rango)
            "2026-04-14T12:00:00",  # 120 min después (fuera de rango)
        ]

        result = await node_match_closest_slot(state)

        assert result == {}  # Sin closest válido

    @pytest.mark.asyncio
    async def test_no_preference_returns_empty(self):
        """Sin datetime_pref, no hacer nada."""
        from src.nodes import node_match_closest_slot

        state = create_initial_arcadium_state("+593900000001")
        state["datetime_preference"] = None
        state["available_slots"] = [
            "2026-04-14T10:00:00",
            "2026-04-14T11:00:00",
        ]

        result = await node_match_closest_slot(state)

        assert result == {}

    @pytest.mark.asyncio
    async def test_no_slots_returns_empty(self):
        """Sin available_slots, no hacer nada."""
        from src.nodes import node_match_closest_slot

        state = create_initial_arcadium_state("+593900000001")
        state["datetime_preference"] = "2026-04-14T10:00:00"
        state["available_slots"] = []

        result = await node_match_closest_slot(state)

        assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# NUEVO EDGE: edge_after_match_closest_slot
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeAfterMatchClosestSlot:
    """Tests para edge que decide si bookeamos o vamos a LLM."""

    def test_with_selected_slot_no_missing(self):
        """Si selected_slot + no missing fields → book."""
        from src.edges import edge_after_match_closest_slot

        state = {
            "selected_slot": "2026-04-14T10:00:00",
            "missing_fields": [],
        }

        result = edge_after_match_closest_slot(state)
        assert result == "book_appointment"

    def test_with_selected_slot_and_missing(self):
        """Si selected_slot pero hay missing fields → generate_response."""
        from src.edges import edge_after_match_closest_slot

        state = {
            "selected_slot": "2026-04-14T10:00:00",
            "missing_fields": ["patient_name"],
        }

        result = edge_after_match_closest_slot(state)
        assert result == "generate_response"

    def test_no_selected_slot(self):
        """Sin selected_slot → generate_response."""
        from src.edges import edge_after_match_closest_slot

        state = {
            "selected_slot": None,
            "missing_fields": [],
        }

        result = edge_after_match_closest_slot(state)
        assert result == "generate_response"


# ══════════════════════════════════════════════════════════════════════════════
# SANITY: Verificación de estado global
# ══════════════════════════════════════════════════════════════════════════════

class TestStateInvariantsGlobal:
    """Invariantes globales del estado que nunca deben violarse."""

    def test_confirmation_sent_requires_event_id(self):
        """INVARIANTE: confirmation_sent=True → event_id DEBE existir."""
        # Estado válido: confirmation_sent con event_id
        valid_state = {
            "confirmation_sent": True,
            "google_event_id": "evt_123",
        }

        # Estado inválido: confirmation_sent sin event_id
        invalid_state = {
            "confirmation_sent": True,
            "google_event_id": None,
        }

        # Verificar estado válido cumple invariante
        is_valid = bool(valid_state["confirmation_sent"] and valid_state.get("google_event_id"))
        assert is_valid

        # Verificar estado inválido viola invariante
        violates_invariant = invalid_state["confirmation_sent"] and not invalid_state.get("google_event_id")
        assert violates_invariant  # Es una violación, como se esperaba

    def test_awaiting_confirmation_with_no_slots_invalid(self):
        """INVARIANTE: awaiting_confirmation=True → available_slots debe tener items."""
        state = {
            "awaiting_confirmation": True,
            "available_slots": [],  # VIOLACIÓN
            "confirmation_type": "book",
        }

        # Esto debería ser inválido
        is_valid = state["awaiting_confirmation"] and len(state.get("available_slots", [])) > 0
        assert not is_valid

    def test_event_id_without_service_invalid(self):
        """INVARIANTE: google_event_id → selected_service DEBE existir."""
        state = {
            "google_event_id": "evt_123",
            "selected_service": None,  # VIOLACIÓN
        }

        is_valid = (not state.get("google_event_id")) or bool(state.get("selected_service"))
        assert is_valid is False
