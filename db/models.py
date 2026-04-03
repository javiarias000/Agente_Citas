#!/usr/bin/env python3
"""
Modelos de base de datos PostgreSQL
Usa SQLAlchemy 2.0 con async/await
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    String, Text, DateTime, Boolean, ForeignKey, Index, Integer,
    BigInteger, JSON
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
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Número de teléfono del usuario (formato internacional)"
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
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, phone={self.phone_number})>"


class Message(Base):
    """Tabla de mensajes"""

    __tablename__ = "messages"
    __table_args__ = (
        Index('idx_message_conversation_created', 'conversation_id', 'created_at'),
        Index('idx_message_direction', 'direction'),
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

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, conv={self.conversation_id}, dir={self.direction})>"


class Appointment(Base):
    """Tabla de citas/agendamientos"""

    __tablename__ = "appointments"
    __table_args__ = (
        Index('idx_appointment_phone_date', 'phone_number', 'appointment_date'),
        Index('idx_appointment_status', 'status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )
    phone_number: Mapped[str] = mapped_column(
        String(20),
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

    def __repr__(self) -> str:
        return f"<Appointment(id={self.id}, phone={self.phone_number}, date={self.appointment_date})>"


class ToolCallLog(Base):
    """Tabla de logs de tool calls (audit trail)"""

    __tablename__ = "tool_call_logs"
    __table_args__ = (
        Index('idx_tool_call_session_tool', 'session_id', 'tool_name'),
        Index('idx_tool_call_timestamp', 'created_at'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
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

    def __repr__(self) -> str:
        return f"<ToolCallLog(id={self.id}, tool={self.tool_name}, session={self.session_id})>"


class LangchainMemory(Base):
    """
    Tabla de memoria para LangChain.
    Almacena historial de conversación separado de mensajes de WhatsApp.
    Una sola tabla para todas las sesiones (escalable).
    """

    __tablename__ = "langchain_memory"
    __table_args__ = (
        Index('idx_langchain_memory_session_created', 'session_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        nullable=False
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc) if hasattr(datetime, 'timezone') else datetime.utcnow,
        nullable=False,
        comment="Timestamp de creación"
    )

    def __repr__(self) -> str:
        return f"<LangchainMemory(id={self.id}, session={self.session_id}, type={self.type})>"
