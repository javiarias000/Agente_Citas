#!/usr/bin/env python3
"""
Memoria PostgreSQL usando tabla única (langchain_memory)

FIXES APLICADOS:
- [CRÍTICO] get_user_profile duplicado (dos definiciones con retornos distintos) → unificado en uno
- [CRÍTICO] create_or_update_profile duplicado → unificado
- [CRÍTICO] increment_user_conversation_count abría objeto detached fuera de sesión → todo en una sesión
- [CRÍTICO] update_user_last_seen mismo problema de sesión detached → corregido
- [IMPORTANTE] clear_session faltaba commit → agregado
- [IMPORTANTE] cleanup_expired no retornaba en bloque except → retorna 0
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import delete, select, text

from db.models import AgentState, LangchainMemory, UserProfile

logger = structlog.get_logger("memory.postgres")


class PostgresStorage:
    """
    Backend de memoria PostgreSQL usando tabla única.
    Todas las sesiones comparten la misma tabla (escalable).
    """

    def __init__(self):
        self._initialized = False
        logger.info("PostgresStorage creado")

    async def initialize(self):
        """Asegura que las tablas existen (confía en migraciones)."""
        self._initialized = True
        logger.info("PostgreSQLMemory inicializado")

    # ============================================
    # HISTORIAL
    # ============================================

    async def get_history(
        self,
        session_id: str,
        project_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> List[BaseMessage]:
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        async with get_async_session() as session:
            stmt = select(LangchainMemory)

            if project_id:
                stmt = stmt.where(
                    LangchainMemory.project_id == project_id,
                    LangchainMemory.session_id == session_id,
                )
            else:
                stmt = stmt.where(LangchainMemory.session_id == session_id)

            stmt = stmt.order_by(LangchainMemory.created_at)

            if limit is not None:
                stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            records = result.scalars().all()

            history = []
            for record in records:
                additional_kwargs = record.additional_kwargs or {}
                if record.type == "human":
                    history.append(HumanMessage(content=record.content, additional_kwargs=additional_kwargs))
                elif record.type == "ai":
                    history.append(AIMessage(content=record.content, additional_kwargs=additional_kwargs))

            if len(history) != len(records):
                logger.warning(
                    "Discrepancia en conversión de historial",
                    session_id=session_id,
                    records_total=len(records),
                    messages_converted=len(history),
                    unconverted_types=[
                        r.type for r in records if r.type not in ["human", "ai"]
                    ],
                )

            logger.debug(
                "Historial recuperado",
                session_id=session_id,
                message_count=len(history),
            )
            return history

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        if not self._initialized:
            await self.initialize()

        if isinstance(message, HumanMessage):
            msg_type = "human"
        elif isinstance(message, AIMessage):
            msg_type = "ai"
        else:
            logger.debug(
                "Ignorando tipo de mensaje no manejado", type=type(message).__name__
            )
            return

        from db import get_async_session

        async with get_async_session() as session:
            additional_kwargs = getattr(message, 'additional_kwargs', None) or {}
            record = LangchainMemory(
                session_id=session_id,
                project_id=project_id,
                type=msg_type,
                content=message.content,
                additional_kwargs=additional_kwargs,
                created_at=datetime.now(timezone.utc),
            )
            session.add(record)
            await session.flush()
            await session.commit()

            logger.debug(
                "Mensaje guardado",
                session_id=session_id,
                type=msg_type,
                content_length=len(message.content),
            )

    async def clear_session(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        async with get_async_session() as session:
            stmt = delete(LangchainMemory).where(
                LangchainMemory.session_id == session_id
            )
            if project_id:
                stmt = stmt.where(LangchainMemory.project_id == project_id)

            await session.execute(stmt)
            await session.flush()
            await session.commit()  # FIX: faltaba commit
            logger.info("Sesión limpiada", session_id=session_id)

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """FIX: Ahora retorna 0 en caso de excepción (antes retornaba None implícito)."""
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                result = await session.execute(
                    text("SELECT cleanup_old_memory() as deleted_count")
                )
                row = result.fetchone()
                deleted_count = row[0] if row else 0

                logger.info("Cleanup completado", deleted_records=deleted_count)
                return deleted_count
        except Exception as e:
            logger.error("Error en cleanup de memoria", error=str(e), exc_info=True)
            return 0  # FIX: retorno explícito en lugar de None

    # ============================================
    # USER PROFILES
    # FIX: eliminadas las dos definiciones duplicadas de get_user_profile
    #      y create_or_update_profile. Queda UNA versión que retorna Dict
    #      (consistente con lo que espera MemoryManager y las tools).
    # ============================================

    async def get_user_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Obtiene perfil de usuario.
        Retorna Dict (NO el ORM object) para evitar problemas de sesión detached.
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(
                    UserProfile.phone_number == phone_number
                )
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                if profile:
                    return self._profile_to_dict(profile)
                return None

        except Exception as e:
            logger.error(
                "Error obteniendo user profile", phone=phone_number, error=str(e)
            )
            return None

    async def create_or_update_profile(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None, **updates
    ) -> Dict[str, Any]:
        """
        Crea o actualiza perfil de usuario.
        Retorna Dict (NO el ORM object).
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(
                    UserProfile.phone_number == phone_number
                )
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()
                now = datetime.now(timezone.utc)

                if profile is None:
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        preferences=updates.get("preferences", {}),
                        notes=updates.get("notes", ""),
                        extracted_facts=updates.get("extracted_facts", {}),
                        total_conversations=updates.get("total_conversations", 0),
                        first_seen=now,
                        last_seen=now,
                    )
                    session.add(profile)
                else:
                    if "preferences" in updates and updates["preferences"] is not None:
                        existing = profile.preferences or {}
                        existing.update(updates["preferences"])
                        profile.preferences = existing

                    if "notes" in updates and updates["notes"] is not None:
                        existing_notes = profile.notes or ""
                        profile.notes = (
                            (existing_notes + "\n" + updates["notes"]).strip()
                            if existing_notes
                            else updates["notes"]
                        )

                    if (
                        "extracted_facts" in updates
                        and updates["extracted_facts"] is not None
                    ):
                        existing_facts = profile.extracted_facts or {}
                        existing_facts.update(updates["extracted_facts"])
                        profile.extracted_facts = existing_facts

                    if "total_conversations" in updates:
                        profile.total_conversations = updates["total_conversations"]

                    profile.last_seen = now

                await session.flush()
                await session.commit()
                return self._profile_to_dict(profile)

        except Exception as e:
            logger.error(
                "Error creando/actualizando profile",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
            raise

    async def increment_user_conversation_count(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        """
        FIX: Todo en UNA sesión para evitar objeto detached.
        Antes abría una sesión para get_user_profile y otra para commit → DetachedInstanceError.
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(
                    UserProfile.phone_number == phone_number
                )
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()
                now = datetime.now(timezone.utc)

                if profile:
                    profile.total_conversations = (profile.total_conversations or 0) + 1
                    profile.last_seen = now
                else:
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        total_conversations=1,
                        first_seen=now,
                        last_seen=now,
                    )
                    session.add(profile)

                await session.flush()
                await session.commit()

        except Exception as e:
            logger.error(
                "Error incrementando conversation count",
                phone=phone_number,
                error=str(e),
            )

    async def update_user_last_seen(
        self, phone_number: str, project_id: Optional[uuid.UUID] = None
    ) -> None:
        """
        FIX: Todo en UNA sesión para evitar objeto detached.
        Antes abría una sesión para get y otra para commit.
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(
                    UserProfile.phone_number == phone_number
                )
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()
                now = datetime.now(timezone.utc)

                if profile:
                    profile.last_seen = now
                else:
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        last_seen=now,
                        first_seen=now,
                    )
                    session.add(profile)

                await session.flush()
                await session.commit()

        except Exception as e:
            logger.error(
                "Error actualizando last_seen", phone=phone_number, error=str(e)
            )

    async def extract_and_save_facts_from_conversation(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        user_message: str,
        agent_response: str,
    ) -> None:
        """Extrae hechos relevantes de la conversación y los guarda en el perfil."""
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(
                    UserProfile.phone_number == phone_number
                )
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()
                now = datetime.now(timezone.utc)

                if profile is None:
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        first_seen=now,
                        last_seen=now,
                    )
                    session.add(profile)

                facts = dict(profile.extracted_facts or {})
                user_msg_lower = user_message.lower()

                service_keywords = {
                    "limpieza": ["limpieza", "profilaxis"],
                    "extracción": ["extracción", "sacar", "muela"],
                    "empaste": ["empaste", "caries"],
                    "ortodoncia": ["ortodoncia", "frenos", "braces"],
                    "consulta": ["consulta", "revisión", "chequeo"],
                }
                for service, keywords in service_keywords.items():
                    if any(k in user_msg_lower for k in keywords):
                        facts["mentioned_service"] = service
                        break

                time_keywords = {
                    "mañana": ["mañana", "por la mañana", "am"],
                    "tarde": ["tarde", "por la tarde", "pm"],
                    "noche": ["noche"],
                }
                for time_pref, keywords in time_keywords.items():
                    if any(k in user_msg_lower for k in keywords):
                        facts["time_preference"] = time_pref
                        break

                profile.extracted_facts = facts
                profile.last_seen = now

                await session.flush()
                await session.commit()
                logger.debug("Hechos extraídos", phone=phone_number, facts=facts)

        except Exception as e:
            logger.error(
                "Error extrayendo hechos de conversación",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )

    # ============================================
    # STATE MACHINE
    # ============================================

    async def get_state(
        self, session_id: str, project_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        async with get_async_session() as session:
            stmt = select(AgentState).where(AgentState.session_id == session_id)
            if project_id:
                stmt = stmt.where(AgentState.project_id == project_id)
            stmt = stmt.order_by(AgentState.updated_at.desc()).limit(1)

            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

            if record:
                return record.state
            return None

    async def save_state(
        self,
        session_id: str,
        state: Dict[str, Any],
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        async with get_async_session() as session:
            stmt = select(AgentState).where(AgentState.session_id == session_id)
            if project_id:
                stmt = stmt.where(AgentState.project_id == project_id)

            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.state = state
                existing.updated_at = datetime.now(timezone.utc)
                logger.debug("Estado actualizado", session_id=session_id)
            else:
                record = AgentState(
                    session_id=session_id,
                    project_id=project_id,
                    state=state,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(record)
                logger.debug("Estado creado", session_id=session_id)

            await session.flush()
            await session.commit()

    # ============================================
    # HELPER PRIVADO
    # ============================================

    def _profile_to_dict(self, profile: UserProfile) -> Dict[str, Any]:
        """Convierte ORM UserProfile a Dict para evitar objetos detached fuera de sesión."""
        return {
            "phone_number": profile.phone_number,
            "project_id": str(profile.project_id) if profile.project_id else None,
            "preferences": profile.preferences or {},
            "notes": profile.notes or "",
            "extracted_facts": profile.extracted_facts or {},
            "total_conversations": profile.total_conversations or 0,
            "first_seen": profile.first_seen.isoformat()
            if profile.first_seen
            else None,
            "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
        }
