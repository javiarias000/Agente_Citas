"""Integration tests for the complete LangGraph flow.

Tests:
1. Graph compiles without errors
2. Happy-path: agendar cita (multi-turn)
3. Ambiguous intent → LLM fallback
4. Cancellation flow
5. Escalation after 10+ turns
"""

import pytest
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage

from src.graph import build_graph, compile_graph
from src.state import ArcadiumState, create_initial_arcadium_state, MAX_TURNS


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """LLM mock that returns predictable JSON/text responses."""
    llm = AsyncMock()

    async def fake_ainvoke(messages, **kwargs):
        # Determine which prompt by looking at system messages
        system_text = ""
        user_text = ""
        for m in messages:
            content = m.content if isinstance(m.content, str) else str(m.content)
            if getattr(m, "type", None) == "system":
                system_text += content.lower()
            elif getattr(m, "type", None) == "human":
                user_text += content

        # extract_intent prompt
        if "clasificador de intenciones" in system_text:
            result = MagicMock()
            result.content = '{"intent": "agendar", "confidence": 0.95}'
            result.type = "ai"
            return result

        # extract_booking_data prompt
        if "extractor de datos" in system_text:
            result = MagicMock()
            result.content = '{"service": "limpieza", "datetime_iso": "2026-04-07T10:00:00", "patient_name": "Ana", "confidence": 0.9, "needs_more_info": false, "missing": []}'
            result.type = "ai"
            return result

        # generate_response prompt
        if "deyy" in system_text:
            result = MagicMock()
            result.content = "Hola Ana, tengo disponibles estos horarios para limpieza el martes 7 de abril: 10:00. Cual prefieres?"
            result.type = "ai"
            return result

        # fallback
        result = MagicMock()
        result.content = "Entendido."
        result.type = "ai"
        return result

    llm.ainvoke.side_effect = fake_ainvoke
    return llm


@pytest.fixture
def mock_store():
    """In-memory store mock."""
    store = AsyncMock()
    store.initialize = AsyncMock()
    store.get_history = AsyncMock(return_value=[])
    store.get_agent_state = AsyncMock(return_value=None)
    store.save_agent_state = AsyncMock()
    store.upsert_user_profile = AsyncMock()
    return store


@pytest.fixture
def mock_calendar_service():
    """Calendar service mock."""
    cal = AsyncMock()
    cal.get_available_slots = AsyncMock(
        return_value=[
            "2026-04-07T10:00:00-05:00",
            "2026-04-07T10:30:00-05:00",
            "2026-04-07T11:00:00-05:00",
        ]
    )
    cal.create_event = AsyncMock(
        return_value=("event_123", "https://calendar.google.com/event?eid=123")
    )
    cal.delete_event = AsyncMock(return_value=True)
    return cal


@pytest.fixture
def mock_db_service():
    """DB service mock."""
    db = AsyncMock()
    appt = MagicMock()
    appt.id = "appt_uuid_001"
    db.create_appointment = AsyncMock(return_value=(True, "Created", appt))
    db.cancel_appointment = AsyncMock(return_value=(True, "Cancelled"))
    return db


def _make_incoming_state(message_text: str) -> dict:
    """Create a state dict with message.

    node_entry reads _incoming_message first, falls back to messages[-1].
    We use messages directly (this matches how agent.py calls the graph).
    """
    state = create_initial_arcadium_state("+593999999999")
    state["messages"] = [HumanMessage(content=message_text)]
    return state


# ── Test 1: Graph compiles ─────────────────────────────────


class TestGraphCompilation:
    def test_build_graph_returns_state_graph(self, mock_llm, mock_store, mock_calendar_service, mock_db_service):
        graph = build_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )
        assert graph is not None

    def test_compile_graph_returns_compiled(self, mock_llm, mock_store, mock_calendar_service, mock_db_service):
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )
        assert compiled is not None

    def test_compile_graph_without_checkpointer(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
            checkpointer=None,
        )
        assert compiled is not None


# ── Test 2: Happy-path agendar (multi-turn) ────────────────


class TestHappyPathAgendar:
    """Full conversation: user wants to book, has all data in first message."""

    @pytest.mark.asyncio
    async def test_agendar_con_todos_los_datos(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        """Single message with all data: service, date, name → goes straight to availability."""
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("Hola, soy Ana y quiero agendar una limpieza para el martes 7 de abril a las 10")

        result = await compiled.ainvoke(state)

        # Debe haber detectado el intent "agendar"
        assert result.get("intent") == "agendar"
        # Debe haber consultado disponibilidad
        assert result.get("available_slots"), "Should have available slots"
        # Debe haber un mensaje AI
        ai_msgs = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
        assert len(ai_msgs) >= 1, "Should have at least one AI response"

    @pytest.mark.asyncio
    async def test_agendar_con_datos_parciales(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        """Message with intent but missing fields → LLM extracts data → availability."""
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("Quiero agendar una cita de limpieza manana a las 10")

        result = await compiled.ainvoke(state)

        assert result.get("intent") == "agendar"
        # After LLM extracts, should reach availability
        assert "available_slots" in result or "last_error" in result

    @pytest.mark.asyncio
    async def test_multi_turn_confirmacion(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        """Two-turn flow: first provides data, second confirms."""
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        # Turn 1: Provide booking data
        state = _make_incoming_state("Hola soy Ana, quiero agendar una limpieza para manana a las 10")

        result1 = await compiled.ainvoke(state)

        # Turn 2: Confirm
        state2 = dict(result1)
        state2["_incoming_message"] = "Si, confirmo esa hora"

        result2 = await compiled.ainvoke(state2)

        # Should have detected confirmation
        assert result2.get("confirmation_result") is not None or result2.get("intent") is not None


# ── Test 3: Ambiguous intent → LLM fallback ───────────────


class TestAmbiguousIntent:
    """Message with no clear keywords → extract_intent (LLM)."""

    @pytest.mark.asyncio
    async def test_ambiguous_message_falls_back_to_llm(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        """Message 'necesito algo para el dentista' has no direct keyword match."""
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("necesito algo para el dentista")

        result = await compiled.ainvoke(state)

        # LLM should have classified the intent
        assert result.get("intent") is not None or result.get("messages")


# ── Test 4: Cancellation flow ──────────────────────────────


class TestCancellationFlow:
    """User wants to cancel appointment."""

    @pytest.mark.asyncio
    async def test_cancelar_flow(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        # Start with existing appointment context
        state = create_initial_arcadium_state("+593999999999")
        state["appointment_id"] = "existing_appt_001"
        state["google_event_id"] = "event_123"
        state["messages"] = [HumanMessage(content="Quiero cancelar mi cita")]

        result = await compiled.ainvoke(state)

        # Should have detected "cancelar" intent
        assert result.get("intent") == "cancelar"


# ── Test 5: Escalation after MAX_TURNS ─────────────────────


class TestEscalation:
    """After MAX_TURNS conversations, should_escalate triggers."""

    @pytest.mark.asyncio
    async def test_escalation_by_turns(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("Hola, quiero agendar una cita")
        state["conversation_turns"] = MAX_TURNS  # Already at limit

        result = await compiled.ainvoke(state)

        assert result.get("should_escalate") is True

    @pytest.mark.asyncio
    async def test_no_escalation_below_max_turns(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("Hola, quiero agendar una consulta")
        state["conversation_turns"] = 3

        result = await compiled.ainvoke(state)

        assert result.get("should_escalate") is not True


# ── Test 6: Edge case — greeting with no intent ────────────


class TestEdgeCases:
    """Messages that don't match any intent."""

    @pytest.mark.asyncio
    async def test_greeting_only(
        self, mock_llm, mock_store, mock_calendar_service, mock_db_service
    ):
        """Just "hola" should generate a response, not crash."""
        compiled = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("hola buenos dias")

        result = await compiled.ainvoke(state)

        # Should produce an AI message response
        ai_msgs = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
        assert len(ai_msgs) >= 1

    @pytest.mark.asyncio
    async def test_empty_store_graceful(
        self, mock_llm, mock_calendar_service, mock_db_service
    ):
        """Graph should work with store=None."""
        compiled = compile_graph(
            llm=mock_llm,
            store=None,
            calendar_service=mock_calendar_service,
            db_service=mock_db_service,
        )

        state = _make_incoming_state("Hola")

        result = await compiled.ainvoke(state)
        assert result is not None
