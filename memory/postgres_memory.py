#!/usr/bin/env python3
"""
Memoria PostgreSQL usando tabla única (langchain_memory)
Implementación limpia y escalable
"""

from typing import List, Optional, Any, Dict
from datetime import datetime, timezone
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from db.models import LangchainMemory, UserProfile, AgentState
import structlog
import uuid

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

    async def get_history(self, session_id: str, project_id: Optional[uuid.UUID] = None, limit: Optional[int] = None) -> List[BaseMessage]:
        """
        Obtiene historial de mensajes para una sesión.

        Args:
            session_id: Identificador de sesión (teléfono o UUID)
            project_id: ID del proyecto (para filtrado multi-tenant). Si se provee, filtra por project_id.
            limit: Número máximo de mensajes a devolver (más recientes). None = sin límite.

        Returns:
            Lista de mensajes en orden cronológica
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            stmt = select(LangchainMemory)

            # Filtrar por project_id si se provee
            if project_id:
                stmt = stmt.where(
                    LangchainMemory.project_id == project_id,
                    LangchainMemory.session_id == session_id
                )
            else:
                stmt = stmt.where(LangchainMemory.session_id == session_id)

            stmt = stmt.order_by(LangchainMemory.created_at)

            if limit is not None:
                stmt = stmt.limit(limit)

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

            # Validación: ¿todos los registros se convirtieron?
            if len(history) != len(records):
                logger.warning(
                    "Discrepancia en conversión de historial",
                    session_id=session_id,
                    project_id=str(project_id) if project_id else None,
                    records_total=len(records),
                    messages_converted=len(history),
                    unconverted_types=[r.type for r in records if r.type not in ["human", "ai"]]
                )

            logger.debug(
                "Historial recuperado",
                session_id=session_id,
                project_id=str(project_id) if project_id else None,
                message_count=len(history),
                records_found=len(records)
            )
            return history

    async def add_message(
        self,
        session_id: str,
        message: BaseMessage,
        project_id: Optional[uuid.UUID] = None
    ) -> None:
        """
        Añade mensaje al historial.

        Args:
            session_id: Identificador de sesión
            message: Mensaje de LangChain (HumanMessage o AIMessage)
            project_id: ID del proyecto (obligatorio para multi-tenant)
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
                project_id=project_id,
                type=msg_type,
                content=message.content,
                created_at=datetime.now(timezone.utc)
            )
            session.add(record)
            await session.flush()
            await session.commit()  # ✅ CRÍTICO: Persistir la transacción

            logger.debug(
                "Mensaje guardado en memoria",
                session_id=session_id,
                project_id=str(project_id) if project_id else None,
                type=msg_type,
                content_length=len(message.content)
            )

    async def clear_session(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> None:
        """
        Limpia historial de una sesión.

        Args:
            session_id: Identificador de sesión
            project_id: ID del proyecto (opcional, para mayor seguridad)
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            stmt = delete(LangchainMemory).where(LangchainMemory.session_id == session_id)
            if project_id:
                stmt = stmt.where(LangchainMemory.project_id == project_id)

            await session.execute(stmt)
            await session.flush()
            logger.info(
                "Sesión limpiada",
                session_id=session_id,
                project_id=str(project_id) if project_id else None
            )

    async def cleanup_expired(self, expiry_hours: int) -> int:
        """
        Limpia registros antiguos de langchain_memory basado en created_at.
        Para Fase 1, se usa la función SQL cleanup_old_memory() que respeta el TTL definido en la función (24h por defecto).
        El parámetro expiry_hours se ignora por ahora porque la función SQL tiene su propio TTL.
        En Fase 2 se podría parametrizar.
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        try:
            async with get_async_session() as session:
                # Llamar a la función de cleanup definida en la migración
                result = await session.execute(
                    text("SELECT cleanup_old_memory() as deleted_count")
                )
                row = result.fetchone()
                deleted_count = row[0] if row else 0

                logger.info(
                    "Cleanup de memoria completado",
                    deleted_records=deleted_count,
                    ttl_hours=expiry_hours
                )
                return deleted_count
        except Exception as e:
            logger.error("Error en cleanup de memoria", error=str(e), exc_info=True)

    # ============================================
    # USER PROFILES MANAGEMENT
    # ============================================

    async def get_user_profile(self, phone_number: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """
        Obtiene perfil de usuario desde UserProfile table.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto (opcional)

        Returns:
            Dict con datos del perfil o None
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(UserProfile.phone_number == phone_number)
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                if profile:
                    return {
                        "phone_number": profile.phone_number,
                        "project_id": str(profile.project_id) if profile.project_id else None,
                        "preferences": profile.preferences or {},
                        "last_appointment": profile.last_appointment.isoformat() if profile.last_appointment else None,
                        "last_appointment_service": profile.last_appointment_service,
                        "notes": profile.notes or "",
                        "extracted_facts": profile.extracted_facts or {},
                        "total_conversations": profile.total_conversations or 0,
                        "first_seen": profile.first_seen.isoformat() if profile.first_seen else None,
                        "last_seen": profile.last_seen.isoformat() if profile.last_seen else None
                    }
                return None

        except Exception as e:
            logger.error("Error obteniendo user profile", phone=phone_number, error=str(e))
            return None

    async def create_or_update_profile(
        self,
        phone_number: str,
        project_id: Optional[uuid.UUID] = None,
        **updates
    ) -> Dict[str, Any]:
        """
        Crea o actualiza perfil de usuario.

        Args:
            phone_number: Número de teléfono
            project_id: ID del proyecto
            **updates: Campos a actualizar (preferences, notes, extracted_facts, total_conversations)

        Returns:
            Dict con el perfil actualizado
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                # Buscar existente
                stmt = select(UserProfile).where(UserProfile.phone_number == phone_number)
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)

                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                now = datetime.now(timezone.utc)

                if profile is None:
                    # Crear nuevo
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        preferences=updates.get('preferences', {}),
                        notes=updates.get('notes', ''),
                        extracted_facts=updates.get('extracted_facts', {}),
                        total_conversations=updates.get('total_conversations', 0),
                        first_seen=now,
                        last_seen=now
                    )
                    session.add(profile)
                    await session.flush()
                else:
                    # Actualizar existente
                    if 'preferences' in updates and updates['preferences'] is not None:
                        if profile.preferences is None:
                            profile.preferences = {}
                        profile.preferences.update(updates['preferences'])
                    if 'notes' in updates and updates['notes'] is not None:
                        existing_notes = profile.notes or ""
                        profile.notes = (existing_notes + "\n" + updates['notes']).strip() if existing_notes else updates['notes']
                    if 'extracted_facts' in updates and updates['extracted_facts'] is not None:
                        if profile.extracted_facts is None:
                            profile.extracted_facts = {}
                        profile.extracted_facts.update(updates['extracted_facts'])
                    if 'total_conversations' in updates:
                        profile.total_conversations = updates['total_conversations']
                    profile.last_seen = now

                await session.flush()
                await session.commit()

                # Devolver dict
                return {
                    "phone_number": profile.phone_number,
                    "project_id": str(profile.project_id) if profile.project_id else None,
                    "preferences": profile.preferences or {},
                    "notes": profile.notes or "",
                    "extracted_facts": profile.extracted_facts or {},
                    "total_conversations": profile.total_conversations or 0,
                    "first_seen": profile.first_seen.isoformat() if profile.first_seen else None,
                    "last_seen": profile.last_seen.isoformat() if profile.last_seen else None
                }

        except Exception as e:
            logger.error("Error creando/actualizando profile", phone=phone_number, error=str(e), exc_info=True)
            raise

    async def increment_user_conversation_count(self, phone_number: str, project_id: Optional[uuid.UUID] = None) -> None:
        """Incrementa contador de conversaciones."""
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(UserProfile.phone_number == phone_number)
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)
                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                now = datetime.now(timezone.utc)

                if profile:
                    profile.total_conversations = (profile.total_conversations or 0) + 1
                    profile.last_seen = now
                else:
                    # Crear perfil con count=1
                    profile = UserProfile(
                        phone_number=phone_number,
                        project_id=project_id,
                        total_conversations=1,
                        first_seen=now,
                        last_seen=now
                    )
                    session.add(profile)

                await session.flush()
                await session.commit()

        except Exception as e:
            logger.error("Error incrementando conversation count", phone=phone_number, error=str(e))

    async def update_user_last_seen(self, phone_number: str, project_id: Optional[uuid.UUID] = None) -> None:
        """Actualiza última vez visto."""
        if not self._initialized:
            await self.initialize()

        from db import get_async_session

        try:
            async with get_async_session() as session:
                stmt = select(UserProfile).where(UserProfile.phone_number == phone_number)
                if project_id:
                    stmt = stmt.where(UserProfile.project_id == project_id)
                result = await session.execute(stmt)
                profile = result.scalar_one_or_none()

                if profile:
                    profile.last_seen = datetime.now(timezone.utc)
                    await session.flush()
                    await session.commit()
                # Si no existe, no hacemos nada

        except Exception as e:
            logger.error("Error updating last_seen", phone=phone_number, error=str(e))
            return 0

    # ============================================
    # USER PROFILES (Memoria a largo plazo)
    # ============================================

    async def get_user_profile(self, phone_number: str, project_id: uuid.UUID) -> Optional[UserProfile]:
        """
        Obtiene el perfil de un usuario.

        Args:
            phone_number: Número normalizado (E.164)
            project_id: ID del proyecto

        Returns:
            UserProfile si existe, None si no
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            stmt = select(UserProfile).where(
                UserProfile.phone_number == phone_number,
                UserProfile.project_id == project_id
            )
            result = await session.execute(stmt)
            profile = result.scalar_one_or_none()
            return profile

    async def create_or_update_profile(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        **updates
    ) -> UserProfile:
        """
        Crea o actualiza un perfil de usuario.

        Args:
            phone_number: Número normalizado
            project_id: ID del proyecto
            **updates: Campos a actualizar (preferences, notes, last_appointment, etc.)

        Returns:
            UserProfile (creado o actualizado)
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            # Buscar existente
            stmt = select(UserProfile).where(
                UserProfile.phone_number == phone_number,
                UserProfile.project_id == project_id
            )
            result = await session.execute(stmt)
            profile = result.scalar_one_or_none()

            if profile:
                # Actualizar
                for key, value in updates.items():
                    if hasattr(profile, key):
                        setattr(profile, key, value)
                profile.updated_at = datetime.now(timezone.utc)
                logger.info("UserProfile actualizado", phone=phone_number, updates=list(updates.keys()))
            else:
                # Crear nuevo
                profile = UserProfile(
                    phone_number=phone_number,
                    project_id=project_id,
                    **updates
                )
                session.add(profile)
                logger.info("UserProfile creado", phone=phone_number)

            await session.flush()
            await session.commit()
            return profile

    async def increment_user_conversation_count(self, phone_number: str, project_id: uuid.UUID) -> None:
        """
        Incrementa el contador de conversaciones del usuario.
        Si el perfil no existe, lo crea con count=1.
        """
        profile = await self.get_user_profile(phone_number, project_id)
        if profile:
            profile.total_conversations += 1
        else:
            profile = UserProfile(
                phone_number=phone_number,
                project_id=project_id,
                total_conversations=1
            )
            from db import get_async_session
            async with get_async_session() as session:
                session.add(profile)
                await session.flush()
                await session.commit()
        logger.debug("Conversación incrementada", phone=phone_number, total=profile.total_conversations)

    async def update_user_last_seen(self, phone_number: str, project_id: uuid.UUID) -> None:
        """
        Actualiza el timestamp de última vez visto.
        Crea perfil si no existe.
        """
        profile = await self.get_user_profile(phone_number, project_id)
        from db import get_async_session
        async with get_async_session() as session:
            if profile:
                profile.last_seen = datetime.now(timezone.utc)
            else:
                profile = UserProfile(
                    phone_number=phone_number,
                    project_id=project_id,
                    last_seen=datetime.now(timezone.utc)
                )
                session.add(profile)
            await session.flush()
            await session.commit()

    async def extract_and_save_facts_from_conversation(
        self,
        phone_number: str,
        project_id: uuid.UUID,
        user_message: str,
        agent_response: str
    ) -> None:
        """
        Extrae hechos relevantes de la conversación y los guarda en el perfil.
        Implementación simple basada en patrones (fase 1).
        Fases posteriores usarán LLM para extracción más profunda.
        """
        profile = await self.get_user_profile(phone_number, project_id)
        if not profile:
            profile = UserProfile(phone_number=phone_number, project_id=project_id)

        facts = dict(profile.extracted_facts) if profile.extracted_facts else {}

        # Extracción simple por patrones (ejemplo: detectar servicio mencionado)
        user_msg_lower = user_message.lower()

        # Detectar servicios de dental
        service_keywords = {
            "limpieza": ["limpieza", "profilaxis"],
            "extracción": ["extracción", "sacar", "muela"],
            "empaste": ["empaste", "caries"],
            "ortodoncia": ["ortodoncia", "frenos", "braces"],
            "consult": ["consulta", "revisión", "chequeo"]
        }

        for service, keywords in service_keywords.items():
            if any(k in user_msg_lower for k in keywords):
                facts["mentioned_service"] = service
                break

        # Detectar preferencias de tiempo
        time_keywords = {
            "mañana": ["mañana", "por la mañana", "am"],
            "tarde": ["tarde", "por la tarde", "pm"],
            "noche": ["noche"]
        }
        for time_pref, keywords in time_keywords.items():
            if any(k in user_msg_lower for k in keywords):
                facts["time_preference"] = time_pref
                break

        # Guardar hechos extraídos
        profile.extracted_facts = facts
        profile.last_seen = datetime.now(timezone.utc)

        from db import get_async_session
        async with get_async_session() as session:
            # Si profile no existía, añadirlo
            if not profile.id:
                session.add(profile)
            await session.flush()
            await session.commit()

        logger.debug("Hechos extraídos del perfil", phone=phone_number, facts=facts)

    # ============================================
    # STATE MACHINE (SupportState)
    # ============================================

    async def get_state(self, session_id: str, project_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """
        Obtiene el estado de SupportState desde agent_states tabla.

        Args:
            session_id: ID de sesión (teléfono normalizado)
            project_id: ID del proyecto (opcional, para filtrado multi-tenant)

        Returns:
            Dict con el estado completo, o None si no existe
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            stmt = select(AgentState).where(AgentState.session_id == session_id)

            if project_id:
                stmt = stmt.where(AgentState.project_id == project_id)

            # Ordenar por updated_at DESC, limit 1
            stmt = stmt.order_by(AgentState.updated_at.desc()).limit(1)

            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

            if record:
                return record.state
            return None

    async def save_state(self, session_id: str, state: Dict[str, Any], project_id: Optional[uuid.UUID] = None) -> None:
        """
        Guarda el estado de SupportState en agent_states tabla.

        Args:
            session_id: ID de sesión
            state: Estado completo (dict) a guardar
            project_id: ID del proyecto (opcional)
        """
        if not self._initialized:
            await self.initialize()

        from db import get_async_session
        async with get_async_session() as session:
            # Buscar registro existente
            stmt = select(AgentState).where(AgentState.session_id == session_id)
            if project_id:
                stmt = stmt.where(AgentState.project_id == project_id)

            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Actualizar existente
                existing.state = state
                existing.updated_at = datetime.now(timezone.utc)
                logger.debug(
                    "Estado actualizado",
                    session_id=session_id,
                    project_id=str(project_id) if project_id else None,
                    keys=list(state.keys())
                )
            else:
                # Crear nuevo
                record = AgentState(
                    session_id=session_id,
                    project_id=project_id,
                    state=state,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(record)
                logger.debug(
                    "Estado creado",
                    session_id=session_id,
                    project_id=str(project_id) if project_id else None,
                    keys=list(state.keys())
                )

            await session.flush()
            await session.commit()
