#!/usr/bin/env python3
"""
Auth utilities for endpoint protection.
Requiere API_KEY en header Authorization: Bearer <token>
"""

from typing import Optional
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthCredentials
import structlog

logger = structlog.get_logger("auth")

security = HTTPBearer()


async def verify_api_token(credentials: HTTPAuthCredentials = Depends(security)) -> str:
    """
    Verifica token Bearer en header Authorization.
    Retorna el token si es válido, sino lanza HTTPException 401.
    """
    from core.config import get_settings

    settings = get_settings()
    token = credentials.credentials

    # Si no hay API_KEY configurado, endpoint es público
    if not settings.API_KEY:
        logger.warning("API_KEY not configured - endpoint is public")
        return token

    # Validar token
    if token != settings.API_KEY:
        logger.warning("Invalid API token attempt")
        raise HTTPException(
            status_code=401,
            detail="Invalid API token"
        )

    return token


async def verify_api_token_optional(
    credentials: Optional[HTTPAuthCredentials] = Depends(security)
) -> Optional[str]:
    """
    Verifica token Bearer si está presente, pero no lo requiere.
    Retorna el token o None si no está presente.
    """
    if not credentials:
        return None

    from core.config import get_settings

    settings = get_settings()
    token = credentials.credentials

    # Si no hay API_KEY configurado, cualquier token es válido
    if not settings.API_KEY:
        return token

    # Validar token si está presente
    if token != settings.API_KEY:
        logger.warning("Invalid API token attempt")
        raise HTTPException(
            status_code=401,
            detail="Invalid API token"
        )

    return token
