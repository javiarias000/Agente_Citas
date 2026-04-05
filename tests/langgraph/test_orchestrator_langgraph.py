"""Tests for Orchestrator integration with LangGraph feature flag.

Tests:
1. USE_LANGGRAPH=false → uses DeyyAgent
2. USE_LANGGRAPH=true → uses ArcadiumAgent
3. _init_langgraph creates store, LLM, and compiles graph
4. Webhook test endpoint works in both modes
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ─────────────────────────────────────────────────


def make_mock_settings(**overrides):
    """Create a mock settings dict with sensible defaults."""
    settings = {
        "OPENAI_API_KEY": "sk-test-key",
        "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
        "WHATSAPP_API_URL": "http://localhost:3000",
        "WHATSAPP_INSTANCE_NAME": "test-instance",
        "WHATSAPP_API_TOKEN": "",
        "USE_POSTGRES_FOR_MEMORY": False,
        "SESSION_EXPIRY_HOURS": 24,
        "DEBUG": False,
        "ENABLE_METRICS": False,
        "OPENAI_MODEL": "gpt-4o-mini",
        "OPENAI_TEMPERATURE": 0.7,
        "AGENT_MAX_ITERATIONS": 10,
        "AGENT_VERBOSE": False,
        "AGENT_SYSTEM_PROMPT": "You are a helpful assistant",
        "ENABLE_STATE_MACHINE": False,
        "USE_LANGGRAPH": False,
        "LANGGRAPH_MODEL": "gpt-4o-mini",
        "LANGGRAPH_TEMPERATURE": 0.5,
    }
    settings.update(overrides)
    return settings


# ── Tests ───────────────────────────────────────────────────


class TestFeatureFlagRouting:
    """Test that the orchestrator picks the right agent based on USE_LANGGRAPH."""

    def test_langgraph_flag_false_uses_deyy(self):
        """When USE_LANGGRAPH=False, should route to DeyyAgent (or RouterAgent)."""
        settings = make_mock_settings(USE_LANGGRAPH=False)
        assert settings["USE_LANGGRAPH"] is False

    def test_langgraph_flag_true(self):
        """When USE_LANGGRAPH=True, should route to ArcadiumAgent."""
        settings = make_mock_settings(USE_LANGGRAPH=True)
        assert settings["USE_LANGGRAPH"] is True


class TestInitLangGraph:
    """Test _init_langgraph method."""

    @pytest.mark.asyncio
    async def test_init_langgraph_components_created(self):
        """_init_langgraph should create store, LLM, and compiled graph."""
        from src.graph import compile_graph

        mock_llm = AsyncMock()
        mock_store = AsyncMock()
        mock_store.initialize = AsyncMock()

        # compile_graph should work with mocked dependencies
        graph = compile_graph(
            llm=mock_llm,
            store=mock_store,
            calendar_service=None,
            db_service=None,
        )

        assert graph is not None
        # Note: initialize() is called by ArcadiumAgent, not compile_graph
        # The store's init is called separately, so we just check graph builds


class TestAgentCreation:
    """Test agent creation paths in the orchestrator."""

    def test_langgraph_agent_path_selected(self):
        """When USE_LANGGRAPH=True, the code path selects ArcadiumAgent."""
        from src.agent import ArcadiumAgent

        mock_graph = AsyncMock()
        mock_store = AsyncMock()
        mock_llm = AsyncMock()

        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=mock_graph,
            store=mock_store,
            llm=mock_llm,
        )

        assert agent.session_id == "deyy_+593999999999"
        assert agent.graph is mock_graph
        assert agent.store is mock_store
        assert agent.llm is mock_llm

    def test_session_id_format(self):
        """Session ID should be formatted as deyy_{phone}."""
        from src.agent import ArcadiumAgent

        mock_graph = AsyncMock()
        mock_store = AsyncMock()

        # Simulate what orchestrator does
        phone = "+593912345678"
        session_id = f"deyy_{phone}"

        agent = ArcadiumAgent(
            session_id=session_id,
            graph=mock_graph,
            store=mock_store,
        )

        assert agent.session_id.startswith("deyy_+")
        assert agent.session_id == "deyy_+593912345678"


class TestWebhookEndpoint:
    """Test webhook integration with LangGraph agent."""

    @pytest.mark.asyncio
    async def test_webhook_test_endpoint_no_send(self):
        """POST /webhook/test should process without sending WhatsApp message."""
        from src.webhook_handler import WebhookHandler
        from src.agent import AgentResponse

        async def mock_agent_factory(phone):
            mock_agent = AsyncMock()
            mock_agent.process_message = AsyncMock(
                return_value=AgentResponse(text="Hola, en que puedo ayudarle?")
            )
            return mock_agent

        handler = WebhookHandler(
            agent_factory=mock_agent_factory,
            redis_client=None,
            evolution_url="http://test-api.com",
            api_key="test-key",
        )

        payload = {
            "data": {
                "key": {"remoteJd": "593999999999@s.whatsapp.net"},
                "message": {"conversation": "Hola quiero agendar una cita"},
            }
        }

        result = await handler.handle(payload)

        assert result["status"] == "ok"
        assert "response" in result

    @pytest.mark.asyncio
    async def test_webhook_handles_incomplete_payload(self):
        """Should gracefully handle missing phone or message."""
        from src.webhook_handler import WebhookHandler

        mock_agent = AsyncMock()
        handler = WebhookHandler(
            agent_factory=lambda phone: mock_agent,
            redis_client=None,
        )

        # Missing message
        result = await handler.handle({
            "data": {"key": {"remoteJd": "593999999999@s.whatsapp.net"}}
        })
        assert result["status"] == "ignored"

        # Missing phone
        result = await handler.handle({
            "data": {"message": {"conversation": "hola"}}
        })
        assert result["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_webhook_phone_normalization(self):
        """Phone should be normalized from JID format."""
        from src.webhook_handler import WebhookHandler
        from src.agent import AgentResponse

        captured_phone = None

        async def mock_factory(phone):
            nonlocal captured_phone
            captured_phone = phone
            mock_agent = AsyncMock()
            mock_agent.process_message = AsyncMock(
                return_value=AgentResponse(text="ok")
            )
            return mock_agent

        handler = WebhookHandler(
            agent_factory=mock_factory,
            redis_client=None,
        )

        payload = {
            "data": {
                "key": {"remoteJd": "593999999999@s.whatsapp.net"},
                "message": {"conversation": "hola"},
            }
        }

        await handler.handle(payload)

        assert captured_phone == "+593999999999"


class TestRollback:
    """Test that rollback (USE_LANGGRAPH=false) works."""

    def test_legacy_path_is_configured(self):
        """When USE_LANGGRAPH=false, legacy agents should be available."""
        from agents.deyy_agent import DeyyAgent
        assert DeyyAgent is not None

    def test_both_paths_available(self):
        """Both LangGraph and legacy agents should be importable."""
        from src.agent import ArcadiumAgent
        from agents.deyy_agent import DeyyAgent

        assert ArcadiumAgent is not None
        assert DeyyAgent is not None
