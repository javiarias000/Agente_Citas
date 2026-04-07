"""
ArcadiumAgent — entry point de alto nivel.

Reemplaza DeyyAgent + StateMachineAgent.

FIXES APLICADOS:
- [CRÍTICO] process_message cargaba historial aquí Y en node_entry → doble carga
  que causaba que el agente olvidara la conversación a partir del 2do mensaje.
  → Ahora solo se pasa _incoming_message. node_entry es el único responsable
    de cargar el historial desde el store.
- [MEDIO] La persistencia de mensajes nuevos se hacía aquí Y en node_save_state
  → mensajes duplicados en el store.
  → Se elimina de aquí; node_save_state es el único responsable.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import structlog

from src.state import create_initial_arcadium_state

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
        project_id: Optional[uuid.UUID] = None,
    ):
        self.session_id = session_id
        self.graph = graph
        self.store = store
        self.llm = llm
        self.calendar_service = calendar_service
        self.db_service = db_service
        self.project_id = project_id
        self._initialized = False
        self._lock = asyncio.Lock()
        self._phone_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
            "phone", default=None
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
        2. Construye estado inicial SOLO con el mensaje nuevo (_incoming_message)
        3. Restaura campos persistentes del estado previo (sin historial de mensajes)
        4. Invoca el grafo — node_entry es el responsable de cargar el historial
        5. Extrae la respuesta y retorna AgentResponse

        FIX: Ya NO se carga historial aquí. Hacerlo causaba que node_entry
        recibiera state["messages"] con datos, entrara al bloque
        `if history and not state.get("messages")` como False, y nunca
        mergeara el historial → el agente olvidaba la conversación.
        """
        await self.initialize()

        # Extraer teléfono del session_id
        phone = self.session_id.replace("deyy_", "")
        self._phone_var.set(phone)

        # Construir estado inicial vacío
        state = create_initial_arcadium_state(
            phone_number=phone,
            project_id=self.project_id,
        )

        # FIX: pasar el mensaje nuevo via _incoming_message, NO via messages.
        # node_entry construirá: historial_del_store + [HumanMessage(incoming)]
        state["_incoming_message"] = message
        state["conversation_turns"] = 0  # node_entry lo incrementa

        # Restaurar campos persistentes del estado previo (NO mensajes)
        try:
            prev_state = await self.store.get_agent_state(phone)
            if prev_state:
                for f in [
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
                    if f in prev_state and prev_state[f] is not None:
                        state[f] = prev_state[f]
        except Exception as e:
            logger.warning("No se pudo cargar estado previo", error=str(e))

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

        # FIX: NO persistir mensajes aquí. node_save_state ya lo hace.
        # Hacerlo en ambos lados causaba mensajes duplicados en el store.

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
