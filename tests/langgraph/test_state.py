"""Tests para src/state.py — sin LLM, puramente deterministas."""

import pytest
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from src.state import (
    ArcadiumState,
    create_initial_arcadium_state,
    route_by_keywords,
    detect_confirmation,
    extract_slot_from_text,
    get_missing_fields,
    is_weekend_adjusted,
    VALID_SERVICES,
    INTENT_KEYWORDS,
)


class TestCreateInitialState:
    def test_creates_with_phone(self):
        state = create_initial_arcadium_state("+593999999999")
        assert state["phone_number"] == "+593999999999"
        assert state["project_id"] is None
        assert state["conversation_turns"] == 0
        assert state["fecha_hoy"]  # debe tener fecha
        assert state["hora_actual"]
        assert state["missing_fields"]  # debe tener campos faltantes

    def test_dates_are_valid(self):
        state = create_initial_arcadium_state("+593999999999")
        # Fecha hoy debe ser parseable
        datetime.fromisoformat(state["fecha_hoy"])
        datetime.fromisoformat(state["manana_fecha"])


class TestRouteByKeywords:
    def test_agendar_direct_keywords(self):
        assert route_by_keywords("quiero agendar una cita") == "agendar"

    def test_cancelar_keywords(self):
        assert route_by_keywords("necesito cancelar mi cita") == "cancelar"

    def test_reagendar_keywords(self):
        assert route_by_keywords("puedo cambiar la fecha de mi cita?") == "reagendar"

    def test_consultar_keywords(self):
        assert route_by_keywords("hay horarios disponibles mañana?") == "consultar"

    def test_no_keywords_in_greeting(self):
        assert route_by_keywords("hola buen día") is None

    def test_dolor_implied_agendar(self):
        assert route_by_keywords("me duele mucho una muela") == "agendar"


class TestDetectConfirmation:
    def test_yes(self):
        assert detect_confirmation("sí") == "yes"
        assert detect_confirmation("dale") == "yes"
        assert detect_confirmation("confirmo") == "yes"
        assert detect_confirmation("ok") == "yes"

    def test_no(self):
        assert detect_confirmation("no") == "no"
        assert detect_confirmation("mejor no") == "no"

    def test_slot_choice(self):
        assert detect_confirmation("a las 10:00") == "slot_choice"
        assert detect_confirmation("prefiero las 3") == "slot_choice"

    def test_unknown(self):
        assert detect_confirmation("puede explicarme mejor") == "unknown"


class TestExtractSlotFromText:
    def test_finds_exact_match(self):
        slots = ["2026-04-10T10:00:00-05:00", "2026-04-10T14:30:00-05:00"]
        assert extract_slot_from_text("a las 10:00", slots) == slots[0]

    def test_no_match(self):
        slots = ["2026-04-10T10:00:00-05:00"]
        assert extract_slot_from_text("a las 8:00", slots) is None


class TestGetMissingFields:
    def test_all_missing(self):
        state = create_initial_arcadium_state("+593999999999")
        missing = get_missing_fields(state)
        assert "patient_name" in missing
        assert "selected_service" in missing
        assert "datetime_preference" in missing

    def test_partial(self):
        state = create_initial_arcadium_state("+593999999999")
        state["patient_name"] = "Juan"
        state["selected_service"] = "limpieza"
        missing = get_missing_fields(state)
        assert "patient_name" not in missing
        assert "selected_service" not in missing
        assert "datetime_preference" in missing


class TestWeekendAdjusted:
    def test_weekday_not_adjusted(self):
        adjusted, new = is_weekend_adjusted("2026-04-07T10:00")  # martes
        assert adjusted is False

    def test_saturday_adjusted(self):
        adjusted, new = is_weekend_adjusted("2026-04-04T10:00")  # sábado
        assert adjusted is True
        # 2026-04-04 es sábado → lunes 2026-04-06
        assert "2026-04-06" in new


class TestConstants:
    def test_valid_services(self):
        assert "consulta" in VALID_SERVICES
        assert VALID_SERVICES["consulta"] == 30
        assert VALID_SERVICES["limpieza"] == 45

    def test_intent_keywords(self):
        assert "agendar" in INTENT_KEYWORDS
        assert "cancelar" in INTENT_KEYWORDS
        assert "reagendar" in INTENT_KEYWORDS
        assert "consultar" in INTENT_KEYWORDS
