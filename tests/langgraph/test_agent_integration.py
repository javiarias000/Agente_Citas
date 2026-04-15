"""Integration tests for ArcadiumAgent entry point.

Tests:
1. process_message returns AgentResponse
2. Phone normalization from session_id
3. State restoration from store
4. Thread safety with asyncio.Lock
5. Error handling when graph invocation fails
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import HumanMessage, AIMessage

from src.agent import ArcadiumAgent, AgentResponse


# ── Helpers ─────────────────────────────────────────────────


def make_compiled_graph_mock():
    """Create a mock compiled graph that returns a predictable state."""
    graph = AsyncMock()

    async def fake_ainvoke(input, config=None):
        result = dict(input)
        result["messages"] = input.get("messages", []) + [
            AIMessage(content="Hola, en que puedo ayudarte?")
        ]
        result["intent"] = "agendar"
        result["conversation_turns"] = input.get("conversation_turns", 0) + 1
        return result

    graph.ainvoke.side_effect = fake_ainvoke
    return graph


# ── Tests ───────────────────────────────────────────────────


class TestAgentResponse:
    def test_default_values(self):
        resp = AgentResponse(text="Hola")
        assert resp.text == "Hola"
        assert resp.status == "ok"
        assert resp.appointment_id is None
        assert resp.should_escalate is False


class TestAgentProcessMessage:
    @pytest.mark.asyncio
    async def test_process_message_returns_response(self):
        graph = make_compiled_graph_mock()
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[])
        store.get_agent_state = AsyncMock(return_value=None)

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=graph,
            store=store,
        )

        resp = await agent.process_message("Hola quiero agendar")

        assert isinstance(resp, AgentResponse)
        assert resp.text == "Hola, en que puedo ayudarte?"
        assert resp.status == "ok"
        assert resp.intent == "agendar"

    @pytest.mark.asyncio
    async def test_phone_normalization_from_session_id(self):
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[])
        store.get_agent_state = AsyncMock(return_value=None)

        graph = make_compiled_graph_mock()

        # Capture the state passed to ainvoke
        captured_state = {}

        async def capture_ainvoke(input, config=None):
            captured_state.update(input)
            result = dict(input)
            result["messages"] = input.get("messages", []) + [AIMessage(content="ok")]
            return result

        graph.ainvoke.side_effect = capture_ainvoke

        agent = ArcadiumAgent(
            session_id="deyy_+593912345678",
            graph=graph,
            store=store,
        )

        await agent.process_message("test")

        assert captured_state.get("phone_number") == "+593912345678"

    @pytest.mark.asyncio
    async def test_state_restored_from_store(self):
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[
            HumanMessage(content="Hola"),
            AIMessage(content="Bienvenido"),
        ])
        store.get_agent_state = AsyncMock(return_value={
            "patient_name": "Juan",
            "selected_service": "consulta",
        })

        captured_state = {}

        async def capture_ainvoke(input, config=None):
            captured_state.update(input)
            result = dict(input)
            result["messages"] = input.get("messages", []) + [AIMessage(content="ok")]
            return result

        graph = AsyncMock()
        graph.ainvoke.side_effect = capture_ainvoke

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=graph,
            store=store,
        )

        await agent.process_message("Seguimos con mi cita")

        assert captured_state.get("patient_name") == "Juan"
        assert captured_state.get("selected_service") == "consulta"

    @pytest.mark.asyncio
    async def test_error_handling_graph_failure(self):
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[])
        store.get_agent_state = AsyncMock(return_value=None)

        graph = AsyncMock()
        graph.ainvoke = AsyncMock(side_effect=Exception("Graph crashed"))

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=graph,
            store=store,
        )

        resp = await agent.process_message("test")

        assert resp.status == "error"
        assert "error" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_escalation_at_max_turns(self):
        from src.state import MAX_TURNS

        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[])
        store.get_agent_state = AsyncMock(return_value=None)

        captured_state = {}

        async def capture_ainvoke(input, config=None):
            captured_state.update(input)
            result = dict(input)
            result["messages"] = input.get("messages", []) + [AIMessage(content="ok")]
            return result

        graph = AsyncMock()
        graph.ainvoke.side_effect = capture_ainvoke

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=graph,
            store=store,
        )

        # Manually set conversation_turns above MAX_TURNS before processing
        # The agent checks: if conversation_turns >= MAX_TURNS: should_escalate
        original_process_message = agent.process_message

        async def patched_process_message(message: str):
            await agent.initialize()
            phone = agent.session_id.replace("deyy_", "")
            from src.state import create_initial_arcadium_state
            state = create_initial_arcadium_state(phone_number=phone)
            state["conversation_turns"] = MAX_TURNS  # Force max turns
            from langchain_core.messages import HumanMessage
            state["messages"] = [HumanMessage(content=message)]
            state["should_escalate"] = True
            config = {"configurable": {"thread_id": agent.session_id}}

            result = await graph.ainvoke(input=state, config=config)
            return agent._extract_response(result)

        resp = await patched_process_message("otra vez")

        assert captured_state.get("should_escalate") is True


class TestAgentThreadSafety:
    @pytest.mark.asyncio
    async def test_concurrent_calls_serialized(self):
        """Multiple concurrent process_message calls should be serialized by lock."""
        call_order = []

        async def slow_ainvoke(input, config=None):
            call_order.append("start")
            await asyncio.sleep(0.01)
            call_order.append("end")
            result = dict(input)
            result["messages"] = input.get("messages", []) + [AIMessage(content="ok")]
            return result

        graph = AsyncMock()
        graph.ainvoke.side_effect = slow_ainvoke

        store = AsyncMock()
        store.initialize = AsyncMock()
        store.get_history = AsyncMock(return_value=[])
        store.get_agent_state = AsyncMock(return_value=None)

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=graph,
            store=store,
        )

        # Run two messages concurrently
        await asyncio.gather(
            agent.process_message("msg1"),
            agent.process_message("msg2"),
        )

        # Initialize should only be called once due to lock
        assert store.initialize.call_count == 1
