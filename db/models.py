#!/usr/bin/env python3
"""
Modelos de base de datos PostgreSQL
Usa SQLAlchemy 2.0 con async/await
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    String, Text, DateTime, Boolean, ForeignKey, Index, Integer,
    BigInteger, JSON, UniqueConstraint
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column,
    relationship, MappedAsDataclass
)
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Base(DeclarativeBase):
    """Base clase para todos los modelos"""
    pass


class Conversation(Base):
    """Tabla de conversaciones"""

    __tablename__ = "conversations"
    __table_args__ = (
        Index('idx_conversation_phone', 'phone_number'),
        Index('idx_conversation_status_updated', 'status', 'updated_at'),
        Index('idx_conversation_project', 'project_id'),
        UniqueConstraint('project_id', 'phone_number', name='uq_conversation_project_phone'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto al que pertenece esta conversación"
    )
    phone_number: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Número de teléfono del usuario (formato internacional) o email para plataformas como Chatwoot"
    )
    platform: Mapped[str] = mapped_column(
        String(50),
        default="whatsapp",
        nullable=False,
        comment="Plataforma de origen (whatsapp, telegram, etc.)"
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="active",
        nullable=False,
        comment="Estado: active, paused, resolved, archived"
    )
    agent_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Si el agente está habilitado para esta conversación"
    )
    meta_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=dict,
        comment="Metadatos adicionales"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="conversations")
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan"
    )
    agent_toggle: Mapped[Optional["AgentToggle"]] = relationship(
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, project={self.project_id}, phone={self.phone_number})>"


class Message(Base):
    """Tabla de mensajes"""

    __tablename__ = "messages"
    __table_args__ = (
        Index('idx_message_conversation_created', 'conversation_id', 'created_at'),
        Index('idx_message_direction', 'direction'),
        Index('idx_message_project', 'project_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto al que pertenece este mensaje"
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False
    )
    direction: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="inbound o outbound"
    )
    message_type: Mapped[str] = mapped_column(
        String(50),
        default="text",
        nullable=False,
        comment="text, image, audio, button, etc."
    )
    content: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Contenido del mensaje (texto o URL de media)"
    )
    raw_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        comment="Payload completo del mensaje (webhook original)"
    )
    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Si el mensaje fue procesado por el agente"
    )
    processing_error: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Error si falló el procesamiento"
    )
    agent_response: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Respuesta generada por el agente"
    )
    tool_calls: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSON,
        comment="Tool calls ejecutados por el agente"
    )
    execution_time_ms: Mapped[Optional[float]] = mapped_column(
        BigInteger,
        comment="Tiempo de procesamiento en milisegundos"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages"
    )
    project: Mapped["Project"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, project={self.project_id}, conv={self.conversation_id}, dir={self.direction})>"


class Appointment(Base):
    """Tabla de citas/agendamientos"""

    __tablename__ = "appointments"
    __table_args__ = (
        Index('idx_appointment_phone_date', 'phone_number', 'appointment_date'),
        Index('idx_appointment_status', 'status'),
        Index('idx_appointment_project', 'project_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto al que pertenece esta cita"
    )
    phone_number: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True
    )
    appointment_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Fecha y hora de la cita"
    )
    service_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Tipo de servicio"
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="scheduled",
        nullable=False,
        comment="scheduled, cancelled, completed, no_show"
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Notas adicionales"
    )
    meta_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=dict,
        comment="Metadatos (datos del cliente, etc.)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    google_event_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="ID del evento en Google Calendar"
    )
    sync_status: Mapped[str] = mapped_column(
        String(50),
        default="synced",
        nullable=False,
        comment="Estado de sincronización: synced, pending, error"
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="appointments")

    def __repr__(self) -> str:
        return f"<Appointment(id={self.id}, project={self.project_id}, phone={self.phone_number}, date={self.appointment_date})>"


class ToolCallLog(Base):
    """Tabla de logs de tool calls (audit trail)"""

    __tablename__ = "tool_call_logs"
    __table_args__ = (
        Index('idx_tool_call_session_tool', 'session_id', 'tool_name'),
        Index('idx_tool_call_timestamp', 'created_at'),
        Index('idx_tool_call_project', 'project_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto al que pertenece este tool call"
    )
    session_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True
    )
    tool_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    input_data: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False
    )
    output_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        comment="Resultado del tool call"
    )
    success: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Error si falló"
    )
    execution_time_ms: Mapped[Optional[float]] = mapped_column(
        BigInteger,
        comment="Tiempo de ejecución en ms"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="tool_call_logs")

    def __repr__(self) -> str:
        return f"<ToolCallLog(id={self.id}, project={self.project_id}, tool={self.tool_name}, session={self.session_id})>"


class LangchainMemory(Base):
    """
    Tabla de memoria para LangChain.
    Almacena historial de conversación separado de mensajes de WhatsApp.
    Una sola tabla para todas las sesiones (escalable).
    """

    __tablename__ = "langchain_memory"
    __table_args__ = (
        Index('idx_langchain_memory_session_created', 'session_id', 'created_at'),
        Index('idx_langchain_memory_project', 'project_id'),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        nullable=False
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        comment="Proyecto al que pertenece esta memoria (opcional)"
    )
    session_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="ID de sesión (teléfono o UUID)"
    )
    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Tipo de mensaje: 'human' o 'ai'"
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Contenido del mensaje"
    )
    additional_kwargs: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=dict,
        nullable=True,
        comment="Metadatos adicionales del mensaje (name, function_call, etc.)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc) if hasattr(datetime, 'timezone') else datetime.utcnow,
        nullable=False,
        comment="Timestamp de creación"
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="langchain_memories")

    def __repr__(self) -> str:
        return f"<LangchainMemory(id={self.id}, project={self.project_id}, session={self.session_id}, type={self.type})>"


# ============================================
# NUEVOS MODELOS PARA MULTI-TENANCY Y GESTIÓN
# ============================================

class Project(Base):
    """Proyecto/organización (multi-tenant)"""

    __tablename__ = "projects"
    __table_args__ = (
        Index('idx_project_slug', 'slug'),
        Index('idx_project_active', 'is_active'),
        UniqueConstraint('slug', name='uq_project_slug'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Nombre del proyecto/organización"
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Slug único para URLs (ej: 'dentapp')"
    )
    api_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="API key para autenticación (hash en DB)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Si el proyecto está activo"
    )
    whatsapp_webhook_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        comment="URL del webhook de WhatsApp para este proyecto"
    )
    settings: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        comment="Configuración específica del proyecto"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    conversations: Mapped[List["Conversation"]] = relationship(back_populates="project")
    appointments: Mapped[List["Appointment"]] = relationship(back_populates="project")
    messages: Mapped[List["Message"]] = relationship(back_populates="project")
    tool_call_logs: Mapped[List["ToolCallLog"]] = relationship(back_populates="project")
    langchain_memories: Mapped[List["LangchainMemory"]] = relationship(back_populates="project")
    agent_states: Mapped[List["AgentState"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan"
    )
    agent_config: Mapped["ProjectAgentConfig"] = relationship(
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan"
    )
    agent_toggles: Mapped[List["AgentToggle"]] = relationship(back_populates="project")
    user_profiles: Mapped[List["UserProfile"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan"
    )
    users: Mapped[List["User"]] = relationship(
        secondary="user_projects",
        back_populates="projects"
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name={self.name}, slug={self.slug})>"


class ProjectAgentConfig(Base):
    """Configuración del agente por proyecto"""

    __tablename__ = "project_agent_configs"
    __table_args__ = (
        UniqueConstraint('project_id', name='uq_project_agent_config_project'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="Proyecto al que pertenece esta config"
    )
    agent_name: Mapped[str] = mapped_column(
        String(100),
        default="DeyyAgent",
        nullable=False,
        comment="Nombre del agente"
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default="Eres un asistente AI útil para el proyecto {project_name}. {custom_instructions}",
        nullable=False,
        comment="Prompt del sistema. Puede usar variables: {project_name}, {custom_instructions}"
    )
    custom_instructions: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Instrucciones adicionales específicas del proyecto"
    )
    max_iterations: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
        comment="Iteraciones máximas del agente"
    )
    temperature: Mapped[float] = mapped_column(
        default=0.7,
        nullable=False,
        comment="Temperatura del modelo (0.0 - 2.0)"
    )
    enabled_tools: Mapped[List[str]] = mapped_column(
        JSON,
        default=lambda: ["agendar_cita", "consultar_disponibilidad", "obtener_citas_cliente", "cancelar_cita"],
        comment="Lista de herramientas habilitadas"
    )
    calendar_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Si usar Google Calendar"
    )
    google_calendar_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment="ID del calendario de Google (email o 'primary')"
    )
    calendar_timezone: Mapped[str] = mapped_column(
        String(50),
        default="America/Guayaquil",
        comment="Timezone para eventos de calendario"
    )
    calendar_mapping: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        comment="Mapeo de servicios a odontólogos y duraciones. Ej: {\"limpieza\": {\"duration\": 30, \"dentist\": \"dr_smith\"}}"
    )
    global_agent_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Si el agente está habilitado globalmente para este proyecto"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="agent_config")

    def __repr__(self) -> str:
        return f"<ProjectAgentConfig(id={self.id}, project={self.project_id}, agent={self.agent_name})>"


class AgentToggle(Base):
    """Toggle de agente por conversación"""

    __tablename__ = "agent_toggles"
    __table_args__ = (
        UniqueConstraint('conversation_id', name='uq_agent_toggle_conversation'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="Conversación a la que aplica este toggle"
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto (cached para queries rápidas)"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Si el agente está habilitado para esta conversación"
    )
    toggled_by: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Usuario que realizó el cambio (email o UUID)"
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Razón del cambio"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    conversation: Mapped["Conversation"] = relationship(back_populates="agent_toggle")
    project: Mapped["Project"] = relationship(back_populates="agent_toggles")

    def __repr__(self) -> str:
        return f"<AgentToggle(id={self.id}, conversation={self.conversation_id}, enabled={self.is_enabled})>"


class User(Base):
    """Usuario del panel admin"""

    __tablename__ = "users"
    __table_args__ = (
        Index('idx_user_email', 'email'),
        UniqueConstraint('email', name='uq_user_email'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Email del usuario (único)"
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Hash de contraseña (bcrypt/argon2)"
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Nombre completo"
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="agent",
        nullable=False,
        comment="Rol: admin, manager, agent, viewer"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Si el usuario está activo"
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="Último login exitoso"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones many-to-many con projects
    projects: Mapped[List["Project"]] = relationship(
        secondary="user_projects",
        back_populates="users"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"


# Tabla intermedia User-Project (many-to-many)
class UserProject(Base):
    """Relación usuarios-proyectos"""

    __tablename__ = "user_projects"
    __table_args__ = (
        UniqueConstraint('user_id', 'project_id', name='uq_user_project'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False
    )
    role_in_project: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
        comment="Rol del usuario en este proyecto: admin, manager, agent, viewer"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    user: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()

    def __repr__(self) -> str:
        return f"<UserProject(user={self.user_id}, project={self.project_id}, role={self.role_in_project})>"


# ============================================
# MEMORIA A LARGO PLAZO (USER PROFILES)
# ============================================

class UserProfile(Base):
    """
    Perfil de usuario (memoria semántica a largo plazo).

    Almacena información persistente sobre el usuario que trasciende conversaciones:
    - Preferencias (servicios favoritos, horarios, etc.)
    - Historial médico relevante (alergias, condiciones)
    - Notas de interacciones pasadas
    - Hechos extraídos automáticamente de conversaciones
    """

    __tablename__ = "user_profiles"
    __table_args__ = (
        Index('idx_user_profile_phone', 'phone_number'),
        UniqueConstraint('phone_number', name='uq_user_profile_phone'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    phone_number: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        comment="Número de teléfono normalizado o email (formato E.164 para teléfonos)"
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Proyecto al que pertenece este perfil"
    )
    preferences: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=dict,
        comment="Preferencias del usuario (servicio favorito, horario, etc.)"
    )
    last_appointment: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Fecha de la última cita agendada"
    )
    last_appointment_service: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Servicio de la última cita"
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Notas médicas o de preferencias ( allergies, miedos, etc.)"
    )
    extracted_facts: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=dict,
        comment="Hechos extraídos automáticamente de conversaciones (estructura libre)"
    )
    total_conversations: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Número total de conversaciones mantenidas"
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="Fecha de primera vez que se vio al usuario"
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="Fecha de última interacción"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relaciones
    project: Mapped["Project"] = relationship(back_populates="user_profiles")

    def __repr__(self) -> str:
        return f"<UserProfile(id={self.id}, phone={self.phone_number}, project={self.project_id})>"



class AgentState(Base):
    """
    Estado de la state machine por sesión.
    Almacena el SupportState completo (current_step, datos de cita, etc.)
    """

    __tablename__ = "agent_states"
    __table_args__ = (
        Index('idx_agent_states_session_id', 'session_id'),
        Index('idx_agent_states_updated_at', 'updated_at'),
        Index('idx_agent_states_project_session', 'project_id', 'session_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        comment="ID único del estado"
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        comment="Proyecto asociado (opcional, para multi-tenant)"
    )
    session_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="ID de sesión (teléfono normalizado o UUID)"
    )
    state: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="Estado completo de SupportState (current_step, intención, datos de cita, etc.)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="Timestamp de creación"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="Timestamp de última actualización"
    )

    # Relaciones
    project: Mapped[Optional["Project"]] = relationship(
        back_populates="agent_states"
    )

    def __repr__(self) -> str:
        step = self.state.get("current_step", "unknown") if self.state else "unknown"
        return f"<AgentState(id={self.id}, session={self.session_id}, step={step})>"
