#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Context Variables compartidas para todo el sistema state machine.
Estas variables son por-thread/async y permiten inyectar contexto
en herramientas sin pasar parámetros explícitos.
"""

import contextvars
import uuid
from typing import Optional

from db.models import ProjectAgentConfig

# ============================================
# CONTEXT VARS
# ============================================

_phone_context = contextvars.ContextVar('phone_number', default=None)
_project_context = contextvars.ContextVar('project_id', default=None)
_project_config_context = contextvars.ContextVar('project_config', default=None)


# ============================================
# SETTERS
# ============================================

def set_current_phone(phone: str) -> contextvars.Token:
    """Set current phone number in context"""
    return _phone_context.set(phone)


def set_current_project(
    project_id: uuid.UUID,
    project_config: Optional[ProjectAgentConfig] = None
) -> tuple[contextvars.Token, contextvars.Token]:
    """Set current project in context, returns (project_token, config_token)"""
    project_token = _project_context.set(project_id)
    config_token = _project_config_context.set(project_config)
    return (project_token, config_token)


# ============================================
# GETTERS
# ============================================

def get_current_phone() -> str:
    """Get current phone number from context"""
    phone = _phone_context.get()
    if not phone:
        raise ValueError("No phone number set in context")
    return phone


def get_current_project_id() -> Optional[uuid.UUID]:
    """Get current project_id from context"""
    return _project_context.get()


def get_current_project_config() -> Optional[ProjectAgentConfig]:
    """Get current project_config from context"""
    return _project_config_context.get()


# ============================================
# RESETERS
# ============================================

def reset_phone(token: contextvars.Token) -> None:
    """Reset phone context"""
    _phone_context.reset(token)


def reset_project(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    """Reset project context"""
    project_token, config_token = tokens
    _project_context.reset(project_token)
    _project_config_context.reset(config_token)
