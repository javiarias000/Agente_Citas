"""
Stub para langchain.agents.middleware
Estas funciones/excepciones ya no existen en langchain moderno,
pero state_machine_agent las importa. Este módulo provee stubs.
"""
from typing import Any, Callable, Dict


def wrap_model_call(callback: Callable) -> Callable:
    """Wrapper para callbacks (stub)"""
    return callback


class ModelRequest:
    """Request model (stub)"""
    pass


class ModelResponse:
    """Response model (stub)"""
    pass
