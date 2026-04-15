"""Tests para src/intent_router.py — sin LLM."""

import pytest
from src.intent_router import (
    route_by_keywords,
    detect_confirmation,
    extract_slot_from_text,
    INTENT_KEYWORDS,
)


class TestRouteByKeywords:
    """Pruebas exhaustivas del enrutamiento por keywords."""

    # --- AGENDAR ---

    def test_agendar_directo(self):
        assert route_by_keywords("quiero agendar una cita") == "agendar"
        assert route_by_keywords("necesito un turno") == "agendar"
        assert route_by_keywords("me gustaría reservar") == "agendar"
        assert route_by_keywords("me duele una muela") == "agendar"

    # --- CANCELAR ---

    def test_cancelar_directo(self):
        assert route_by_keywords("quiero cancelar mi cita") == "cancelar"
        assert route_by_keywords("no puedo ir mañana") == "cancelar"
        assert route_by_keywords("anula la cita") == "cancelar"

    # --- REAGENDAR ---

    def test_reagendar_directo(self):
        assert route_by_keywords("puedo cambiar la fecha?") == "reagendar"
        assert route_by_keywords("otra fecha por favor") == "reagendar"
        assert route_by_keywords("mover cita") == "reagendar"

    # --- CONSULTAR ---

    def test_consultar_directo(self):
        assert route_by_keywords("hay horarios disponibles?") == "consultar"
        assert route_by_keywords("cuándo puedo ir?") == "consultar"
        assert route_by_keywords("quiero ver mis citas") == "consultar"

    # --- SIN MATCH ---

    def test_greeting(self):
        assert route_by_keywords("hola qué tal") is None
        assert route_by_keywords("buenas tardes") is None

    def test_no_match_ambiguous(self):
        assert route_by_keywords("gracias por todo") is None
        assert route_by_keywords("perfecto") is None


class TestDetectConfirmation:
    _YES = [
        "sí",
        "si",
        "claro",
        "confirmo",
        "dale",
        "ok",
        "va",
        "bueno",
        "perfecto",
        "de una",
        "hágale",
    ]
    _NO = [
        "no",
        "mejor no",
        "no quiero",
        "cancela",
        "olvídalo",
        "mejor luego",
        "después",
    ]
    _SLOTS = [
        "a las 10:00",
        "prefiero las 3:30",
        "el de las 14",
        "9:00 me funciona",
    ]

    @pytest.mark.parametrize("text", _YES)
    def test_yes(self, text):
        assert detect_confirmation(text) == "yes"

    @pytest.mark.parametrize("text", _NO)
    def test_no(self, text):
        assert detect_confirmation(text) == "no"

    @pytest.mark.parametrize("text", _SLOTS)
    def test_slot_choice(self, text):
        assert detect_confirmation(text) == "slot_choice"

    def test_empty_returns_unknown(self):
        assert detect_confirmation("") == "unknown"
        assert detect_confirmation("   ") == "unknown"

    def test_unknown(self):
        assert detect_confirmation("puede explicarme de nuevo?") == "unknown"


class TestExtractSlotFromText:
    def test_exact_match(self):
        slots = ["2026-04-10T10:00:00", "2026-04-10T14:30:00"]
        assert extract_slot_from_text("a las 10:00", slots) == slots[0]

    def test_partial_match(self):
        slots = ["2026-04-10T10:00:00", "2026-04-10T14:30:00"]
        assert extract_slot_from_text("el de las 14:30", slots) == slots[1]

    def test_no_match(self):
        slots = ["2026-04-10T10:00:00"]
        assert extract_slot_from_text("a las 8:00", slots) is None

    def test_empty_slots(self):
        assert extract_slot_from_text("a las 10:00", []) is None

    def test_no_time_in_text(self):
        slots = ["2026-04-10T10:00:00"]
        assert extract_slot_from_text("sí confirmo", slots) is None
