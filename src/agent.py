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
from langchain_core.messages import HumanMessage, AIMessage

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
        memory_integration=None,  # NUEVO: MemoryAgentIntegration opcional
    ):
        self.session_id = session_id
        self.graph = graph
        self.store = store
        self.llm = llm
        self.calendar_service = calendar_service
        self.db_service = db_service
        self.project_id = project_id
        self.memory_integration = memory_integration  # NUEVO
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

        state["_incoming_message"] = message
        state["conversation_turns"] = 0
        
        # Restaurar campos persistentes del estado previo (NO mensajes)
        try:
            prev_state = await self.store.get_agent_state(phone)
            if prev_state:
                # Campos estables — siempre se restauran
                for f in [
                    "patient_name",
                    "conversation_turns",
                    "awaiting_confirmation",
                    "confirmation_type",
                    "errors_count",
                ]:
                    if f in prev_state and prev_state[f] is not None:
                        state[f] = prev_state[f]

                # Campos transientes — solo se restauran si el turno anterior
                # quedó esperando una confirmación (flujo en progreso).
                # Si no estamos esperando nada, son datos de una cita ya
                # completada en sesión anterior y no deben contaminar el contexto.
                if prev_state.get("awaiting_confirmation"):
                    for f in [
                        "selected_service",
                        "service_duration",
                        "intent",
                        "datetime_preference",
                        "available_slots",
                        "selected_slot",
                        "appointment_id",
                        "google_event_id",
                        "google_event_link",
                    ]:
                        if f in prev_state and prev_state[f] is not None:
                            state[f] = prev_state[f]
        except Exception as e:
            logger.warning("No se pudo cargar estado previo", error=str(e))

        # CRÍTICO: recalcular missing_fields DESPUÉS de restaurar campos
        from src.state import get_missing_fields
        state["missing_fields"] = get_missing_fields(state)

        # NUEVO: Enriquecer con contexto semántico si memory_agent está habilitado
        if self.memory_integration:
            phone = self.session_id.replace("deyy_", "")
            semantic_context = await self._get_semantic_context(phone, message)
            if semantic_context:
                # Añadir al estado; el prompt del sistema debería referenciar esta variable
                state["semantic_memory_context"] = semantic_context
                logger.debug(
                    "Contexto semántico inyectado",
                    phone=phone,
                    memories_count=semantic_context.count('\n')
                )

        # Invocar grafo
        config = {"configurable": {"thread_id": self.session_id}}
        try:
            result = await self.graph.ainvoke(
                state,
                config=config
            )

            # Check if graph is interrupted (pending human review)
            if hasattr(result, "interrupts") and result.interrupts:
                logger.info("Grafo interrumpido para revisión humana", session_id=self.session_id)
                return AgentResponse(
                    text="SISTEMA: Acción pendiente de aprobación humana.",
                    status="pending_approval",
                    intent=state.get("intent"),
                )

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

    async def _get_semantic_context(self, phone: str, query: str) -> str:
        """
        Recupera memorias semánticas relevantes para enriquecer el contexto del LLM.

        Args:
            phone: Número de teléfono del usuario
            query: Texto de la consulta actual (para búsqueda por similitud)

        Returns:
            String formateado con memorias relevantes, o string vacío si none.
        """
        if not self.memory_integration or not self.memory_integration._initialized:
            return ""

        try:
            user_id = phone  # Usamos phone como user_id
            limit = getattr(
                self.memory_integration.settings,
                'MEMORY_AGENT_MAX_RESULTS',
                5
            )
            threshold = getattr(
                self.memory_integration.settings,
                'MEMORY_AGENT_SIMILARITY_THRESHOLD',
                0.7
            )

            memories = await self.memory_integration.search_memories(
                user_id=user_id,
                query=query,
                project_id=self.project_id,
                limit=limit,
                threshold=threshold
            )

            if not memories:
                return ""

            # Formatear para incluir en prompt
            lines = ["Memorias relevantes del usuario:"]
            for mem in memories:
                lines.append(f"- [{mem['key']}] {mem['content']} (contexto: {mem['context']})")

            return "\n".join(lines)

        except Exception as e:
            logger.warning(
                "Error recuperando contexto semántico",
                phone=phone,
                error=str(e)
            )
            return ""
