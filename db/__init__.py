#!/usr/bin/env python3
"""
DB module - Gestión de sesiones de base de datos
"""

from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, AsyncEngine
from sqlalchemy.orm import sessionmaker
import structlog

logger = structlog.get_logger("db")

# Sesión global (inicializada por la app)
_async_session_maker: Optional[sessionmaker] = None
_engine: Optional[AsyncEngine] = None


def init_session_maker(engine: AsyncEngine) -> sessionmaker:
    """Inicializa el session maker global"""
    global _async_session_maker, _engine
    _engine = engine
    _async_session_maker = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    logger.info("DB session maker inicializado")
    return _async_session_maker


def get_async_session() -> AsyncSession:
    """
    Obtiene una nueva sesión de base de datos.
    Usar como: async with get_async_session() as session:

    Returns:
        AsyncSession
    """
    if _async_session_maker is None:
        raise RuntimeError(
            "DB no inicializada. "
            "Asegúrate de llamar a init_session_maker() en la inicialización."
        )
    return _async_session_maker()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency para FastAPI
    Uso: async def endpoint(session: AsyncSession = Depends(get_db_session))
    """
    async with get_async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_engine() -> Optional[AsyncEngine]:
    """Obtiene el engine global (para crear tablas, etc)"""
    return _engine
