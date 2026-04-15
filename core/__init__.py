# -*- coding: utf-8 -*-
"""
Core package - Arcadium Automation
"""

from .config import get_settings, Settings
# Note: ArcadiumAPI and create_app are imported lazily to avoid circular imports.
# Import them directly from core.orchestrator when needed.

__all__ = [
    "get_settings",
    "Settings",
    # "ArcadiumAPI",
    # "create_app"
]
