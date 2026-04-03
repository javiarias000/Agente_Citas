#!/usr/bin/env python3
"""
Memoria PostgreSQL usando tabla única (langchain_memory)
Implementación limpia y escalable
"""

from typing import List
from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from db.models import LangchainMemory
import structlog

logger = structlog.get_logger("memory.postgres")


class PostgresStorage:
    """
    Backend de memoria PostgreSQL usando tabla única.
    Todas las sesiones comparten la misma tabla (escalable).
    """

    def __init__(self):
        """Inicializa - necesita ser await initialize()"""
        self._initialized = False
        logger.info("PostgresStorage inicializado")

    async def initialize(self):
        """Asegura que las tablas existen (no crea nada, confía en migraciones)"""
        # Las tablas se crean al inicio de la app via Base.metadata.create_all()
        self._initialized = True
        logger.info("PostgreSQLMemory inicializado")

    async def get_history(self, session_id: str) -> List[BaseMessage]:
        """
        Obtiene historial de mensajes para una sesión.

        Args:
            session_id: Identificador de sesión (teléfono)

        Returns:
            Lista de mensajes en orden cronológico
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            stmt = select(LangchainMemory).where(
                LangchainMemory.session_id == session_id
            ).order_by(LangchainMemory.created_at)

            result = await session.execute(stmt)
            records = result.scalars().all()

            # Convertir a mensajes de LangChain
            history = []
            for record in records:
                if record.type == "human":
                    history.append(HumanMessage(content=record.content))
                elif record.type == "ai":
                    history.append(AIMessage(content=record.content))
                # Ignorar otros tipos

            logger.debug(
                "Historial recuperado",
                session_id=session_id,
                message_count=len(history)
            )
            return history

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage
    ) -> None:
        """
        Añade mensaje al historial.

        Args:
            session_id: Identificador de sesión
            message: Mensaje de LangChain (HumanMessage o AIMessage)
        """
        if not self._initialized:
            await self.initialize()

        if isinstance(message, HumanMessage):
            msg_type = "human"
        elif isinstance(message, AIMessage):
            msg_type = "ai"
        else:
            # No guardar otros tipos (SystemMessage, etc.)
            logger.debug(
                "Ignorando tipo de mensaje no manejado",
                type=type(message).__name__
            )
            return

        from db import get_async_session
        async with get_async_session() as session:
            record = LangchainMemory(
                session_id=session_id,
                type=msg_type,
                content=message.content,
                created_at=datetime.now(timezone.utc)
            )
            session.add(record)
            await session.flush()

            logger.debug(
                "Mensaje guardado en memoria",
                session_id=session_id,
                type=msg_type,
                content_length=len(message.content)
            )

    async def clear_session(self, session_id: str) -> None:
        """
        Limpia historial de una sesión.
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            await session.execute(
                delete(LangchainMemory).where(
                    LangchainMemory.session_id == session_id
                )
            )
            await session.flush()
            logger.info("Sesión limpiada", session_id=session_id)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """
        Limpia sesiones expiradas basado en tiempo de última actividad.
        NOTA: Necesitarías una columna `last_activity` en LangchainMemory
        para implementar esto eficientemente. Por ahora retorna 0.
        """
        logger.warning(
            "cleanup_expired no implementado para PostgreSQLMemory (requiere last_activity)"
        )
        return 0
