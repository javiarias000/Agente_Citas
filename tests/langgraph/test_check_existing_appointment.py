"""Tests for node_check_existing_appointment.

Covers all relevant scenarios:
1. No calendar service
2. No phone and no name
3. No events at all
4. Phone match identifies patient event
5. Name match only (no phone available)
6. Name match returns OTHER client's event → filtered out (critical bug regression)
7. Exact match (same service + same date) → has_existing_appointment True for agendar
8. Patient event but different service → has_existing_appointment False for agendar
9. Patient event but different date → has_existing_appointment False for agendar
10. Cancel intent: any patient event → has_existing_appointment True
11. Reagendar intent: any patient event → has_existing_appointment True
12. Exception in calendar → has_existing_appointment False, datetime_preference preserved
13. datetime_preference preserved across all return paths
14. _no_appointment_found() does NOT contain datetime_preference key
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from src.nodes import node_check_existing_appointment, _no_appointment_found
from src.state import create_initial_arcadium_state

TZ = ZoneInfo("America/Guayaquil")
PHONE = "+593099111222"
PATIENT_NAME = "Jorge Javier Arias"
SERVICE = "limpieza dental"
TOMORROW = (datetime.now(TZ) + timedelta(days=1)).replace(
    hour=10, minute=0, second=0, microsecond=0
)
TOMORROW_ISO = TOMORROW.isoformat()


def _make_state(
    phone: str = PHONE,
    patient_name: str = "",
    service: str = "",
    datetime_preference: str = "",
    intent: str = "agendar",
) -> dict:
    state = create_initial_arcadium_state(phone)
    if patient_name:
        state["patient_name"] = patient_name
    if service:
        state["selected_service"] = service
    if datetime_preference:
        state["datetime_preference"] = datetime_preference
    state["intent"] = intent
    return state


def _make_calendar_event(
    event_id: str = "evt_001",
    summary: str = f"{SERVICE} - {PATIENT_NAME}",
    phone: str = PHONE,
    start_iso: str = TOMORROW_ISO,
) -> dict:
    """Build a minimal Google Calendar event dict."""
    return {
        "id": event_id,
        "summary": summary,
        "description": f"Paciente: {PATIENT_NAME}\nTeléfono: {phone}",
        "start": {"dateTime": start_iso},
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
    }


def _make_calendar_service(
    all_events: list = None,
    name_search_events: list = None,
    available_slots: list = None,
    raise_on_list: bool = False,
) -> AsyncMock:
    cal = AsyncMock()
    if raise_on_list:
        cal.list_events = AsyncMock(side_effect=Exception("Calendar API error"))
    else:
        cal.list_events = AsyncMock(return_value=all_events or [])
    cal.search_events_by_query = AsyncMock(return_value=name_search_events or [])
    cal.get_available_slots = AsyncMock(return_value=available_slots or [])
    return cal


# ─────────────────────────────────────────────────────────────
# 1. No calendar service
# ─────────────────────────────────────────────────────────────
class TestNoCalendarService:
    @pytest.mark.asyncio
    async def test_no_calendar_service_returns_false(self):
        state = _make_state()
        result = await node_check_existing_appointment(state, calendar_service=None)
        assert result["has_existing_appointment"] is False
        assert result["calendar_appointment_found"] is False


# ─────────────────────────────────────────────────────────────
# 2. No phone and no name
# ─────────────────────────────────────────────────────────────
class TestNoPhoneNoName:
    @pytest.mark.asyncio
    async def test_no_phone_no_name_returns_false(self):
        cal = _make_calendar_service()
        state = _make_state(phone="", patient_name="")
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is False


# ─────────────────────────────────────────────────────────────
# 3. No events at all
# ─────────────────────────────────────────────────────────────
class TestNoEvents:
    @pytest.mark.asyncio
    async def test_no_events_returns_false(self):
        cal = _make_calendar_service(all_events=[], name_search_events=[])
        state = _make_state(patient_name=PATIENT_NAME, service=SERVICE, datetime_preference=TOMORROW_ISO)
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is False
        assert result["calendar_appointment_found"] is False
        assert result["existing_appointments"] == []
        assert result["calendar_total_for_patient"] == 0


# ─────────────────────────────────────────────────────────────
# 4. Phone match identifies patient event
# ─────────────────────────────────────────────────────────────
class TestPhoneMatch:
    @pytest.mark.asyncio
    async def test_phone_match_finds_event_for_agendar_exact_match(self):
        event = _make_calendar_event(phone=PHONE, start_iso=TOMORROW_ISO)
        cal = _make_calendar_service(all_events=[event], name_search_events=[])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        # Event for this phone, same service, same date → exact match
        assert result["has_existing_appointment"] is True
        assert result["calendar_appointment_found"] is True

    @pytest.mark.asyncio
    async def test_phone_match_no_name_still_works(self):
        """Phone match works even without patient_name."""
        event = _make_calendar_event(phone=PHONE, start_iso=TOMORROW_ISO)
        cal = _make_calendar_service(all_events=[event], name_search_events=[])
        state = _make_state(
            phone=PHONE,
            patient_name="",  # no name
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is True


# ─────────────────────────────────────────────────────────────
# 5. Name match only (no phone available)
# ─────────────────────────────────────────────────────────────
class TestNameMatchOnly:
    @pytest.mark.asyncio
    async def test_name_match_without_phone_finds_event(self):
        """When phone is not available, name match alone can identify the patient."""
        event = _make_calendar_event(
            phone="",  # event has no phone in description
            start_iso=TOMORROW_ISO,
            summary=f"{SERVICE} - {PATIENT_NAME}",
        )
        # Override description to have the name but not the phone
        event["description"] = f"Paciente: {PATIENT_NAME}"

        cal = _make_calendar_service(all_events=[], name_search_events=[event])
        state = _make_state(
            phone="",  # no phone
            patient_name=PATIENT_NAME,
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        # No phone to filter by → name results pass through
        assert result["has_existing_appointment"] is True


# ─────────────────────────────────────────────────────────────
# 6. CRITICAL: Name match returns OTHER client's event → filtered out
# ─────────────────────────────────────────────────────────────
class TestNameMatchOtherClient:
    @pytest.mark.asyncio
    async def test_other_client_event_not_attributed(self):
        """
        Regression test for the primary bug:
        Google search_events_by_query returns events from OTHER patients
        (e.g., searching "Jorge" returns "Jorge García" whose phone is different).
        These must NOT be counted as the requesting client's appointments.
        """
        OTHER_PHONE = "+593099999999"
        other_client_event = _make_calendar_event(
            event_id="evt_other",
            summary=f"{SERVICE} - Jorge García",
            phone=OTHER_PHONE,  # different phone
            start_iso=TOMORROW_ISO,
        )

        # list_events returns nothing for this phone (correct)
        # search_events_by_query returns the other client's event (fuzzy match on "Jorge")
        cal = _make_calendar_service(
            all_events=[other_client_event],  # listed but phone won't match
            name_search_events=[other_client_event],  # name search false positive
        )

        state = _make_state(
            phone=PHONE,  # our client's phone
            patient_name="Jorge Javier Arias",
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)

        # The other client's event should NOT trigger has_existing_appointment
        assert result["has_existing_appointment"] is False, (
            "BUG: otro cliente's event was incorrectly attributed to requesting client"
        )
        assert result["calendar_appointment_found"] is False
        assert result["calendar_total_for_patient"] == 0

    @pytest.mark.asyncio
    async def test_own_event_passes_phone_filter(self):
        """After fix, own events that have the phone in description still work."""
        own_event = _make_calendar_event(
            event_id="evt_own",
            phone=PHONE,
            start_iso=TOMORROW_ISO,
        )
        cal = _make_calendar_service(
            all_events=[],
            name_search_events=[own_event],  # returned by name search
        )
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is True


# ─────────────────────────────────────────────────────────────
# 7. Exact match (same service + same date) → True for agendar
# ─────────────────────────────────────────────────────────────
class TestExactMatch:
    @pytest.mark.asyncio
    async def test_exact_match_same_service_and_date(self):
        event = _make_calendar_event(
            summary=f"limpieza dental - {PATIENT_NAME}",
            phone=PHONE,
            start_iso=TOMORROW_ISO,
        )
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service="limpieza dental",
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is True
        assert result["calendar_first_match"] is not None


# ─────────────────────────────────────────────────────────────
# 8. Patient has event but different service → False for agendar
# ─────────────────────────────────────────────────────────────
class TestDifferentService:
    @pytest.mark.asyncio
    async def test_different_service_does_not_block_booking(self):
        """Patient has a 'ortodoncia' event; they want 'limpieza' → should allow booking."""
        event = _make_calendar_event(
            summary=f"ortodoncia - {PATIENT_NAME}",
            phone=PHONE,
            start_iso=TOMORROW_ISO,
        )
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service="limpieza dental",  # different service
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        # Different service → no exact match → should not block
        assert result["has_existing_appointment"] is False


# ─────────────────────────────────────────────────────────────
# 9. Patient has event but different date → False for agendar
# ─────────────────────────────────────────────────────────────
class TestDifferentDate:
    @pytest.mark.asyncio
    async def test_different_date_does_not_block_booking(self):
        """Patient has same service but on a different day → should allow new booking."""
        other_day = (datetime.now(TZ) + timedelta(days=5)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        event = _make_calendar_event(
            summary=f"limpieza dental - {PATIENT_NAME}",
            phone=PHONE,
            start_iso=other_day.isoformat(),
        )
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service="limpieza dental",
            datetime_preference=TOMORROW_ISO,  # tomorrow, not day+5
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        # Different date → no exact match → should not block
        assert result["has_existing_appointment"] is False


# ─────────────────────────────────────────────────────────────
# 10 & 11. Cancel / Reagendar: any patient event → True
# ─────────────────────────────────────────────────────────────
class TestCancelReagendar:
    @pytest.mark.asyncio
    async def test_cancel_with_any_event_returns_true(self):
        event = _make_calendar_event(phone=PHONE, start_iso=TOMORROW_ISO)
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(phone=PHONE, patient_name=PATIENT_NAME, intent="cancelar")
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is True

    @pytest.mark.asyncio
    async def test_reagendar_with_any_event_returns_true(self):
        event = _make_calendar_event(phone=PHONE, start_iso=TOMORROW_ISO)
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(phone=PHONE, patient_name=PATIENT_NAME, intent="reagendar")
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is True

    @pytest.mark.asyncio
    async def test_cancel_no_events_returns_false(self):
        cal = _make_calendar_service(all_events=[], name_search_events=[])
        state = _make_state(phone=PHONE, patient_name=PATIENT_NAME, intent="cancelar")
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is False


# ─────────────────────────────────────────────────────────────
# 12. Exception in calendar → False + preserves datetime_preference
# ─────────────────────────────────────────────────────────────
class TestCalendarException:
    @pytest.mark.asyncio
    async def test_exception_returns_false_and_preserves_datetime(self):
        cal = _make_calendar_service(raise_on_list=True)
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result["has_existing_appointment"] is False
        # datetime_preference must be preserved so the booking flow can continue
        assert result.get("datetime_preference") == TOMORROW_ISO


# ─────────────────────────────────────────────────────────────
# 13. datetime_preference preserved across all return paths
# ─────────────────────────────────────────────────────────────
class TestDatetimePreservation:
    @pytest.mark.asyncio
    async def test_datetime_preserved_when_no_events(self):
        cal = _make_calendar_service(all_events=[], name_search_events=[])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result.get("datetime_preference") == TOMORROW_ISO

    @pytest.mark.asyncio
    async def test_datetime_preserved_when_has_existing(self):
        event = _make_calendar_event(phone=PHONE, start_iso=TOMORROW_ISO)
        cal = _make_calendar_service(all_events=[event])
        state = _make_state(
            phone=PHONE,
            patient_name=PATIENT_NAME,
            service=SERVICE,
            datetime_preference=TOMORROW_ISO,
            intent="agendar",
        )
        result = await node_check_existing_appointment(state, calendar_service=cal)
        assert result.get("datetime_preference") == TOMORROW_ISO


# ─────────────────────────────────────────────────────────────
# 14. _no_appointment_found() must NOT contain datetime_preference key
# ─────────────────────────────────────────────────────────────
class TestNoAppointmentFoundHelper:
    def test_no_appointment_found_excludes_datetime_preference(self):
        result = _no_appointment_found()
        assert "datetime_preference" not in result, (
            "_no_appointment_found() must not include datetime_preference "
            "so LangGraph does not overwrite the user's requested date"
        )

    def test_no_appointment_found_required_keys(self):
        result = _no_appointment_found()
        assert result["calendar_lookup_done"] is True
        assert result["calendar_appointment_found"] is False
        assert result["existing_appointments"] == []
        assert result["calendar_total_for_patient"] == 0
        assert result["google_event_id"] is None
        assert result["google_event_link"] is None
