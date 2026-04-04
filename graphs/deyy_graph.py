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
import asyncio
import json
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # For Python < 3.9

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

class DeyyState(TypedDict, total=False):
    """Estado mínimo para DeyyAgent"""
    messages: Annotated[List[BaseMessage], add_messages]
    phone_number: str
    project_id: Optional[uuid.UUID]
    context_vars: Optional[Dict[str, Any]]  # Variables de contexto (fechas, etc.)
    current_user_message: str  # Mensaje del usuario para este turno (no guardado aún)
    initial_message_count: int  # Para deduplicación: número de mensajes ya guardados antes de este turno


def create_initial_deyy_state(
    phone_number: str,
    project_id: Optional[uuid.UUID] = None,
    system_prompt: Optional[str] = None,
    context_vars: Optional[Dict[str, Any]] = None
) -> DeyyState:
    """
    Crea estado inicial para DeyyAgent.

    Args:
        phone_number: Número del usuario
        project_id: ID del proyecto
        system_prompt: Prompt del sistema (opcional)
        context_vars: Variables de contexto (fechas calculadas, etc.)

    Returns:
        DeyyState inicializado
    """
    return DeyyState(
        messages=[],
        phone_number=phone_number,
        project_id=project_id,
        context_vars=context_vars
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
    # Cargar solo últimos 50 mensajes para ventana de contexto
    history = await store.get_history(session_id, limit=50)

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
        # Copy to avoid mutating store's internal list
        state["messages"] = list(history)

    # Marcar cuántos mensajes ya estaban en el store (para evitar duplicados en save)
    # Usar len(history) porque son los mensajes que ya estaban guardados.
    # El mensaje actual (current_user_message) se añadirá después y no cuenta como ya guardado.
    state["initial_message_count"] = len(history)

    # Si hay un mensaje actual del usuario (current_user_message), añadirlo al estado
    if "current_user_message" in state:
        user_msg_content = state.pop("current_user_message")
        state["messages"].append(HumanMessage(content=user_msg_content))
        # Nota: no incrementamos initial_message_count porque este mensaje es nuevo y no estaba guardado

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

    Ejecuta un bucle de tool calling: el agente puede usar herramientas
    iterativamente hasta generar una respuesta final.

    Args:
        state: Estado actual
        store: Store para persistencia
        llm: Modelo de lenguaje
        tools: Lista de herramientas
        system_prompt: Prompt del sistema (opcional)

    Returns:
        Estado actualizado con respuesta del agente y ToolMessages si aplica
    """
    # Construir prompt
    default_system = "Eres Deyy, asistente especializado en gestión de citas dentales."
    system_prompt_to_use = system_prompt or default_system

    # Obtener context_vars y calcular fechas por defecto
    context_vars = state.get("context_vars", {})
    tz = ZoneInfo("America/Guayaquil")
    now_dt = datetime.now(tz)
    default_current_date = now_dt.strftime("%Y-%m-%d")
    default_current_time = now_dt.strftime("%H:%M")
    default_tomorrow = now_dt + timedelta(days=1)
    default_tomorrow_date = default_tomorrow.strftime("%Y-%m-%d")

    # Formatear placeholders {current_date}, {current_time}, {tomorrow_date}
    try:
        system_prompt_to_use = system_prompt_to_use.format(
            current_date=context_vars.get("fecha_hoy", default_current_date),
            current_time=context_vars.get("hora_actual", default_current_time),
            tomorrow_date=context_vars.get("manana_fecha", default_tomorrow_date)
        )
    except KeyError as e:
        logger.warning("Faltan variables para formatear system prompt", missing=str(e))
        # Si falla, continuamos con el prompt sin formatear (podría no tener placeholders)

    # Enriquecer con bloque de fechas si hay context_vars
    if context_vars:
        fecha_info_parts = []
        if "fecha_legible" in context_vars:
            fecha_info_parts.append(f"Fecha actual: {context_vars['fecha_legible']}")
        if "fecha_hoy" in context_vars:
            fecha_info_parts.append(f"Hoy (ISO): {context_vars['fecha_hoy']}")
        if "manana_fecha" in context_vars:
            fecha_info_parts.append(f"Mañana (ISO): {context_vars['manana_fecha']}")
        if "manana_dia" in context_vars:
            fecha_info_parts.append(f"Mañana (día): {context_vars['manana_dia']}")
        if "hora_actual" in context_vars:
            fecha_info_parts.append(f"Hora actual: {context_vars['hora_actual']}")

        if fecha_info_parts:
            system_prompt_to_use = system_prompt_to_use + "\n\n=== INFORMACIÓN DE FECHAS (USA ESTAS VARIABLES) ===\n" + "\n".join(fecha_info_parts) + "\n\nIMPORTANTE: Usa estas fechas directamente. No calcules fechas tú mismo."

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt_to_use),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    # Crear agente (runnable)
    agent = create_openai_tools_agent(llm, tools, prompt)

    # Preparar input
    all_messages = state.get("messages", [])
    if all_messages:
        user_input = all_messages[-1].content
        chat_history = all_messages[:-1].copy()
    else:
        user_input = ""
        chat_history = []

    # Extraer intermediate_steps iniciales del historial (si hay tool calls previas)
    intermediate_steps = []
    i = 0
    while i < len(chat_history):
        msg = chat_history[i]
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            if i + 1 < len(chat_history):
                next_msg = chat_history[i + 1]
                if isinstance(next_msg, ToolMessage):
                    for tool_call in msg.tool_calls:
                        action = {
                            "tool": tool_call.get("name", ""),
                            "tool_input": tool_call.get("args", {})
                        }
                        observation = next_msg.content
                        intermediate_steps.append((action, observation))
                    i += 1  # Saltar ToolMessage
        i += 1

    # Bucle de ejecución (max_iterations para evitar loops infinitos)
    max_iterations = 5
    current_chat_history = chat_history.copy()
    current_intermediate_steps = intermediate_steps.copy()
    final_ai_message = None

    for iteration in range(max_iterations):
        try:
            # Invocar agente
            result = await agent.ainvoke({
                "input": user_input,
                "chat_history": current_chat_history,
                "intermediate_steps": current_intermediate_steps
            })
        except Exception as e:
            logger.error("Agent invocation failed", iteration=iteration, error=str(e), exc_info=True)
            error_msg = AIMessage(content=f"Error interno del agente: {str(e)}")
            state["messages"].append(error_msg)
            return state

        # Normalizar resultado a AIMessage
        if isinstance(result, AIMessage):
            ai_message = result
        elif isinstance(result, str):
            ai_message = AIMessage(content=result)
        elif isinstance(result, dict):
            output = result.get("output", "")
            ai_message = AIMessage(content=output)
            # Preservar tool_calls si están en el dict
            if "tool_calls" in result:
                ai_message.tool_calls = result["tool_calls"]
        else:
            ai_message = AIMessage(content=str(result))

        # Añadir mensaje AI al estado y al historial actual
        state["messages"].append(ai_message)
        current_chat_history.append(ai_message)

        # Verificar si el mensaje contiene tool_calls
        tool_calls = getattr(ai_message, "tool_calls", [])
        if not tool_calls:
            # No hay más herramientas, esta es la respuesta final
            final_ai_message = ai_message
            logger.debug(
                "Agent response final (no tools)",
                response_len=len(ai_message.content),
                iteration=iteration,
                tools_used=len(current_intermediate_steps)
            )
            break

        # Ejecutar cada tool call
        for tool_call in tool_calls:
            # Extraer tool name
            tool_name = None
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name")
                if not tool_name:
                    func = tool_call.get("function", {})
                    tool_name = func.get("name")
            else:
                tool_name = getattr(tool_call, "name", None) or getattr(tool_call, "function", {}).get("name")

            # Extraer argumentos como diccionario Python
            tool_input = {}
            if isinstance(tool_call, dict):
                args = tool_call.get("args")
                if args is not None:
                    if isinstance(args, dict):
                        tool_input = args
                    elif isinstance(args, str):
                        try:
                            tool_input = json.loads(args)
                        except json.JSONDecodeError:
                            tool_input = {}
                else:
                    func = tool_call.get("function", {})
                    args_data = func.get("arguments", {})
                    if isinstance(args_data, dict):
                        tool_input = args_data
                    elif isinstance(args_data, str):
                        try:
                            tool_input = json.loads(args_data)
                        except json.JSONDecodeError:
                            tool_input = {}
            else:
                # Objeto tool_call (puede tener .args o .function.arguments)
                if hasattr(tool_call, "args") and tool_call.args is not None:
                    tool_input = tool_call.args if isinstance(tool_call.args, dict) else {}
                elif hasattr(tool_call, "function"):
                    func = tool_call.function
                    args_data = getattr(func, "arguments", {})
                    if isinstance(args_data, dict):
                        tool_input = args_data
                    elif isinstance(args_data, str):
                        try:
                            tool_input = json.loads(args_data)
                        except json.JSONDecodeError:
                            tool_input = {}

            # Buscar herramienta
            tool = None
            for t in tools:
                t_name = getattr(t, "name", None)
                if t_name == tool_name:
                    tool = t
                    break

            if not tool:
                logger.warning("Tool not found", tool_name=tool_name)
                observation = f"Error: herramienta '{tool_name}' no disponible"
            else:
                try:
                    # Ejecutar herramienta
                    if hasattr(tool, "arun"):
                        observation = await tool.arun(tool_input)
                    else:
                        loop = asyncio.get_event_loop()
                        observation = await loop.run_in_executor(None, lambda: tool.run(tool_input))
                except Exception as e:
                    observation = f"Error ejecutando {tool_name}: {str(e)}"

            # Crear ToolMessage
            tool_call_id = None
            if isinstance(tool_call, dict):
                tool_call_id = tool_call.get("id", f"call_{len(current_intermediate_steps)}")
            else:
                tool_call_id = getattr(tool_call, "id", f"call_{len(current_intermediate_steps)}")

            tool_msg = ToolMessage(
                content=str(observation),
                tool_call_id=tool_call_id,
                name=tool_name
            )
            state["messages"].append(tool_msg)
            current_chat_history.append(tool_msg)

            # Para intermediate_steps, el tool_input debe ser diccionario Python (no JSON string)
            current_intermediate_steps.append(
                ({"tool": tool_name, "tool_input": tool_input}, str(observation))
            )

        # Fin de la iteración; si quedan tool_calls en el último ai_message, continuará

    # Si después del loop no tenemos final_ai_message (ej: max_iterations alcanzado), usar último AI
    if final_ai_message is None and current_chat_history:
        last_msg = current_chat_history[-1]
        if isinstance(last_msg, AIMessage):
            final_ai_message = last_msg
        else:
            final_ai_message = AIMessage(content="")
    elif final_ai_message is None:
        final_ai_message = AIMessage(content="")

    # Log final
    logger.debug(
        "Agent node completed",
        final_response_len=len(final_ai_message.content),
        total_tools_used=len(current_intermediate_steps) - len(intermediate_steps),
        iterations=iteration+1 if 'iteration' in locals() else 0
    )

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
    initial_count = state.get("initial_message_count", 0)

    # Solo guardar mensajes NUEVOS agregados durante este turno
    new_messages = messages[initial_count:] if initial_count < len(messages) else []

    # Guardar solo mensajes humano y AI (no system)
    for msg in new_messages:
        if isinstance(msg, (HumanMessage, AIMessage)):
            await store.add_message(session_id, msg)

    logger.debug(
        "Messages saved to store",
        session_id=session_id,
        total_messages=len(messages),
        initial_count=initial_count,
        new_messages_count=len([m for m in new_messages if isinstance(m, (HumanMessage, AIMessage))])
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
