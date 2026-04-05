"""
ArcadiumAgent — entry point de alto nivel.

Reemplaza DeyyAgent + StateMachineAgent.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from src.state import create_initial_arcadium_state, TIMEZONE

logger = structlog.get_logger("langgraph.agent")


@dataclass
class AgentResponse:
    """Respuesta estandarizada del agente."""

    text: str
    appointment_id: Optional[str] = None
    google_event_link: Optional[str] = None
    status: str = "ok"  # ok | error | escalated
    intent: Optional[str] = None
    should_escalate: bool = False


class ArcadiumAgent:
    """
    Agente principal Arcadium sobre LangGraph.

    Uso:
        agent = ArcadiumAgent(
            session_id="deyy_+593999999999",
            graph=compiled_graph,
            store=postgres_store,
        )
        resp = await agent.process_message("Quiero agendar una limpieza mañana")
    """

    def __init__(
        self,
        session_id: str,
        graph,
        store,
        llm=None,
        calendar_service=None,
        db_service=None,
    ):
        self.session_id = session_id
        self.graph = graph
        self.store = store
        self.llm = llm
        self.calendar_service = calendar_service
        self.db_service = db_service
        self._initialized = False
        self._lock = asyncio.Lock()
        self._phone_var: contextvars.ContextVar[Optional[str]] = (
            contextvars.ContextVar("phone", default=None)
        )

    async def initialize(self) -> None:
        """Inicialización thread-safe."""
        async with self._lock:
            if not self._initialized:
                await self.store.initialize()
                self._initialized = True
                logger.info("ArcadiumAgent inicializado", session_id=self.session_id)

    async def process_message(self, message: str) -> AgentResponse:
        """
        Procesa un mensaje de WhatsApp a través del grafo.

        Flujo:
        1. Normaliza teléfono
        2. Establece ContextVars
        3. Carga estado previo del store
        4. Invoca el grafo
        5. Extrae la respuesta y retorna AgentResponse
        """
        await self.initialize()

        # Extraer teléfono del session_id
        phone = self.session_id.replace("deyy_", "")

        # ContextVar
        self._phone_var.set(phone)

        # Construir estado inicial
        state = create_initial_arcadium_state(phone_number=phone)

        # Cargar historial y estado previo
        history = await self.store.get_history(phone, limit=50)
        prev_state = await self.store.get_agent_state(phone)

        if history:
            state["messages"] = list(history)

        if prev_state:
            # Restaurar campos persistentes
            for field in [
                "patient_name",
                "selected_service",
                "service_duration",
                "intent",
                "datetime_preference",
                "available_slots",
                "selected_slot",
                "appointment_id",
                "google_event_id",
                "google_event_link",
                "conversation_turns",
                "awaiting_confirmation",
                "confirmation_type",
                "errors_count",
            ]:
                if field in prev_state and prev_state[field] is not None:
                    state[field] = prev_state[field]

        # Agregar el mensaje entrante al estado
        from langchain_core.messages import HumanMessage

        state["messages"] = state.get("messages", []) + [HumanMessage(content=message)]
        state["conversation_turns"] = state.get("conversation_turns", 0) + 1

        # Verificar escalación por número de turns
        if state["conversation_turns"] >= 10:
            state["should_escalate"] = True

        # Invocar grafo
        config = {"configurable": {"thread_id": self.session_id}}

        try:
            result = await self.graph.ainvoke(input=state, config=config)
        except Exception as e:
            logger.error("Error invocando grafo", error=str(e), exc_info=True)
            return AgentResponse(
                text="Lo siento, hubo un error técnico. Por favor llame a la clínica. 📞",
                status="error",
            )

        # Extraer respuesta
        return self._extract_response(result)

    def _extract_response(self, state: Dict[str, Any]) -> AgentResponse:
        """Extrae AgentResponse del estado final del grafo."""
        messages = state.get("messages", [])
        text = ""
        for msg in reversed(messages):
            if getattr(msg, "type", None) == "ai":
                text = msg.content
                break

        if not text:
            text = "Lo siento, no pude procesar su mensaje. 📞"

        return AgentResponse(
            text=text,
            appointment_id=state.get("appointment_id"),
            google_event_link=state.get("google_event_link"),
            status="ok",
            intent=state.get("intent"),
            should_escalate=state.get("should_escalate", False),
        )
