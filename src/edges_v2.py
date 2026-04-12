"""
Edges del grafo V2 (arquitectura ReAct).

Routing puro — lee estado, retorna string, nunca muta.
El grafo V2 tiene solo 5 nodos, por eso los edges son triviales.
"""

from __future__ import annotations

from src.state import ArcadiumState

MAX_TOOL_ITERATIONS = 6


def edge_after_react(state: ArcadiumState) -> str:
    """
    Después de node_react_loop:
    - Si el LLM devolvió tool_calls → ejecutar tools
    - Si no → formatear respuesta final
    """
    pending = state.get("pending_tool_calls", [])
    if pending:
        return "execute_tools"
    return "format_response"


def edge_after_execute_tools(state: ArcadiumState) -> str:
    """
    Después de node_execute_tools:
    - Si hay más iteraciones disponibles → volver al LLM con los ToolMessages
    - Si se agotaron las iteraciones → formatear respuesta (safety valve)
    """
    iterations = state.get("_tool_iterations", 0)
    if iterations < MAX_TOOL_ITERATIONS:
        return "react_loop"
    return "format_response"
