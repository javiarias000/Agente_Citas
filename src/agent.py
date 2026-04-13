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
        Procesa un mensaje de WhatsApp a través del grafo (V1 o V2).

        Flujo V2 (ReAct):
        1. Normaliza teléfono
        2. Construye estado inicial SOLO con el mensaje nuevo
        3. Enriquece con contexto del paciente (memorias)
        4. Invoca el grafo — entry_v2 carga historial, react_loop decide todo
        5. Extrae _final_response del estado final

        Flujo V1 (legacy):
        1-4. Igual, pero node_entry carga historial y 20+ nodos rutean.
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

                # Campos de flujo — se restauran si la cita NO fue completada.
                # confirmation_sent=True significa que el flujo terminó (cita creada/cancelada).
                # En ese caso, no restaurar para no contaminar un nuevo flujo.
                # Si confirmation_sent=False/None, el flujo está en progreso (pidiendo campos,
                # esperando confirmación, etc.) → restaurar todo el contexto de booking.
                if not prev_state.get("confirmation_sent"):
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

        # Enriquecer con memoria del paciente (tipada + semántica)
        phone = self.session_id.replace("deyy_", "")
        patient_context = await self._load_patient_context(phone, message)
        if patient_context:
            state["semantic_memory_context"] = patient_context
            logger.debug(
                "Contexto de paciente inyectado",
                phone=phone,
                chars=len(patient_context),
            )

        # Invocar grafo
        # UUID por invocación: evita que add_messages acumule mensajes del checkpoint
        # de sesiones anteriores. La historia real viene del store (limit=10 en node_entry).
        invoke_thread_id = f"{self.session_id}_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": invoke_thread_id}}
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
        """Extrae AgentResponse del estado final del grafo.

        V2: usa _final_response (seteado por node_format_response).
        V1: busca último AIMessage en messages.
        """
        # V2: _final_response es la fuente canónica
        text = state.get("_final_response", "")

        if not text:
            # V1 fallback: buscar último AIMessage
            messages = state.get("messages", [])
            for msg in reversed(messages):
                if getattr(msg, "type", None) == "ai" and getattr(msg, "content", ""):
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

    async def _load_patient_context(self, phone: str, query: str) -> str:
        """
        Carga el contexto completo del paciente combinando:
        1. Perfil estructurado (patient_memories tipo 'user') — SIEMPRE incluido.
        2. Memorias de feedback/project relevantes al query actual.
        3. Memorias semánticas vectoriales (si memory_agent está habilitado).

        El perfil 'user' equivale al MEMORY.md de Claude Code: siempre en contexto,
        sin importar el intent. Los demás tipos se cargan por relevancia.
        """
        parts: list[str] = []

        # ── 1. Perfil estructurado (siempre) ─────────────────────────────────
        try:
            from db import get_async_session
            from services.patient_memory_service import PatientMemoryService

            async with get_async_session() as session:
                mem_svc = PatientMemoryService(session)
                profile = await mem_svc.load_profile(phone)
                if profile:
                    parts.append(PatientMemoryService.format_profile(profile))

                # Cargar feedback y project para contexto adicional
                all_mem = await mem_svc.load_all(phone)
                extra = PatientMemoryService.format_full(
                    all_mem, include_types=["feedback", "project", "reference"]
                )
                if extra:
                    parts.append(f"CONTEXTO ADICIONAL DEL PACIENTE:\n{extra}")

        except Exception as e:
            logger.warning("Error cargando patient_memories", phone=phone, error=str(e))

        # ── 2. Memorias semánticas vectoriales (si disponibles) ───────────────
        if self.memory_integration and getattr(self.memory_integration, "_initialized", False):
            try:
                limit = getattr(self.memory_integration.settings, "MEMORY_AGENT_MAX_RESULTS", 5)
                threshold = getattr(self.memory_integration.settings, "MEMORY_AGENT_SIMILARITY_THRESHOLD", 0.7)

                memories = await self.memory_integration.search_memories(
                    user_id=phone,
                    query=query,
                    project_id=self.project_id,
                    limit=limit,
                    threshold=threshold,
                )
                if memories:
                    lines = ["MEMORIAS SEMÁNTICAS RELACIONADAS:"]
                    for mem in memories:
                        lines.append(f"  - {mem['content']} (ctx: {mem['context']})")
                    parts.append("\n".join(lines))
            except Exception as e:
                logger.warning("Error en búsqueda semántica", phone=phone, error=str(e))

        return "\n\n".join(parts) if parts else ""

    # ── Legado — conservado para compatibilidad ───────────────────────────────
    async def _get_semantic_context(self, phone: str, query: str) -> str:
        """Legado. Usar _load_patient_context en su lugar."""
        return await self._load_patient_context(phone, query)
