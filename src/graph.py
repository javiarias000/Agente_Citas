"""
Constructor del grafo LangGraph completo.

Conecta todos los nodos, edges, y dependencias en un StateGraph compilado.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

logger = structlog.get_logger("langgraph.graph")


def build_graph(
    llm: Any,
    store: Any = None,
    calendar_service: Any = None,
    db_service: Any = None,
    vector_store: Any = None,
) -> StateGraph:
    """
    Construye el StateGraph de Arcadium con todos los nodos y routing.

    Args:
        llm: ChatOpenAI (o compatible)
        store: BaseStore (PostgresStore o InMemoryStore) para persistencia de mensajes.
        calendar_service: GoogleCalendarService wrapper
        db_service: AppointmentService
        vector_store: BaseStore vectorial para memorias semánticas (MemoryAgentIntegration.store)
    """
    from src.edges import (
        edge_after_adjust_weekend,
        edge_after_check_missing,
        edge_after_confirm,
        edge_after_extract_data,
        edge_after_route_intent,
    )

    # Importar nodos y edges
    from src.nodes import (
        edge_after_generate_response,
        node_adjust_weekend,
        node_book_appointment,
        node_cancel_appointment,
        node_check_availability,
        node_check_missing,
        node_detect_confirmation,
        node_entry,
        node_execute_memory_tools,
        node_extract_data,
        node_extract_intent,
        node_generate_response_with_tools,
        node_prepare_modification,
        node_reschedule_appointment,
        node_route_intent,
        node_save_state,
        node_validate_and_confirm,
    )
    from src.state import ArcadiumState

    # ── Crear graph ──────────────────────────────────────────
    graph = StateGraph(ArcadiumState)

    # ── Nodos deterministas ──────────────────────────────────

    graph.add_node("entry", partial(node_entry, store=store))
    graph.add_node("route_intent", node_route_intent)
    graph.add_node("check_missing", node_check_missing)
    graph.add_node("adjust_weekend", node_adjust_weekend)
    graph.add_node(
        "check_availability",
        partial(node_check_availability, calendar_service=calendar_service),
    )
    graph.add_node("detect_confirmation", node_detect_confirmation)
    graph.add_node("validate_and_confirm", node_validate_and_confirm)
    graph.add_node(
        "book_appointment",
        partial(
            node_book_appointment,
            calendar_service=calendar_service,
            db_service=db_service,
        ),
    )
    graph.add_node(
        "cancel_appointment",
        partial(
            node_cancel_appointment,
            calendar_service=calendar_service,
            db_service=db_service,
        ),
    )
    graph.add_node("prepare_modification", node_prepare_modification)
    graph.add_node(
        "reschedule_appointment",
        partial(
            node_reschedule_appointment,
            calendar_service=calendar_service,
            db_service=db_service,
        ),
    )
    graph.add_node("save_state", partial(node_save_state, store=store))

    # ── Nodos LLM ─────────────────────────────────────────────
    graph.add_node("extract_intent", partial(node_extract_intent, llm=llm))
    graph.add_node("extract_data", partial(node_extract_data, llm=llm))
    graph.add_node(
        "generate_response",
        partial(node_generate_response_with_tools, llm=llm, vector_store=vector_store),
    )
    graph.add_node(
        "execute_memory_tools",
        partial(node_execute_memory_tools, vector_store=vector_store),
    )

    # ── Edges: entrada siempre → entry ───────────────────────
    graph.add_edge(START, "entry")

    # entry → route_intent (siempre)
    graph.add_edge("entry", "route_intent")

    # route_intent → routing edge
    graph.add_conditional_edges(
        "route_intent",
        edge_after_route_intent,
        {
            "extract_intent": "extract_intent",
            "check_missing": "check_missing",
            "check_availability": "check_availability",
            "handle_modification": "prepare_modification",
            "generate_response": "generate_response",
        },
    )

    # prepare_modification → detect_confirmation (siempre)
    graph.add_edge("prepare_modification", "detect_confirmation")

    # extract_intent → check_missing (ya se determinó el intent, ahora verificar datos)
    graph.add_edge("extract_intent", "check_missing")

    # check_missing → routing
    graph.add_conditional_edges(
        "check_missing",
        edge_after_check_missing,
        {
            "extract_data": "extract_data",
            "check_availability": "check_availability",
            "generate_response": "generate_response",
        },
    )

    # extract_data → routing
    graph.add_conditional_edges(
        "extract_data",
        edge_after_extract_data,
        {
            "adjust_weekend": "adjust_weekend",
            "check_missing": "check_missing",
            "generate_response": "generate_response",
        },
    )

    # adjust_weekend → check_missing (re-evaluar después del ajuste)
    graph.add_conditional_edges(
        "adjust_weekend",
        edge_after_adjust_weekend,
        {
            "check_missing": "check_missing",
        },
    )

    # check_availability → generate_response (mostrar slots)
    graph.add_edge("check_availability", "generate_response")

    # generate_response → routing condicional (execute_memory_tools o save_state)
    # luego de execute_memory_tools, vuelve a generate_response para segunda ronda
    graph.add_conditional_edges(
        "generate_response",
        edge_after_generate_response,
        {
            "execute_memory_tools": "execute_memory_tools",
            "save_state": "save_state",
        },
    )
    graph.add_edge("execute_memory_tools", "generate_response")
    graph.add_edge("save_state", END)

    # --- Para el segundo turno (confirmación, re-entrada) ---
    # detect_confirmation → routing
    graph.add_conditional_edges(
        "detect_confirmation",
        edge_after_confirm,
        {
            "book_appointment": "book_appointment",
            "cancel_appointment": "cancel_appointment",
            "validate_slot": "validate_and_confirm",
            "generate_response": "generate_response",
        },
    )

    # validate_and_confirm → generate_response
    graph.add_edge("validate_and_confirm", "generate_response")

    # book_appointment → generate_response (confirmación de éxito)
    graph.add_edge("book_appointment", "generate_response")

    # cancel_appointment → generate_response (confirmación de cancelación)
    graph.add_edge("cancel_appointment", "generate_response")

    # reschedule_appointment → generate_response (confirmación de reagendamiento)
    graph.add_edge("reschedule_appointment", "generate_response")

    # ── Escalation check (opcional) ──────────────────────────
    # No se agrega como conditional edge en el graph principal
    # porque el grafo siempre termina en save_state → END.
    # La lógica de escalación se evalúa en save_state y
    # generate_response genera el mensaje "Voy a pasarle con alguien".

    logger.info("Graph de Arcadium construido")

    return graph


def compile_graph(
    llm=None,
    store=None,
    calendar_service=None,
    db_service=None,
    vector_store=None,
    checkpointer=None,
):
    """
    Construye y compila el grafo con checkpointer.
    """

    graph = build_graph(
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

    logger.info("Graph de Arcadium compilado")

    return compiled
