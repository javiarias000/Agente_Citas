#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeyyGraph - StateGraph simple para DeyyAgent.

Implementa un grafo mínimo que:
1. Carga historial desde Store
2. Invoca al LLM con herramientas
3. Guarda mensajes y actualiza Store
4. Usa checkpointer PostgreSQL para persistencia
"""

from typing import Dict, Any, List, Optional, Annotated, TypedDict
import uuid
import structlog
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from agents.langchain_compat import create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.base import BaseCheckpointSaver

from core.store import ArcadiumStore
from core.config import get_settings

logger = structlog.get_logger("graph.deyy")


# ============================================
#  ESTADO SIMPLE PARA DEYY
# ============================================

class DeyyState(TypedDict):
    """Estado mínimo para DeyyAgent"""
    messages: Annotated[List[BaseMessage], add_messages]
    phone_number: str
    project_id: Optional[uuid.UUID]


def create_initial_deyy_state(
    phone_number: str,
    project_id: Optional[uuid.UUID] = None,
    system_prompt: Optional[str] = None
) -> DeyyState:
    """
    Crea estado inicial para DeyyAgent.

    Args:
        phone_number: Número del usuario
        project_id: ID del proyecto
        system_prompt: Prompt del sistema (opcional)

    Returns:
        DeyyState inicializado
    """
    return DeyyState(
        messages=[],
        phone_number=phone_number,
        project_id=project_id
    )


# ============================================
#  NODOS DEL GRAFO
# ============================================

async def load_initial_context(
    state: DeyyState,
    store: ArcadiumStore,
    system_prompt: Optional[str] = None
) -> DeyyState:
    """
    Nodo: Carga el historial de mensajes desde Store.

    Args:
        state: Estado actual
        store: Store para acceso a datos
        system_prompt: Prompt del sistema a insertar

    Returns:
        Estado con historial cargado
    """
    session_id = state["phone_number"]
    history = await store.get_history(session_id)

    logger.debug(
        "Context loaded",
        session_id=session_id,
        history_len=len(history)
    )

    # Añadir system message si se provee y no hay mensajes aún
    if system_prompt and not history:
        system_msg = SystemMessage(content=system_prompt)
        state["messages"] = [system_msg] + history
    else:
        state["messages"] = history

    return state


async def agent_node(
    state: DeyyState,
    store: ArcadiumStore,
    llm: ChatOpenAI,
    tools: List[Any],
    system_prompt: Optional[str] = None
) -> DeyyState:
    """
    Nodo: Invoca al agente Deyy con LLM y herramientas.

    Args:
        state: Estado actual
        store: Store para persistencia
        llm: Modelo de lenguaje
        tools: Lista de herramientas

    Returns:
        Estado actualizado con respuesta del agente
    """
    # Construir prompt con variables correctas para create_openai_tools_agent
    default_system = "Eres Deyy, asistente especializado en gestión de citas dentales."
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt or default_system),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    # Crear agente
    agent = create_openai_tools_agent(llm, tools, prompt)

    # Preparar input
    all_messages = state.get("messages", [])
    # Separar historial (todo excepto el último mensaje que es el input actual)
    if all_messages:
        user_input = all_messages[-1].content
        chat_history = all_messages[:-1]
    else:
        user_input = ""
        chat_history = []

    # Extraer intermediate_steps del historial
    intermediate_steps = []
    i = 0
    while i < len(chat_history):
        msg = chat_history[i]
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            # Encontrar el ToolMessage correspondiente (siguiente mensaje si existe)
            if i + 1 < len(chat_history):
                next_msg = chat_history[i + 1]
                if isinstance(next_msg, ToolMessage):
                    # Extraer tool call y observation
                    for tool_call in msg.tool_calls:
                        # Buscar el ToolMessage que corresponde a este tool_call
                        # Asumimos orden secuencial
                        observation = next_msg.content
                        # Crear objeto action-like (dict con tool y tool_input)
                        action = {
                            "tool": tool_call.get("name", ""),
                            "tool_input": tool_call.get("args", {})
                        }
                        intermediate_steps.append((action, observation))
                    i += 1  # Saltar ToolMessage
        i += 1

    try:
        # Invocar agente con intermediate_steps
        result = await agent.ainvoke({
            "input": user_input,
            "chat_history": chat_history,
            "intermediate_steps": intermediate_steps
        })

        # Normalizar resultado: puede ser str o dict
        if isinstance(result, str):
            output = result
            used_intermediate_steps = []
        elif isinstance(result, dict):
            output = result.get("output", "")
            used_intermediate_steps = result.get("intermediate_steps", [])
        else:
            output = str(result)
            used_intermediate_steps = []

        # Añadir respuesta a mensajes
        ai_message = AIMessage(content=output)
        state["messages"].append(ai_message)

        logger.debug(
            "Agent response generated",
            response_len=len(output),
            tools_used=len(used_intermediate_steps)
        )

    except Exception as e:
        logger.error("Error in agent_node", error=str(e), exc_info=True)
        # Añadir mensaje de error
        error_msg = AIMessage(content=f"Error: {str(e)}")
        state["messages"].append(error_msg)

    return state


async def save_context_node(
    state: DeyyState,
    store: ArcadiumStore
) -> DeyyState:
    """
    Nodo: Guarda los mensajes en Store.

    Args:
        state: Estado actual
        store: Store para persistencia

    Returns:
        Estado (sin cambios)
    """
    session_id = state["phone_number"]
    messages = state.get("messages", [])

    # Guardar solo mensajes humano y AI (no system)
    for msg in messages:
        if isinstance(msg, (HumanMessage, AIMessage)):
            await store.add_message(session_id, msg)

    logger.debug(
        "Messages saved to store",
        session_id=session_id,
        messages_count=len([m for m in messages if isinstance(m, (HumanMessage, AIMessage))])
    )

    return state


# ============================================
#  CONSTRUCTOR DEL GRAFO
# ============================================

def build_deyy_graph(
    llm: ChatOpenAI,
    store: ArcadiumStore,
    tools: List[Any],
    system_prompt: str,
    checkpointer: Optional[BaseCheckpointSaver] = None
) -> StateGraph:
    """
    Construye el StateGraph para DeyyAgent.

    Args:
        llm: Modelo de lenguaje
        store: Store para memoria persistente
        tools: Lista de herramientas
        system_prompt: Prompt del sistema
        checkpointer: Checkpointer para persistencia de state

    Returns:
        StateGraph compilado
    """
    workflow = StateGraph(DeyyState)

    # Nodos: usar functools.partial para bind arguments a funciones async
    from functools import partial
    load_node = partial(load_initial_context, store=store, system_prompt=system_prompt)
    agent_node_wrapper = partial(agent_node, store=store, llm=llm, tools=tools, system_prompt=system_prompt)
    save_node = partial(save_context_node, store=store)

    workflow.add_node("load", load_node)
    workflow.add_node("agent", agent_node_wrapper)
    workflow.add_node("save", save_node)

    # Edges
    workflow.set_entry_point("load")
    workflow.add_edge("load", "agent")
    workflow.add_edge("agent", "save")
    workflow.add_edge("save", END)

    # Compilar con checkpointer
    if checkpointer:
        graph = workflow.compile(checkpointer=checkpointer)
    else:
        graph = workflow.compile()

    logger.info("DeyyGraph compiled", tools_count=len(tools), has_checkpointer=checkpointer is not None)
    return graph


# ============================================
#  FACTORY
# ============================================

async def create_deyy_graph(
    session_id: str,
    store: ArcadiumStore,
    tools: List[Any],  # Obligatorio
    project_id: Optional[uuid.UUID] = None,
    system_prompt: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_temperature: Optional[float] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None
) -> StateGraph:
    """
    Factory: Crea el DeyyGraph.

    Args:
        session_id: ID de sesión
        store: ArcadiumStore
        project_id: ID del proyecto
        system_prompt: Prompt del sistema
        llm_model: Modelo de OpenAI
        llm_temperature: Temperatura
        tools: Lista de herramientas (OBLIGATORIO)
        checkpointer: Checkpointer para persistencia

    Returns:
        StateGraph compilado
    """
    settings = get_settings()

    # 1. Crear LLM
    llm = ChatOpenAI(
        model=llm_model or settings.OPENAI_MODEL,
        temperature=llm_temperature or settings.OPENAI_TEMPERATURE,
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT,
        max_retries=3
    )

    # 2. Prompt por defecto si no se provee
    if system_prompt is None:
        system_prompt = """Eres Deyy, asistente especializado en gestión de citas dentales.

Tu objetivo es ayudar a los pacientes a:
- Agendar nuevas citas
- Consultar disponibilidad
- Cancelar o reagendar citas existentes

Sé amable, profesional y claro. Pide toda la información necesaria."""
    # 3. Crear checkpointer si no se proporcionó
    if checkpointer is None:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from sqlalchemy.engine import make_url
            # Limpiar DATABASE_URL: PostgresSaver.from_conn_string espera URL PostgreSQL simple
            # Sin driver prefix como +asyncpg
            url = make_url(settings.DATABASE_URL)
            if url.drivername in ('postgresql+asyncpg', 'postgresql+psycopg2'):
                clean_drivername = 'postgresql'
            else:
                clean_drivername = url.drivername
            # Reconstruir URL sin driver
            db_url = f"{clean_drivername}://"
            if url.username:
                db_url += url.username
                if url.password:
                    db_url += f":{url.password}"
                db_url += "@"
            if url.host:
                db_url += url.host
            if url.port:
                db_url += f":{url.port}"
            if url.database:
                db_url += f"/{url.database}"
            if url.query:
                db_url += f"?{url.query}"
            # from_conn_string devuelve un context manager. Obtenemos la instancia del generador subyacente
            cm = PostgresSaver.from_conn_string(db_url)
            checkpointer = next(cm.gen)
            # Importante: inicializar tablas si no existen
            await checkpointer.setup()
            logger.info("PostgresSaver checkpointer creado para DeyyGraph", session_id=session_id)
        except Exception as e:
            logger.warning("No se pudo crear PostgresSaver, usando InMemorySaver", error=str(e))
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
            logger.info("InMemorySaver checkpointer creado para DeyyGraph", session_id=session_id)

    # 4. Crear gráfo
    graph = build_deyy_graph(
        llm=llm,
        store=store,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )

    return graph
