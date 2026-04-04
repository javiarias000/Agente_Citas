#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Herramientas (tools) para State Machine de Arcadium.
Todas las tools devuelven Command objects para actualizar el estado y controlar transiciones.

Incluye:
- 5 herramientas nuevas (classify_intent, record_*, transition_to, go_back_to)
- 4 herramientas modificadas (consultar_disponibilidad, agendar_cita, cancelar_cita, reagendar_cita)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal, Optional, Dict, Any, List
from pydantic import Field
from langchain_core.tools import tool
from langgraph.types import Command
from langchain_core.messages import ToolMessage
import structlog

from db import get_async_session
from agents.context_vars import (
    get_current_phone,
    get_current_project_id,
    get_current_project_config
)
from agents.support_state import (
    SupportState,
    SupportStep,
    Intent,
    ServiceType,
    get_service_duration,
    add_error,
    clear_errors
)

logger = structlog.get_logger("tools.state_machine")


# ============================================
# HERRAMIENTAS NUEVAS (State Machine)
# ============================================

@tool
def classify_intent(
    user_message: str,
    runtime: Any
) -> Command:
    """
    Analiza el mensaje del usuario y clasifica su intención.

    Categorías:
    - "agendar": Quiere reservar nueva cita
    - "consultar": Solo quiere ver disponibilidad
    - "cancelar": Quiere eliminar cita existente
    - "reagendar": Quiere modificar fecha/hora
    - "otro": Otro motivo

    Returns:
        Command que actualiza 'intent' y transita al paso siguiente apropiado.
    """
    # Determinar intención usando keyword matching + LLM (simplificado por ahora)
    message_lower = user_message.lower()

    # Palabras clave para clasificación (mejorar con LLM después)
    # Orden: cancelar y reagendar tienen prioridad sobre agendar (porque "cita" aparece en múltiples)
    if any(word in message_lower for word in ["cancelar", "eliminar", "anular", "borrar"]):
        intent: Intent = "cancelar"
    elif any(word in message_lower for word in ["cambiar", "reagendar", "mover", "modificar", "otra fecha"]):
        intent = "reagendar"
    elif any(word in message_lower for word in ["agendar", "reservar", "cita", "turno", "programar", "consulta", "querer", "necesitar"]):
        # "consulta" como verbo/necesidad, también "querer" y "necesitar" indican intención de agendar algo
        intent = "agendar"
    elif any(word in message_lower for word in ["disponibilidad", "hueco", "libre", "hay", "cuándo"]):
        intent = "consultar"
    else:
        intent = "otro"

    # Determinar siguiente estado basado en intención
    next_step_map = {
        "agendar": "info_collector",
        "consultar": "scheduler",
        "cancelar": "resolution",
        "reagendar": "resolution",
        "otro": "reception"
    }
    next_step = next_step_map[intent]

    logger.info(
        "Intent classified",
        intent=intent,
        next_step=next_step,
        user_message_preview=user_message[:50]
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Intención detectada: {intent}",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "intent": intent,
            "current_step": next_step,
            "errors_encountered": []  # Limpiar errores al avanzar
        }
    )


@tool
def transition_to(
    step: SupportStep,
    reason: str,
    runtime: Any
) -> Command:
    """
    Transita manualmente a un estado específico.

    ADVERTENCIA: Solo usar si el workflow requiere un salto no estándar.
    Normalmente las tools específicas manejan las transiciones automáticamente.

    Args:
        step: Estado destino ("reception", "info_collector", "scheduler", "resolution")
        reason: Razón de la transición (para logs y debugging)

    Returns:
        Command que actualiza current_step y limpia errores.
    """
    from_step = runtime.state.get("current_step", "unknown")

    logger.info(
        "State transition (manual)",
        from_step=from_step,
        to_step=step,
        reason=reason,
        session_id=runtime.state.get("session_id")
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Transitioning to {step}: {reason}",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "current_step": step,
            "errors_encountered": clear_errors(runtime.state)
        }
    )


@tool
def go_back_to(
    step: Literal["reception", "info_collector", "scheduler"],
    reason: str,
    runtime: Any
) -> Command:
    """
    Retrocede a un estado anterior para corrección.

    Casos de uso:
    - Usuario dice "en realidad mi nombre es..."
    - Usuario quiere otra fecha/hora
    - Cancelar agendado y empezar de nuevo

    IMPORTANTE: No se puede retroceder desde 'resolution' a 'scheduler' directamente.
    desde 'resolution' solo se puede ir a 'info_collector' o 'reception'.

    Args:
        step: Estado anterior al que retroceder
        reason: Razón del retroceso

    Returns:
        Command que actualiza current_step.
    """
    current_step = runtime.state.get("current_step")

    if current_step == "resolution" and step == "scheduler":
        logger.warning(
            "Invalid backward transition",
            from_step=current_step,
            to_step=step,
            reason=reason
        )
        # Forzar a info_collector en lugar de scheduler desde resolution
        step = "info_collector"

    logger.info(
        "State transition (backward)",
        from_step=current_step,
        to_step=step,
        reason=reason
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Going back to {step}: {reason}",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "current_step": step
        }
    )


@tool
def record_service_selection(
    service: str,
    runtime: Any
) -> Command:
    """
    Registra el servicio dental seleccionado y su duración.
    NO transita - solo guarda dato.

    Args:
        service: Servicio dental (ej: "limpieza", "consulta", "empaste")

    Returns:
        Command que actualiza selected_service y service_duration.
    """
    # Normalizar servicio (convertir a snake_case, validar)
    normalized = service.lower().strip()

    # Verificar que es un servicio válido
    valid_services = ["consulta", "limpieza", "empaste", "extraccion", "endodoncia",
                      "ortodoncia", "cirugia", "implantes", "estetica", "odontopediatria"]

    if normalized not in valid_services:
        logger.warning("Servicio no reconocido, usando default", service=service)
        normalized = "consulta"  # Default

    duration = get_service_duration(normalized)

    logger.info(
        "Service recorded",
        service=normalized,
        duration=duration,
        patient=runtime.state.get("patient_name")
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Servicio registrado: {normalized} ({duration} min)",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "selected_service": normalized,
            "service_duration": duration
        }
    )


@tool
def record_datetime_pref(
    fecha: str,
    runtime: Any,
    alternatives: Optional[List[str]] = None
) -> Command:
    """
    Registra la fecha/hora preferida del usuario y transita a 'scheduler'.

    Args:
        fecha: Fecha principal en formato ISO (ej: "2025-12-25T14:30")
        alternatives: Lista de fechas alternativas en formato ISO (opcional)

    Returns:
        Command que guarda fecha y transita a scheduler.
    """
    # Validar formato ISO básico
    try:
        dt = datetime.fromisoformat(fecha)
        # Validar que es fecha futura
        if dt < datetime.now():
            logger.warning("Fecha en el pasado, rechazando", fecha=fecha)
            # Registrar error en estado (mutación directa)
            add_error(runtime.state, "fecha_pasada")
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Error: {fecha} está en el pasado. Por favor proporciona una fecha futura.",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )

        # Guardar fecha original para logging
        original_fecha_str = fecha

        # Si es fin de semana (sábado=5, domingo=6), mover al próximo lunes
        if dt.weekday() >= 5:
            days_to_monday = (7 - dt.weekday()) % 7  # 5(Sat)->2, 6(Sun)->1
            if days_to_monday == 0:
                days_to_monday = 7
            dt = dt + timedelta(days=days_to_monday)
            # Actualizar fecha string para guardar
            fecha = dt.isoformat()
            logger.info(
                "Fecha en fin de semana ajustada al próximo lunes",
                original_fecha=original_fecha_str,
                adjusted_fecha=fecha,
                weekday=dt.weekday()
            )
    except ValueError as e:
        logger.error("Formato de fecha inválido", fecha=fecha, error=str(e))
        add_error(runtime.state, "formato_fecha_invalido")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error: Formato de fecha inválido. Usa YYYY-MM-DDTHH:MM",
                        tool_call_id=runtime.tool_call_id
                    )
                ]
            }
        )

    logger.info(
        "Datetime preference recorded",
        fecha=fecha,
        alternatives_count=len(alternatives) if alternatives else 0
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Fecha preferida registrada: {fecha}",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "datetime_preference": fecha,
            "datetime_alternatives": alternatives or [],
            "current_step": "scheduler"  # ← Transita a coordinación
        }
    )


@tool
def record_appointment(
    appointment_id: str,
    runtime: Any,
    google_event_id: Optional[str] = None,
    google_event_link: Optional[str] = None
) -> Command:
    """
    Helper: registra datos de cita agendada y transita a 'resolution'.
    Se usa después de agendar (ya sea por agendar_cita o manualmente).

    Args:
        appointment_id: UUID de la cita en DB
        google_event_id: ID del evento en Google Calendar (opcional)
        google_event_link: Enlace al evento (opcional)

    Returns:
        Command que actualiza datos de cita y transita a resolution.
    """
    logger.info(
        "Appointment recorded",
        appointment_id=appointment_id,
        google_event_id=google_event_id
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Cita registrada: {appointment_id}",
                    tool_call_id=runtime.tool_call_id
                )
            ],
            "appointment_id": appointment_id,
            "google_event_id": google_event_id,
            "google_event_link": google_event_link,
            "current_step": "resolution"
        }
    )


# ============================================
# HERRAMIENTAS MODIFICADAS (devuelven Command)
# ============================================

@tool
async def consultar_disponibilidad(
    runtime: Any,
    fecha: Optional[str] = None,
    servicio: Optional[str] = None
) -> Command:
    """
    Consulta horarios disponibles para agendar.

    Fuentes:
    1. Google Calendar (si habilitado)
    2. Base de datos (citas ya agendadas)

    Args:
        fecha: Fecha a consultar en formato YYYY-MM-DD (opcional, usa datetime_preference si no)
        servicio: Servicio para calcular duración (opcional, usa selected_service si no)

    Returns:
        Command que actualiza available_slots y availability_checked.
        NO transita - el agente decide si elige slot o pide otra fecha.
    """
    state = runtime.state
    fecha = fecha or (state.get("datetime_preference", "").split("T")[0] if state.get("datetime_preference") else None)
    servicio = servicio or state.get("selected_service")

    if not fecha:
        logger.warning("No hay fecha para consultar disponibilidad")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: Necesito una fecha para consultar disponibilidad",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, "fecha_missing")
            }
        )

    try:
        # Obtener AppointmentService (usar contexto multi-tenant)
        appointment_service = _get_appointment_service_from_runtime(runtime)

        # Determinar duración
        duration = 30  # default
        if servicio:
            duration = get_service_duration(servicio)

        # Obtener sesión de DB
        from db import get_async_session
        session = get_async_session()

        # Parsear fecha a datetime
        from datetime import datetime
        if isinstance(fecha, str):
            # Si es YYYY-MM-DD, convertir a datetime
            if len(fecha) == 10:
                fecha_dt = datetime.fromisoformat(fecha + "T00:00:00")
            else:
                fecha_dt = datetime.fromisoformat(fecha)
        else:
            fecha_dt = fecha

        # Consultar slots
        slots = await appointment_service.get_available_slots(
            session=session,
            date=fecha_dt,
            duration_minutes=duration
        )

        # Convertir a ISO strings
        slot_strings = [slot.start.isoformat() for slot in slots]

        logger.info(
            "Disponibilidad consultada",
            fecha=fecha,
            service=servicio,
            slots_count=len(slot_strings)
        )

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Disponibilidad consultada: {len(slot_strings)} slots libres",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "available_slots": slot_strings,
                "availability_checked": True
            }
        )

    except Exception as e:
        logger.error("Error consultando disponibilidad", error=str(e))
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error consultando disponibilidad: {str(e)}",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, str(e))
            }
        )


@tool
async def agendar_cita(
    runtime: Any,
    fecha: Optional[str] = None,
    servicio: Optional[str] = None,
    notas: Optional[str] = None,
    nombre: Optional[str] = None
) -> Command:
    """
    Agenda la cita en Google Calendar + DB.

    Flujo:
    1. Valida fecha (futura, laboral)
    2. Consulta disponibilidad (race condition check)
    3. Crea evento en Google Calendar
    4. Guarda en PostgreSQL
    5. Devuelve confirmación + enlace

    Args:
        fecha: Fecha en formato ISO (opcional, usa selected_slot o datetime_preference del state)
        servicio: Tipo de servicio (opcional, usa selected_service del state)
        notas: Notas adicionales (opcional)
        nombre: Nombre completo del cliente (opcional, usa patient_name del state si no se provee)

    Returns:
        Command con appointment_id y transita a 'resolution' si éxito.
        Si falla, NO transita y agrega error a errors_encountered.
    """
    state = runtime.state
    fecha = fecha or state.get("selected_slot") or state.get("datetime_preference")
    servicio = servicio or state.get("selected_service")
    phone = get_current_phone()  # Debería venir de contextvars

    # Obtener nombre del cliente: prioridad argumento > state > phone (fallback)
    if not nombre:
        nombre = state.get("patient_name") or f"Cliente {phone}"

    # LOG para depurar
    logger.info("AGENDAR_CITA invoked", fecha=fecha, servicio=servicio, phone=phone, current_step=state.get("current_step"))

    if not fecha or not servicio:
        logger.warning("Faltan datos para agendar", fecha=fecha, servicio=servicio)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: Necesito fecha y servicio para agendar",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, "missing_fecha_o_servicio")
            }
        )

    try:
        appointment_service = _get_appointment_service_from_runtime(runtime)

        # Validar fecha
        dt = datetime.fromisoformat(fecha)
        logger.info("AGENDAR_CITA parsed date", dt=dt.isoformat(), weekday=dt.weekday())

        # Preparar metadata con nombre del cliente
        metadata = {}
        if nombre:
            metadata["client_name"] = nombre

        # Crear cita
        project_id = state.get("project_id")  # Puede ser None para modo legacy
        success, message, appointment = await appointment_service.create_appointment(
            session=get_async_session(),
            phone_number=phone or "+0000000000",  # TODO: obtener phone real
            appointment_datetime=dt,
            service_type=servicio,
            project_id=project_id,
            notes=notas,
            metadata=metadata
        )

        if success and appointment:
            logger.info(
                "Cita agendada exitosamente",
                appointment_id=str(appointment.id),
                google_event_id=appointment.google_event_id
            )

            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"✅ Cita agendada: {appointment.id}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "appointment_id": str(appointment.id),
                    "google_event_id": appointment.google_event_id,
                    # No incluir google_event_link porque no está en el modelo
                    "current_step": "resolution",  # ← ÉXITO: Transita a resolución
                    "errors_encountered": clear_errors(state)
                }
            )
        else:
            logger.error("Error agendando cita", message=message)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"❌ Error agendando: {message}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "errors_encountered": add_error(state, message)
                }
            )

    except Exception as e:
        logger.error("Excepción agendando cita", error=str(e), exc_info=True)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"❌ Error interno: {str(e)}",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, str(e))
            }
        )


@tool
async def cancelar_cita(
    runtime: Any,
    appointment_id: Optional[str] = None
) -> Command:
    """
    Cancela una cita agendada.

    Flujo:
    1. Si no hay appointment_id, busca la próxima cita del cliente
    2. Elimina evento de Google Calendar (si existe)
    3. Actualiza estado en DB a "cancelled"

    Args:
        appointment_id: ID de la cita a cancelar (opcional)

    Returns:
        Command con confirmación y transición a 'reception' si éxito.
    """
    state = runtime.state
    phone = get_current_phone()

    try:
        appointment_service = _get_appointment_service_from_runtime(runtime)

        # Si no hay ID, buscar próxima cita
        if not appointment_id:
            citas = await appointment_service.get_appointments_by_phone(
                session=get_async_session(),
                phone_number=phone or "",
                upcoming_only=True
            )
            if not citas:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="No hay citas para cancelar",
                                tool_call_id=runtime.tool_call_id
                            )
                        ]
                    }
                )
            appointment_id = str(citas[0].id)

        # Ejecutar cancelación
        success, message = await appointment_service.cancel_appointment(
            appointment_id=uuid.UUID(appointment_id) if isinstance(appointment_id, str) else appointment_id,
            phone=phone
        )

        if success:
            logger.info("Cita cancelada", appointment_id=appointment_id, reason=message)

            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"✅ Cita cancelada: {appointment_id}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "appointment_id": None,
                    "current_step": "reception",  # ← Cancelación completa: volver a inicio
                    "errors_encountered": clear_errors(state)
                }
            )
        else:
            logger.error("Error cancelando", appointment_id=appointment_id, reason=message)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"❌ Error cancelando: {message}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "errors_encountered": add_error(state, message)
                }
            )

    except Exception as e:
        logger.error("Excepción cancelando cita", error=str(e), exc_info=True)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"❌ Error interno: {str(e)}",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, str(e))
            }
        )


@tool
async def reagendar_cita(
    runtime: Any,
    appointment_id: Optional[str] = None,
    nueva_fecha: Optional[str] = None
) -> Command:
    """
    Cambia fecha/hora de una cita existente.

    Flujo:
    1. Si no hay appointment_id, usa la próxima cita del cliente
    2. Consulta disponibilidad en nueva_fecha
    3. Si disponible: actualiza Google Calendar + DB
    4. Transita a 'info_collector' para recolectar nueva info si es necesaria

    Args:
        appointment_id: ID de la cita a reagendar (opcional)
        nueva_fecha: Nueva fecha/hora en ISO (REQUERIDO si no está en state.selected_slot)

    Returns:
        Command con resultado y transición.
    """
    state = runtime.state
    phone = get_current_phone()

    try:
        appointment_service = _get_appointment_service_from_runtime(runtime)

        # Determinar qué cita reagendar
        if not appointment_id:
            citas = await appointment_service.get_appointments_by_phone(
                session=get_async_session(),
                phone_number=phone or "",
                upcoming_only=True
            )
            if not citas:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="No hay citas para reagendar",
                                tool_call_id=runtime.tool_call_id
                            )
                        ]
                    }
                )
            appointment_id = str(citas[0].id)

        # Si no pasó nueva_fecha, extraer de state
        if not nueva_fecha:
            nueva_fecha = state.get("selected_slot") or state.get("datetime_preference")
            if not nueva_fecha:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="Necesito una nueva fecha para reagendar",
                                tool_call_id=runtime.tool_call_id
                            )
                        ]
                    }
                )

        # Validar formato
        try:
            new_dt = datetime.fromisoformat(nueva_fecha)
        except ValueError:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Formato de fecha inválido: {nueva_fecha}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "errors_encountered": add_error(state, "formato_fecha_invalido")
                }
            )

        # Ejecutar reagendado
        success, message, updated_appointment = await appointment_service.reschedule_appointment(
            appointment_id=uuid.UUID(appointment_id) if isinstance(appointment_id, str) else appointment_id,
            new_datetime=new_dt,
            phone=phone
        )

        if success:
            logger.info(
                "Cita reagendada",
                appointment_id=appointment_id,
                new_date=nueva_fecha
            )

            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"✅ Cita reagendada a {nueva_fecha}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "appointment_id": str(updated_appointment.id),
                    "selected_date": nueva_fecha,
                    "google_event_link": updated_appointment.google_event_link,
                    "current_step": "info_collector",  # ← Volver a recoger datos (por si quiere agendar más)
                    "errors_encountered": clear_errors(state)
                }
            )
        else:
            logger.error("Error reagendando", reason=message)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"❌ Error reagendando: {message}",
                            tool_call_id=runtime.tool_call_id
                        )
                    ],
                    "errors_encountered": add_error(state, message)
                }
            )

    except Exception as e:
        logger.error("Excepción reagendando cita", error=str(e), exc_info=True)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"❌ Error interno: {str(e)}",
                        tool_call_id=runtime.tool_call_id
                    )
                ],
                "errors_encountered": add_error(state, str(e))
            }
        )


# ============================================
# HERRAMIENTAS SIN CAMBIOS (solo lectura)
# ============================================

@tool
async def obtener_citas_cliente(
    runtime: Any,
    historico: Annotated[bool, Field(description="Si True, incluye citas pasadas")] = False
) -> Dict[str, Any]:
    """
    Obtiene las citas del cliente actual.

    NO transica - solo lectura de datos.

    Args:
        historico: Si True, incluye citas pasadas/canceladas

    Returns:
        Dict con lista de citas.
    """
    phone = get_current_phone()  # TODO: obtener de contextvars

    try:
        appointment_service = _get_appointment_service_from_runtime(runtime)
        citas = await appointment_service.get_appointments_by_phone(
            session=get_async_session(),
            phone_number=phone or "",
            upcoming_only=not historico  # Si historico=True, upcoming_only=False
        )

        result = {
            "citas": [
                {
                    "id": str(c.id),
                    "fecha": c.datetime.isoformat(),
                    "servicio": c.service_type,
                    "estado": c.status
                }
                for c in citas
            ],
            "count": len(citas)
        }

        logger.info("Citas obtenidas", count=len(citas), phone=phone)

        # Si no hay citas, retornar Command para volver a reception
        if len(citas) == 0:
            return Command(
                goto="reception",
                update={
                    "messages": [
                        ToolMessage(
                            content="No tienes citas registradas. ¿Te gustaría agendar una?",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )

        return result

    except Exception as e:
        logger.error("Error obteniendo citas", error=str(e))
        return {"citas": [], "count": 0, "error": str(e)}


# ============================================
# FUNCIONES HELPER
# ============================================

def _get_appointment_service_from_runtime(runtime: Any) -> AppointmentService:
    """
    Obtiene AppointmentService configurado según el contexto (multi-tenant).
    """
    from core.config import get_settings
    from services.appointment_service import AppointmentService
    from services.google_calendar_service import GoogleCalendarService
    from services.project_appointment_service import ProjectAppointmentService

    project_id = get_current_project_id()
    project_config = get_current_project_config()

    if project_id and project_config:
        # Modo multi-tenant
        return ProjectAppointmentService(project_config)
    else:
        # Modo legacy - usar settings globales
        settings = get_settings()

        if settings.GOOGLE_CALENDAR_ENABLED:
            gcal = GoogleCalendarService(
                calendar_id=settings.GOOGLE_CALENDAR_DEFAULT_ID,
                credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
                timezone=settings.GOOGLE_CALENDAR_TIMEZONE
            )
            return AppointmentService(
                settings=settings,
                google_calendar_service=gcal
            )
        else:
            return AppointmentService(settings=settings)


@tool
async def record_patient_name(
    runtime: Any,
    nombre: str = Field(description="Nombre completo del cliente")
) -> Command:
    """
    Guarda el nombre del paciente en el estado de la conversación.

    Usar cuando el cliente proporcione su nombre para registrarlo en la cita.

    Args:
        nombre: Nombre completo del cliente

    Returns:
        Command con actualización de patient_name en el estado.
    """
    state = runtime.state

    logger.info(
        "Guardando nombre del paciente",
        nombre=nombre,
        phone=state.get("phone_number")
    )

    # Guardar en estado (StateMachine)
    return Command(
        update={
            "patient_name": nombre
        }
    )


# ============================================
# LISTA DE TODAS LAS TOOLS
# ============================================

STATE_MACHINE_TOOLS = [
    classify_intent,
    transition_to,
    go_back_to,
    record_service_selection,
    record_datetime_pref,
    record_appointment,
    consultar_disponibilidad,
    agendar_cita,
    obtener_citas_cliente,
    cancelar_cita,
    reagendar_cita,
    record_patient_name,  # Nueva herramienta
    reagendar_cita
]
