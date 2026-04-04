#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StateMachineAgent - Agente con State Machine pattern usando LangGraph StateGraph.

Características:
- Usa SupportState para mantener estado entre turnos
- StateGraph con checkpointer PostgreSQL (PostgresSaver)
- Store ArcadiumStore para memoria cruzada-conversación
- Transiciones controladas por tools
- Persistencia completa de estado
"""

from typing import Any, Dict, List, Optional
import uuid
import structlog
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage

from graphs.arcadium_graph import ArcadiumStore, create_arcadium_graph, create_initial_arcadium_state
from core.config import get_settings
from agents.context_vars import (
    set_current_phone,
    set_current_project,
    reset_phone,
    reset_project
)
from agents.support_state import (
    SupportState,
    SupportStep,
    create_initial_state
)
from agents.step_configs import (
    get_prompt_for_step,
    get_tools_for_step,
    get_next_step,
    initialize_step_tools
)
from agents.tools_state_machine import STATE_MACHINE_TOOLS
from utils.phone_utils import normalize_phone

logger = structlog.get_logger("agent.state_machine")


# ============================================
# StateMachineAgent
# ============================================

class StateMachineAgent:
    """
    Agente con state machine pattern.
    Utiliza SupportState para mantener contexto de workflow.
    """

    def __init__(
        self,
        session_id: str,
        store: ArcadiumStore,  # Ahora recibe Store en lugar de MemoryManager
        project_id: Optional[uuid.UUID] = None,
        project_config: Optional[Any] = None,
        whatsapp_service: Optional[Any] = None,
        system_prompt: Optional[str] = None,  # No se usa, pero por compatibilidad
        llm_model: Optional[str] = None,
        llm_temperature: Optional[float] = None,
        max_iterations: Optional[int] = None,
        verbose: bool = False
    ):
        self.session_id = session_id
        self.store = store
        self.memory_manager = store.memory_manager  # Backward compatibility (opcional)
        self.project_id = project_id
        self.project_config = project_config
        self.whatsapp_service = whatsapp_service
        self.verbose = verbose

        # Configuración LLM
        # Usar settings del store si está disponible, sino get_settings()
        if hasattr(store, 'memory_manager') and hasattr(store.memory_manager, 'settings'):
            agent_settings = store.memory_manager.settings
        else:
            from core.config import get_settings
            agent_settings = get_settings()
        self.llm_model = llm_model or (project_config.agent_name if project_config else agent_settings.OPENAI_MODEL)
        self.llm_temperature = llm_temperature if llm_temperature is not None else (
            project_config.temperature if project_config else agent_settings.OPENAI_TEMPERATURE
        )
        self.max_iterations = max_iterations or (
            project_config.max_iterations if project_config else agent_settings.AGENT_MAX_ITERATIONS
        )

        # Inicializar componentes (lazy)
        self._llm: Optional[ChatOpenAI] = None
        self._graph = None  # StateGraph
        self.store: ArcadiumStore = store  # Store proporcionado
        self._checkpointer: Optional[BaseCheckpointSaver] = None
        self._initialized = False

        logger.info(
            "StateMachineAgent creado",
            session_id=session_id,
            project_id=str(project_id) if project_id else None,
            model=self.llm_model
        )

    async def initialize(self):
        """Inicializa el agente (crea LLM, gráfico StateGraph, store y checkpointer)."""
        if self._initialized:
            return

        logger.info("Inicializando StateMachineAgent", session_id=self.session_id)

        # 0. Inicializar configuración de steps (tools por estado)
        from agents.step_configs import initialize_step_tools
        initialize_step_tools()

        # Store ya provisto en __init__, usar self.store
        pass

        # 1. Inicializar DB session maker si no está ya inicializado
        from db import get_engine, init_session_maker
        from sqlalchemy.ext.asyncio import create_async_engine
        # Usar settings del memory_manager (que proviene del store)
        settings_mm = self.store.memory_manager.settings
        if get_engine() is None:
            # Determinar URL de DB
            database_url = settings_mm.DATABASE_URL
            if not database_url:
                # Fallback a SQLite para tests si no hay DATABASE_URL
                database_url = "sqlite+aiosqlite:///:memory:"
                logger.info("Usando SQLite en memoria para DB", session_id=self.session_id)
            # Crear engine e inicializar session maker
            engine = create_async_engine(
                database_url,
                echo=settings_mm.DEBUG if hasattr(settings_mm, 'DEBUG') else False,
                pool_size=5,
                max_overflow=10
            )
            init_session_maker(engine)
            logger.info("DB session maker auto-inicializado", engine=str(engine.url))

        # 2. Crear Checkpointer (PostgreSQL para prod, MemorySaver para dev/tests)
        settings = settings_mm  # Usar settings del memory_manager
        self._checkpointer = None
        if settings.USE_POSTGRES_FOR_MEMORY:
            try:
                from langgraph.checkpoint.postgres import PostgresSaver
                database_url = settings.DATABASE_URL
                if database_url:
                    self._checkpointer = PostgresSaver(conn_string=database_url)
                    logger.info("PostgresSaver checkpointer creado", session_id=self.session_id)
                else:
                    logger.warning("DATABASE_URL no configurada, checkpointer deshabilitado")
            except ImportError as e:
                logger.warning("PostgresSaver no disponible, usando MemorySaver", error=str(e))
                # Fallback a MemorySaver
                try:
                    from langgraph.checkpoint.memory import MemorySaver
                    self._checkpointer = MemorySaver()
                    logger.info("MemorySaver checkpointer creado (fallback)", session_id=self.session_id)
                except ImportError:
                    logger.error("MemorySaver no disponible tampoco, checkpointer deshabilitado")
            except Exception as e:
                logger.error("Error creando PostgresSaver", error=str(e))
                # Fallback a MemorySaver
                try:
                    from langgraph.checkpoint.memory import MemorySaver
                    self._checkpointer = MemorySaver()
                    logger.info("MemorySaver checkpointer creado (fallback)", session_id=self.session_id)
                except ImportError:
                    logger.error("MemorySaver no disponible, checkpointer deshabilitado")
        else:
            # Modo development/testing: NO crear checkpointer por defecto (puede causar recursion)
            self._checkpointer = None
            logger.debug("Checkpointer deshabilitado (USE_POSTGRES_FOR_MEMORY=false)", session_id=self.session_id)

        # 3. Crear LLM
        self._llm = ChatOpenAI(
            model=self.llm_model,
            temperature=self.llm_temperature,
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.OPENAI_TIMEOUT,
            max_retries=3
        )

        # 4. Obtener herramientas (todas)
        from agents.tools_state_machine import STATE_MACHINE_TOOLS
        all_tools = STATE_MACHINE_TOOLS

        # 5. Crear StateGraph usando ArcadiumGraph
        self._graph = await create_arcadium_graph(
            session_id=self.session_id,
            memory_manager=self.store.memory_manager,  # Para crear store si no existe
            project_id=self.project_id,
            llm_model=self.llm_model,
            llm_temperature=self.llm_temperature,
            tools=all_tools,
            store=self.store,  # Usar el store existente
            checkpointer=self._checkpointer
        )

        self._initialized = True
        logger.info("StateMachineAgent inicializado con StateGraph", session_id=self.session_id)

    async def process_message(self, message: str) -> Dict[str, Any]:
        """
        Procesa un mensaje del usuario con state machine usando StateGraph.

        Args:
            message: Mensaje del usuario

        Returns:
            Dict con:
            - response: texto de respuesta
            - tool_calls: lista de herramientas usadas
            - current_step: estado actual después del turno
            - state: estado completo
            - execution_time_seconds: tiempo de ejecución
            - status: "success" o "error"
        """
        if not self._initialized:
            await self.initialize()

        start_time = datetime.now()
        phone_token = None
        project_token = None

        try:
            # 1. Configurar context vars
            phone = self._extract_phone_from_session(self.session_id)
            phone_token = set_current_phone(phone)
            project_token = None
            if self.project_id:
                project_token = set_current_project(self.project_id, self.project_config)

            # 2. Cargar historial y estado de SupportState desde store
            history = await self.store.get_history(self.session_id)
            agent_state = await self.store.get_agent_state(self.session_id, project_id=self.project_id)

            # 3. Construir ArcadiumState completo
            if agent_state:
                # Merge: start from agent_state
                arcadium_state = create_initial_arcadium_state(
                    phone_number=phone,
                    project_id=self.project_id
                )
                # Merge agent_state fields
                for key, value in agent_state.items():
                    if key in arcadium_state:
                        arcadium_state[key] = value
            else:
                # Estado nuevo
                arcadium_state = create_initial_arcadium_state(
                    phone_number=phone,
                    project_id=self.project_id
                )

            # Añadir historial
            arcadium_state["messages"] = history

            # Incrementar turnos
            arcadium_state["conversation_turns"] = arcadium_state.get("conversation_turns", 0) + 1

            # 4. Añadir mensaje del usuario
            arcadium_state["messages"].append(HumanMessage(content=message))

            # 5. Invocar StateGraph
            config = {
                "configurable": {
                    "thread_id": self.session_id
                }
            }

            # Nota: El graph espera que ArcadiumState incluya todos los campos
            result = await self._graph.ainvoke(arcadium_state, config=config)

            # 6. Extraer resultados
            response = ""
            tool_calls = []
            updated_state = result

            logger.info(
                "Graph result",
                selected_service=updated_state.get("selected_service"),
                current_step=updated_state.get("current_step"),
                intent=updated_state.get("intent")
            )

            # El último mensaje en result["messages"] es la respuesta del AI
            if result.get("messages"):
                ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
                if ai_messages:
                    response = ai_messages[-1].content

                    # Extraer tool calls de la historia (intermediate steps)
                    # En StateGraph, las tools se ejecutan y los resultados se añaden a messages
                    for i, msg in enumerate(result["messages"]):
                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_calls.append({
                                    "tool": tc.get("name", "unknown"),
                                    "input": tc.get("args", {}),
                                    "output": ""  # No disponible fácilmente
                                })

                # También incluir tool_calls delegados a DeyyAgent (si existen)
                if result.get("delegated_tool_calls"):
                    tool_calls.extend(result["delegated_tool_calls"])

            # 7. Guardar estado actualizado (store ya lo hizo en nodo save_state, pero por si acaso)
            # El nodo save_state ya guardó, pero podemos forzar save adicional si es necesario
            current_step = updated_state.get("current_step", "reception")

            execution_time = (datetime.now() - start_time).total_seconds()

            logger.info(
                "Mensaje procesado con StateGraph",
                session_id=self.session_id,
                current_step=current_step,
                turns=updated_state.get("conversation_turns"),
                response_len=len(response),
                status="success"
            )

            # Preparar estado para respuesta (excluir messages por tamaño)
            return_state = {k: v for k, v in updated_state.items() if k != "messages"}
            logger.info(
                "Returning process_message",
                selected_service=return_state.get("selected_service"),
                current_step=return_state.get("current_step")
            )

            return {
                "response": response,
                "tool_calls": tool_calls,
                "current_step": current_step,
                "state": return_state,
                "execution_time_seconds": execution_time,
                "status": "success"
            }

        except Exception as e:
            logger.error("Error procesando mensaje", error=str(e), exc_info=True)
            return {
                "response": "Lo siento, ha ocurrido un error. Por favor intenta de nuevo.",
                "status": "error",
                "error": str(e),
                "current_step": "reception"
            }

        finally:
            # Reset context vars
            if phone_token:
                reset_phone(phone_token)
            if project_token:
                reset_project(project_token)

    async def get_current_state(self) -> Dict[str, Any]:
        """
        Devuelve el estado actual del agente (SupportState solamente).
        """
        state_data = await self.store.get_agent_state(self.session_id, project_id=self.project_id)
        return state_data or create_initial_state()

    async def reset_state(self, step: SupportStep = "reception") -> Dict[str, Any]:
        """
        Reinicia el estado a un paso específico (por defecto reception).
        """
        new_state = create_initial_state(step=step)
        await self.store.save_agent_state(self.session_id, new_state, project_id=self.project_id)
        logger.info("Estado reiniciado", session_id=self.session_id, step=step)
        return new_state

    def _extract_phone_from_session(self, session_id: str) -> str:
        """
        Extrae número de teléfono del session_id.
        Session ID suele ser el número o un UUID.
        Normaliza el número a formato E.164.
        """
        # Si session_id tiene formato de teléfono, normalizarlo
        if "@" not in session_id and session_id.replace("+", "").isdigit():
            try:
                return normalize_phone(session_id)
            except ValueError:
                return session_id
        # Si no es teléfono, devolver tal cual (UUID, etc.)
        return session_id
