"""
Constructor del grafo V2 (arquitectura ReAct).

5 nodos vs 20+ del V1:
  entry_v2 → react_loop ⇄ execute_tools → format_response → save_state_v2

Dependencias inyectadas vía functools.partial:
  - store: BaseStore para historial y estado
  - llm_with_tools: ChatOpenAI con tools ya vinculados
  - tool_map: dict[str, BaseTool] para execute_tools
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, Optional

import structlog
from langgraph.graph import END, START, StateGraph

from src.confirmation_interceptor import node_confirmation_interceptor
from src.edges_v2 import edge_after_execute_tools, edge_after_interceptor, edge_after_react
from src.nodes_v2 import (
    node_entry_v2,
    node_execute_tools,
    node_format_response,
    node_react_loop,
    node_save_state_v2,
)
from src.state import ArcadiumState

logger = structlog.get_logger("langgraph.graph_v2")


def build_graph_v2(
    llm: Any,
    store: Any = None,
    calendar_service: Any = None,
    db_service: Any = None,
    vector_store: Any = None,
) -> StateGraph:
    """
    Construye el StateGraph V2.

    Args:
        llm: ChatOpenAI (o compatible) — SIN tools aún; aquí se vinculan.
        store: BaseStore para persistencia de mensajes y estado del agente.
        calendar_service: GoogleCalendarService wrapper.
        db_service: AppointmentService.
        vector_store: No usado en V2 (la memoria se inyecta antes de invocar).
    """
    from src.tools.calendar_tools import (
        make_book_appointment_tool,
        make_cancel_appointment_tool,
        make_check_availability_tool,
        make_lookup_appointments_tool,
        make_reschedule_appointment_tool,
    )
    from src.tools.memory_tools_v2 import make_save_patient_memory_tool

    # ── Construir tools con los servicios inyectados ───────────────────────────
    tools = [
        make_check_availability_tool(calendar_service),
        make_book_appointment_tool(calendar_service, db_service),
        make_cancel_appointment_tool(calendar_service, db_service),
        make_lookup_appointments_tool(calendar_service),
        make_reschedule_appointment_tool(calendar_service, db_service),
        make_save_patient_memory_tool(),
    ]

    # tool_map: nombre → callable para node_execute_tools
    tool_map: Dict[str, Any] = {t.name: t for t in tools}

    # LLM con tools vinculados (único objeto que el LLM usa para razonar)
    llm_with_tools = llm.bind_tools(tools)

    # ── Crear grafo ────────────────────────────────────────────────────────────
    graph = StateGraph(ArcadiumState)

    # Nodo 1: entry
    graph.add_node("entry_v2", partial(node_entry_v2, store=store))

    # Nodo 2: confirmation_interceptor (determinista — sin LLM)
    graph.add_node("confirmation_interceptor", node_confirmation_interceptor)

    # Nodo 3: react_loop (única LLM call por iteración)
    graph.add_node(
        "react_loop",
        partial(node_react_loop, llm_with_tools=llm_with_tools),
    )

    # Nodo 4: execute_tools (ejecuta todos los pending tool_calls)
    graph.add_node(
        "execute_tools",
        partial(node_execute_tools, tool_map=tool_map),
    )

    # Nodo 5: format_response (determinista para éxito, LLM para el resto)
    graph.add_node("format_response", node_format_response)

    # Nodo 6: save_state
    graph.add_node("save_state_v2", partial(node_save_state_v2, store=store))

    # ── Edges ──────────────────────────────────────────────────────────────────
    graph.add_edge(START, "entry_v2")
    graph.add_edge("entry_v2", "confirmation_interceptor")

    graph.add_conditional_edges(
        "confirmation_interceptor",
        edge_after_interceptor,
        {
            "execute_tools": "execute_tools",
            "react_loop": "react_loop",
        },
    )

    graph.add_conditional_edges(
        "react_loop",
        edge_after_react,
        {
            "execute_tools": "execute_tools",
            "format_response": "format_response",
        },
    )

    graph.add_conditional_edges(
        "execute_tools",
        edge_after_execute_tools,
        {
            "react_loop": "react_loop",
            "format_response": "format_response",
        },
    )

    graph.add_edge("format_response", "save_state_v2")
    graph.add_edge("save_state_v2", END)

    logger.info("Graph V2 (ReAct) construido", tools=[t.name for t in tools])
    return graph


def compile_graph_v2(
    llm=None,
    store=None,
    calendar_service=None,
    db_service=None,
    vector_store=None,
    checkpointer=None,
) -> Any:
    """
    Construye y compila el grafo V2.

    Sin interrupt_before por defecto — el HITL en V2 se maneja a nivel
    de tool (el LLM debe pedir confirmación explícita antes de llamar
    book/cancel/reschedule, no necesitamos interrumpir el grafo).
    """
    graph = build_graph_v2(
        llm=llm,
        store=store,
        calendar_service=calendar_service,
        db_service=db_service,
        vector_store=vector_store,
    )

    if checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
    else:
        compiled = graph.compile()

    logger.info("Graph V2 compilado")
    return compiled
