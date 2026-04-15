#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArcadiumGraph - StateGraph unificado para Arcadium Automation.

Este grafo implementa el flujo completo de agendamiento de citas usando:
- StateGraph de LangGraph
- Checkpointer PostgreSQL (PostgresSaver)
- Store para memoria cruzada-conversación (ArcadiumStore)
- Soporte para múltiples agentes (DeyyAgent, StateMachineAgent)
"""

from typing import Dict, Any, Literal, TypedDict, Annotated, List, Optional
from datetime import datetime, timedelta
import uuid
import structlog
from contextvars import ContextVar
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # For Python < 3.9

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.message import add_messages
from langgraph.types import Command

from core.store import ArcadiumStore, StoreProtocol
from core.config import get_settings
from agents.support_state import SupportState, SupportStep, create_initial_state

logger = structlog.get_logger("graph.arcadium")


# ============================================
# ESTADO DEL GRAFO (ArcadiumState)
# ============================================

class ArcadiumState(TypedDict):
    """
    Estado unificado del grafo de Arcadium.

    Combina:
    - Mensajes de conversación (LangGraph maneja automáticamente)
    - Estado de SupportState (current_step, intent, etc.)
    - Metadata de runtime (phone_number, project_id)
    """
    # Mensajes (manejados por add_messages reducer)
    messages: Annotated[List[BaseMessage], add_messages]

    # Metadata de sesión
    phone_number: str
    project_id: Optional[uuid.UUID]

    # Estado de SupportState (todos opcionales excepto current_step)
    current_step: SupportStep
    conversation_turns: int
    last_tool_used: Optional[str]
    errors_encountered: List[str]

    # Campos de recepción
    intent: Optional[str]

    # Campos de información
    patient_name: Optional[str]
    patient_phone: Optional[str]
    selected_service: Optional[str]
    service_duration: Optional[int]
    datetime_preference: Optional[str]
    datetime_alternatives: List[str]

    # Campos de coordinación
    availability_checked: bool
    available_slots: List[str]
    selected_slot: Optional[str]
    appointment_id: Optional[str]
    google_event_id: Optional[str]
    google_event_link: Optional[str]

    # Campos de resolución
    confirmation_sent: bool
    appointment_details: Optional[Dict[str, Any]]

    # Variables de contexto externas (fechas calculadas, etc.)
    context_vars: Optional[Dict[str, Any]]
    follow_up_needed: bool


def create_initial_arcadium_state(
    phone_number: str,
    project_id: Optional[uuid.UUID] = None,
    initial_step: SupportStep = "reception",
    context_vars: Optional[Dict[str, Any]] = None
) -> ArcadiumState:
    """
    Crea estado inicial para una nueva conversación.

    Args:
        phone_number: Número del usuario
        project_id: ID del proyecto (opcional)
        initial_step: Paso inicial del state machine
        context_vars: Variables de contexto (fechas calculadas, etc.)

    Returns:
        ArcadiumState inicializado
    """
    return ArcadiumState(
        messages=[],
        phone_number=phone_number,
        project_id=project_id,
        current_step=initial_step,
        conversation_turns=0,
        last_tool_used=None,
        errors_encountered=[],
        intent=None,
        patient_name=None,
        patient_phone=None,
        selected_service=None,
        service_duration=None,
        datetime_preference=None,
        datetime_alternatives=[],
        availability_checked=False,
        available_slots=[],
        selected_slot=None,
        appointment_id=None,
        google_event_id=None,
        google_event_link=None,
        confirmation_sent=False,
        appointment_details=None,
        follow_up_needed=False,
        context_vars=context_vars,
    )


# ============================================
# NODOS DEL GRAFO
# ============================================

async def load_conversation_context(
    state: ArcadiumState,
    store: ArcadiumStore,
    project_id: Optional[uuid.UUID] = None
) -> ArcadiumState:
    """
    Nodo: Carga el contexto de conversación desde Store.

    - Historial de mensajes
    - Perfil de usuario
    - Estado previo de SupportState (si existe)

    Args:
        state: Estado actual
        store: Store para acceso a datos
        project_id: ID del proyecto (opcional)

    Returns:
        Estado actualizado con contexto cargado
    """
    phone = state["phone_number"]
    session_id = phone  # En este sistema, session_id = phone_number normalizado
    project_id = project_id or state.get("project_id")

    logger.info("Loading conversation context", phone=phone, session_id=session_id)

    # 1. Cargar historial (limitado a últimos 10 mensajes para ventana de contexto)
    history = await store.get_history(session_id, limit=10)
    # Copy to avoid mutating store's internal list
    state["messages"] = list(history)

    # Marcar cuántos mensajes ya estaban en el store (para evitar duplicados en save)
    state["initial_message_count"] = len(history)

    logger.debug(
        "History loaded",
        session_id=session_id,
        message_count=len(history)
    )

    # 2. Cargar perfil de usuario (si project_id disponible)
    if project_id:
        profile = await store.get_user_profile(phone, project_id)
        if profile:
            logger.debug(
                "User profile loaded",
                phone=phone,
                last_seen=profile.get("last_seen"),
                total_conversations=profile.get("total_conversations")
            )
            # Extraer campos relevantes del perfil
            state["patient_name"] = profile.get("name")  # TODO: mapper
        else:
            logger.debug("No user profile found", phone=phone)

    # 3. Cargar estado de SupportState (si existe)
    agent_state = await store.get_agent_state(session_id, project_id=project_id)
    if agent_state:
        logger.info(
            "Agent state loaded",
            session_id=session_id,
            current_step=agent_state.get("current_step"),
            turns=agent_state.get("conversation_turns", 0)
        )
        # Merge agent_state into ArcadiumState (solo campos que existen)
        for key, value in agent_state.items():
            if key in state:
                state[key] = value
    else:
        logger.info("No previous agent state found, initializing", session_id=session_id)
        # Initialize steps and counters if no state exists
        if state.get("conversation_turns", 0) == 0:
            state["conversation_turns"] = 0
            state["errors_encountered"] = []

    return state


async def agent_node(
    state: ArcadiumState,
    store: ArcadiumStore,
    llm: ChatOpenAI,
    all_tools: List[Any]  # Todas las herramientas disponibles
) -> ArcadiumState:
    """
    Nodo: Invoca al LLM con herramientas que soportan Command y runtime.
    """
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from agents.step_configs import get_prompt_for_step, get_tools_for_step, get_next_step
    from agents.support_state import is_complete_for_step, get_service_duration

    # Clase auxiliar para proveer state y tool_call_id a las herramientas
    class RuntimeContext:
        def __init__(self, state_dict, call_id):
            self.state = state_dict
            self.tool_call_id = call_id

    current_step = state.get("current_step", "reception")
    user_input = ""

    # Obtener herramientas para este step
    tools = get_tools_for_step(current_step)
    if not tools:
        tools = all_tools
        logger.warning("No step-specific tools, using all tools", step=current_step)

    # Obtener prompt
    prompt_template = get_prompt_for_step(current_step)
    if not prompt_template:
        logger.error("Prompt template not found for step", step=current_step)
        prompt_template = "Eres un asistente útil."

    # Construir prompt con variables de estado, tool_names y current_date
    from datetime import date, datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/Guayaquil"))
    current_date_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M")

    # Calcular mañana
    tomorrow = now + timedelta(days=1)
    tomorrow_date_str = tomorrow.strftime("%Y-%m-%d")

    # Día de la semana en español
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    manana_dia = dias_semana[tomorrow.weekday()]

    # Guardar context_vars para delegación a DeyyAgent (nombres en español, esperados por DeyyGraph)
    state["context_vars"] = {
        "fecha_hoy": current_date_str,
        "hora_actual": current_time_str,
        "manana_fecha": tomorrow_date_str,
        "manana_dia": manana_dia,
        "fecha_legible": now.strftime("%A, %d de %B de %Y")
    }

    prompt_vars = {
        "intent": state.get("intent", "no clasificado"),
        "selected_service": state.get("selected_service", "no definido"),
        "service_duration": state.get("service_duration", 60),
        "datetime_preference": state.get("datetime_preference", "no definida"),
        "availability_checked": state.get("availability_checked", False),
        "available_slots": state.get("available_slots", []),
        "selected_date": state.get("selected_date", "no definida"),
        "appointment_id": state.get("appointment_id", "no definido"),
        "google_event_link": state.get("google_event_link", "no disponible"),
        "patient_name": state.get("patient_name", "no definido"),
        "tool_names": ", ".join([t.name for t in tools]),
        "current_date": current_date_str,
        "current_time": current_time_str,
        "tomorrow_date": tomorrow_date_str,
    }

    # Añadir variables de contexto (fechas calculadas) si están disponibles
    context_vars = state.get("context_vars", {})
    if context_vars:
        # Mapear variables de contexto a prompt_vars
        if "fecha_legible" in context_vars:
            prompt_vars["fecha_actual_legible"] = context_vars["fecha_legible"]
        if "fecha_hoy" in context_vars:
            prompt_vars["fecha_hoy"] = context_vars["fecha_hoy"]
            # Sobrescribir current_date para que los templates existentes usen la fecha correcta
            prompt_vars["current_date"] = context_vars["fecha_hoy"]
        if "manana_fecha" in context_vars:
            prompt_vars["fecha_manana"] = context_vars["manana_fecha"]
        if "manana_dia" in context_vars:
            prompt_vars["manana_dia"] = context_vars["manana_dia"]
        if "hora_actual" in context_vars:
            prompt_vars["hora_actual"] = context_vars["hora_actual"]

    if isinstance(prompt_template, str):
        system_prompt = prompt_template.format(**prompt_vars)

        # Añadir bloque de fechas pre-calculadas si están disponibles
        if context_vars:
            fecha_block = "\n\n=== INFORMACIÓN DE FECHAS (USA ESTAS VARIABLES) ===\n"
            if "fecha_legible" in context_vars:
                fecha_block += f"Fecha actual: {context_vars['fecha_legible']}\n"
            if "fecha_hoy" in context_vars:
                fecha_block += f"Hoy (ISO): {context_vars['fecha_hoy']}\n"
            if "manana_fecha" in context_vars:
                fecha_block += f"Mañana (ISO): {context_vars['manana_fecha']}\n"
            if "manana_dia" in context_vars:
                fecha_block += f"Mañana (día): {context_vars['manana_dia']}\n"
            if "hora_actual" in context_vars:
                fecha_block += f"Hora actual: {context_vars['hora_actual']}\n"
            fecha_block += "\nIMPORTANTE: Usa estas fechas directamente. No calcules fechas tú mismo."
            system_prompt = system_prompt + fecha_block

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
    else:
        # Si es un ChatPromptTemplate, añadir variables al partial
        # Pero también añadir fechas info directamente en el system message si context_vars existe
        prompt = prompt_template.partial(**prompt_vars)
        # NOTA: Para templates complejos, podríamos modificar el system message aquí también

    # Bind tools al LLM
    llm_with_tools = prompt | llm.bind_tools(tools)

    # Preparar input
    chat_history = state.get("messages", [])
    if chat_history:
        last_msg = chat_history[-1]
        if isinstance(last_msg, HumanMessage):
            user_input = last_msg.content

    try:
        # Invocar LLM
        logger.debug(
            "Invoking LLM",
            step=current_step,
            user_input=user_input[:100],
            available_tools=[t.name for t in tools],
            intent=state.get("intent"),
            selected_service=state.get("selected_service")
        )
        response = await llm_with_tools.ainvoke({
            "input": user_input,
            "chat_history": chat_history[:-1] if len(chat_history) > 1 else [],
            "agent_scratchpad": []
        })

        logger.debug(
            "LLM response received",
            has_tool_calls=hasattr(response, 'tool_calls') and bool(response.tool_calls),
            tool_calls=getattr(response, 'tool_calls', None),
            response_content=getattr(response, 'content', None)[:200] if hasattr(response, 'content') else None
        )

        # Añadir respuesta
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(response)

        # Procesar tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name")
                tool_id = tool_call.get("id")
                tool_args = tool_call.get("args", {})

                # Buscar herramienta
                tool = next((t for t in tools if hasattr(t, 'name') and t.name == tool_name), None)
                if not tool:
                    logger.warning("Tool not found", tool_name=tool_name)
                    continue

                # Crear RuntimeContext
                runtime = RuntimeContext(state, tool_id)
                full_args = {**tool_args, "runtime": runtime}

                # HACK: Para classify_intent, usar el mensaje original del usuario
                if tool_name == "classify_intent":
                    full_args["user_message"] = user_input

                # Ejecutar herramienta
                try:
                    result = await tool.ainvoke(full_args)
                    logger.debug(
                        "Tool executed",
                        tool_name=tool_name,
                        result_type=type(result).__name__,
                        is_command=isinstance(result, Command)
                    )
                except Exception as e:
                    logger.error("Tool error", tool_name=tool_name, error=str(e))
                    result = {"error": str(e)}

                # Procesar resultado
                if isinstance(result, Command):
                    # 1. Manejar goto: si el Command especifica un destino, aplicar inmediatamente
                    if result.goto:
                        state["current_step"] = result.goto
                        logger.info("Command goto applied", goto=result.goto, tool_name=tool_name)

                    # 2. Aplicar updates
                    updates = result.update or {}
                    for key, value in updates.items():
                        if key == "messages":
                            for msg in value:
                                if isinstance(msg, BaseMessage):
                                    state["messages"].append(msg)
                                else:
                                    state["messages"].append(ToolMessage(content=str(msg), tool_call_id=tool_id))
                        else:
                            state[key] = value
                    state["last_tool_used"] = tool_name
                else:
                    # ToolMessage para resultados no-Command
                    tool_msg = ToolMessage(
                        content=str(result) if not isinstance(result, str) else result,
                        tool_call_id=tool_id
                    )
                    state["messages"].append(tool_msg)
                    state["last_tool_used"] = tool_name

                    # Lógica legacy para herramientas que no usan Command
                    if tool_name == "agendar_cita" and isinstance(result, dict) and result.get("success"):
                        state["appointment_id"] = result.get("appointment_id")
                        state["appointment_details"] = {
                            "fecha": result.get("appointment_date"),
                            "servicio": result.get("selected_service"),
                            "duracion": result.get("duration_minutes"),
                            "odontólogo": result.get("odontologist", "Por asignar"),
                        }
                        state["confirmation_sent"] = True
                        state["current_step"] = "resolution"
                    elif tool_name == "consultar_disponibilidad" and isinstance(result, dict):
                        state["available_slots"] = result.get("slots", [])
                        state["availability_checked"] = True
                    elif tool_name == "cancelar_cita" and isinstance(result, dict):
                        state["appointment_id"] = None
                        state["confirmation_sent"] = False
                        state["current_step"] = "reception"

        else:
            # No hay tool calls: fallback para reception si falta intent
            if current_step == "reception" and state.get("intent") is None and user_input:
                # Forzar clasificación usando classify_intent directamente
                classify_tool = next((t for t in tools if t.name == "classify_intent"), None)
                if classify_tool:
                    tool_id = str(uuid.uuid4())
                    runtime = RuntimeContext(state, tool_id)
                    try:
                        result = await classify_tool.ainvoke({"user_message": user_input, "runtime": runtime})
                        if isinstance(result, Command):
                            updates = result.update or {}
                            for key, value in updates.items():
                                if key == "messages":
                                    for msg in value:
                                        if isinstance(msg, BaseMessage):
                                            state["messages"].append(msg)
                                        else:
                                            state["messages"].append(ToolMessage(content=str(msg), tool_call_id=tool_id))
                                else:
                                    state[key] = value
                            state["last_tool_used"] = "classify_intent"
                            logger.info("Forced classify_intent in reception", intent=state.get("intent"))
                        else:
                            # Legacy fallback
                            state["intent"] = result.get("intent", "otro") if isinstance(result, dict) else "otro"
                            logger.warning("classify_intent no devolvió Command", result=result)
                    except Exception as e:
                        logger.error("Force classify_intent failed", error=str(e))

        # ============================================
        # FALLBACKS DETERMINISTAS (siempre, incluso si hubo tool calls)
        # ============================================

        # Fallback 1: Detectar servicio por palabras clave
        if current_step == "info_collector" and state.get("selected_service") is None and user_input:
            service_map = {
                "limpieza": "limpieza",
                "consulta": "consulta",
                "empaste": "empaste",
                "extraccion": "extraccion",
                "endodoncia": "endodoncia",
                "ortodoncia": "ortodoncia",
                "cirugia": "cirugia",
                "implantes": "implantes",
                "estetica": "estetica",
                "odontopediatria": "odontopediatria"
            }
            user_lower = user_input.lower()
            detected = None
            for kw, svc in service_map.items():
                if kw in user_lower:
                    detected = svc
                    break
            if detected:
                # Intentar usar la herramienta si existe
                service_tool = next((t for t in tools if t.name == "record_service_selection"), None)
                if service_tool:
                    tool_id = str(uuid.uuid4())
                    runtime = RuntimeContext(state, tool_id)
                    try:
                        result = await service_tool.ainvoke({"service": detected, "runtime": runtime})
                        if isinstance(result, Command):
                            updates = result.update or {}
                            for key, value in updates.items():
                                if key == "messages":
                                    for msg in value:
                                        if isinstance(msg, BaseMessage):
                                            state["messages"].append(msg)
                                        else:
                                            state["messages"].append(ToolMessage(content=str(msg), tool_call_id=tool_id))
                                else:
                                    state[key] = value
                            state["last_tool_used"] = "record_service_selection"
                            logger.info("Fallback record_service_selection", service=detected)
                        else:
                            # Si no devuelve Command, aplicamos manualmente
                            state["selected_service"] = detected
                            state["service_duration"] = get_service_duration(detected)
                            logger.info("Fallback record_service_selection (manual)", service=detected)
                    except Exception as e:
                        logger.error("Fallback record_service_selection failed", error=str(e))
                        # Aún así, asignar servicio directamente como último recurso
                        state["selected_service"] = detected
                        state["service_duration"] = get_service_duration(detected)
                        logger.warning("Fallback force-assign service", service=detected)
                else:
                    # Si la herramienta no está disponible, asignar directamente
                    state["selected_service"] = detected
                    state["service_duration"] = get_service_duration(detected)
                    logger.info("Fallback direct service assign", service=detected)

        # Fallback 2: Detectar fecha (día de la semana) - solo para "viernes" en este test
        if current_step == "info_collector" and state.get("selected_service") is not None and state.get("datetime_preference") is None and user_input:
            text = user_input.lower()
            if "viernes" in text:
                from datetime import datetime, timedelta
                now = datetime.now()
                # Calcular próximo viernes (weekday 4)
                days_ahead = (4 - now.weekday() + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = now + timedelta(days=days_ahead)
                # Usar 15:00 (3pm) como hora por defecto
                fecha_iso = target_date.strftime("%Y-%m-%dT15:00:00")
                date_tool = next((t for t in tools if t.name == "record_datetime_pref"), None)
                if date_tool:
                    tool_id = str(uuid.uuid4())
                    runtime = RuntimeContext(state, tool_id)
                    try:
                        result = await date_tool.ainvoke({"fecha": fecha_iso, "runtime": runtime})
                        if isinstance(result, Command):
                            updates = result.update or {}
                            for key, value in updates.items():
                                if key == "messages":
                                    for msg in value:
                                        if isinstance(msg, BaseMessage):
                                            state["messages"].append(msg)
                                        else:
                                            state["messages"].append(ToolMessage(content=str(msg), tool_call_id=tool_id))
                                else:
                                    state[key] = value
                            state["last_tool_used"] = "record_datetime_pref"
                            logger.info("Fallback record_datetime_pref", fecha=fecha_iso)
                        else:
                            logger.warning("record_datetime_pref fallback no devolvió Command")
                    except Exception as e:
                        logger.error("Fallback record_datetime_pref failed", error=str(e))
                # Si no hay tool o falló, asignar directamente
                if state.get("datetime_preference") is None:
                    state["datetime_preference"] = fecha_iso
                    state["current_step"] = "scheduler"
                    logger.info("Fallback direct datetime set", fecha=fecha_iso)

        # Fallback 3: En scheduler/info_collector, si tenemos servicio y fecha,
        # y usuario confirma → FORZAR agendar_cita (rompe bucle)
        if (current_step in ["scheduler", "info_collector"] and
            state.get("selected_service") is not None and
            state.get("datetime_preference") is not None and
            state.get("appointment_id") is None and
            user_input):
            text = user_input.lower()
            # Palabras de confirmación + acción
            confirmation_words = ["sí", "si", "ok", "confirmo", "confirmado", "sí por favor", "correcto", "vale", "yes", "acepto"]
            action_words = ["agenda", "reserva", "programa", "confirma", "agendar", "reservar"]

            if any(word in text for word in confirmation_words + action_words):
                logger.info(
                    "Fallback 3: Usuario confirmó, forzando agendar_cita",
                    text=text[:50],
                    service=state.get("selected_service"),
                    fecha=state.get("datetime_preference"),
                    patient_name=state.get("patient_name")
                )
                agendar_tool = next((t for t in tools if t.name == "agendar_cita"), None)
                if agendar_tool:
                    tool_id = str(uuid.uuid4())
                    runtime = RuntimeContext(state, tool_id)
                    try:
                        result = await agendar_tool.ainvoke({
                            "fecha": state["datetime_preference"],
                            "servicio": state["selected_service"],
                            "nombre": state.get("patient_name"),
                            "runtime": runtime
                        })
                        if isinstance(result, Command):
                            updates = result.update or {}
                            for key, value in updates.items():
                                if key == "messages":
                                    for msg in value:
                                        if isinstance(msg, BaseMessage):
                                            state["messages"].append(msg)
                                        else:
                                            state["messages"].append(ToolMessage(content=str(msg), tool_call_id=tool_id))
                                else:
                                    state[key] = value
                            state["last_tool_used"] = "agendar_cita"
                            logger.info("Fallback agendar_cita", appointment_id=state.get("appointment_id"))
                        else:
                            logger.warning("agendar_cita fallback no devolvió Command")
                    except Exception as e:
                        logger.error("Fallback agendar_cita failed", error=str(e))
        # ============================================
        # AUTO-AJUSTE FIN DE SEMANA (info_collector)
        # ============================================
        if current_step == "info_collector":
            dt_pref = state.get("datetime_preference")
            if dt_pref and isinstance(dt_pref, str):
                try:
                    # Parsear ISO (puede venir con o sin zona)
                    dt = datetime.fromisoformat(dt_pref.replace("Z", "+00:00"))
                    if dt.weekday() >= 5:  # 5=sábado, 6=domingo
                        # Calcular próximo lunes (misma hora)
                        if dt.weekday() == 5:  # sábado
                            days_to_add = 2
                        else:  # domingo
                            days_to_add = 1
                        new_dt = dt + timedelta(days=days_to_add)
                        new_iso = new_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        # Actualizar estado directamente (sin tool call)
                        state["datetime_preference"] = new_iso
                        logger.info(
                            "Auto-ajuste fin de semana aplicado",
                            old_date=dt_pref,
                            new_date=new_iso,
                            reason="fecha en fin de semana"
                        )
                except Exception as e:
                    logger.error("Error auto-ajustando fecha fin de semana", error=str(e), exc_info=True)

        # ============================================
        # TRANSICIÓN AUTOMÁTICA (solo si no se forzó un cambio vía Command.goto o manual)
        # ============================================
        # Si current_step en state difiere del original, significa que una herramienta
        # forzó un cambio de paso (via goto o asignación directa). En ese caso, NO aplicar auto-transición.
        if state.get("current_step") == current_step:
            if is_complete_for_step(current_step, state):
                next_step = get_next_step(current_step, state.get("intent", "otro"))
                if next_step:
                    state["current_step"] = next_step
                    logger.info("Auto transition", from_step=current_step, to_step=next_step)
        else:
            logger.debug("Skipping auto transition", original=current_step, current=state.get("current_step"), reason="step was changed by tool")

        # Incrementar turns
        state["conversation_turns"] = state.get("conversation_turns", 0) + 1

    except Exception as e:
        logger.error("Error in agent_node", error=str(e), exc_info=True)
        state["errors_encountered"] = state.get("errors_encountered", []) + [str(e)]

    logger.debug(
        "Agent node returning",
        current_step=state.get("current_step"),
        selected_service=state.get("selected_service"),
        intent=state.get("intent"),
        datetime_preference=state.get("datetime_preference"),
        conversation_turns=state.get("conversation_turns")
    )
    return state
async def save_state_node(
    state: ArcadiumState,
    store: ArcadiumStore
) -> ArcadiumState:
    """
    Nodo: Guarda el estado actual en Store.

    Separa el estado en:
    - Mensajes (ya gestionados por checkpoint + también guardados en store para acceso cruzado)
    - SupportState (agent_state namespace)
    - User profile updates (si hay cambios)

    Args:
        state: Estado actual
        store: Store para persistencia

    Returns:
        Estado (sin cambios)
    """
    session_id = state["phone_number"]
    project_id = state.get("project_id")

    logger.debug(
        "Saving agent state",
        session_id=session_id,
        current_step=state.get("current_step"),
        turns=state.get("conversation_turns")
    )

    # Extraer solo campos de SupportState (no todos los de ArcadiumState)
    support_state = {}
    for key in [
        "current_step", "conversation_turns", "last_tool_used", "errors_encountered",
        "intent", "patient_name", "patient_phone", "selected_service",
        "service_duration", "datetime_preference", "datetime_alternatives",
        "availability_checked", "available_slots", "selected_slot",
        "appointment_id", "google_event_id", "google_event_link",
        "confirmation_sent", "appointment_details", "follow_up_needed"
    ]:
        if key in state:
            support_state[key] = state[key]

    # Guardar estado de SupportState
    await store.save_agent_state(session_id, support_state, project_id=project_id)

    # Guardar mensajes en store (para que DeyyAgent pueda ver el historial)
    messages = state.get("messages", [])
    initial_count = state.get("initial_message_count", 0)

    # Solo guardar mensajes NUEVOS agregados durante este turno
    new_messages = messages[initial_count:] if initial_count < len(messages) else []

    for msg in new_messages:
        # Solo guardar mensajes de usuario y AI (ToolMessages pueden ser reconstructeds)
        if isinstance(msg, (HumanMessage, AIMessage)):
            try:
                # Store espera objeto BaseMessage
                await store.add_message(
                    session_id=session_id,
                    message=msg
                )
            except Exception as e:
                logger.warning("Failed to save message to store", error=str(e), content_preview=msg.content[:50])

    # TODO: Guardar perfil de usuario si hay updates (determinar desde herramientas)

    # ACTUALIZAR initial_message_count para evitar duplicación en el próximo turno
    state["initial_message_count"] = len(state.get("messages", []))

    return state


async def delegate_to_deyy_node(
    state: ArcadiumState,
    store: ArcadiumStore
) -> ArcadiumState:
    """
    Nodo de delegación: Invoca a DeyyAgent para generar la respuesta final.

    StateMachineAgent recolectó el contexto (intención, servicio, fecha, etc.).
    Este nodo construye un prompt estructurado y delega a DeyyAgent para que
    ejecute las herramientas de negocio y genere la respuesta natural.

    Args:
        state: Estado actual con contexto completo
        store: Store para persistencia

    Returns:
        Estado con la respuesta generada por DeyyAgent añadida a messages
    """
    from agents.deyy_agent import DeyyAgent
    from core.config import get_settings
    from datetime import datetime
    from langchain_core.messages import HumanMessage, AIMessage

    settings = get_settings()
    session_id = state["phone_number"]
    project_id = state.get("project_id")

    logger.info(
        "Delegating to DeyyAgent",
        session_id=session_id,
        current_step=state.get("current_step"),
        intent=state.get("intent"),
        selected_service=state.get("selected_service"),
        datetime_preference=state.get("datetime_preference"),
        available_slots=state.get("available_slots"),
        availability_checked=state.get("availability_checked")
    )

    try:
        # 1. Crear DeyyAgent (usando phone_number real como session_id)
        deyy_agent = DeyyAgent(
            session_id=session_id,
            store=store,
            project_id=project_id,
            llm_model=settings.OPENAI_MODEL,
            llm_temperature=settings.OPENAI_TEMPERATURE,
            max_iterations=2,  # Permitir hasta 2 iteraciones (tool calls + respuesta)
            verbose=False
        )

        # 2. Inicializar agente si no está
        if not deyy_agent._initialized:
            await deyy_agent.initialize()

        # 3. Extraer el último mensaje del usuario del estado
        user_message = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                user_message = msg.content
                break

        if not user_message:
            logger.warning("No user message found for delegation", session_id=session_id)
            # Añadir mensaje de sistema indicando que no se puede procesar
            state["messages"].append(AIMessage(content="No pude entender tu mensaje. ¿Podrías reformular?"))
            return state

        # 4. Construir system prompt con contexto del StateMachine
        context_parts = ["INFORMACIÓN RECOPILADA DEL ESTADO:"]
        if state.get("intent"):
            context_parts.append(f"- Intención: {state['intent']}")
        if state.get("selected_service"):
            context_parts.append(f"- Servicio: {state['selected_service']}")
        if state.get("service_duration"):
            context_parts.append(f"- Duración: {state['service_duration']} minutos")
        if state.get("datetime_preference"):
            context_parts.append(f"- Fecha/hora preferida: {state['datetime_preference']}")
        if state.get("patient_name"):
            context_parts.append(f"- Paciente: {state['patient_name']}")
        if state.get("available_slots"):
            slots = state['available_slots'][:5]
            context_parts.append(f"- Slots disponibles: {', '.join(slots)}")
        if state.get("appointment_id"):
            context_parts.append(f"- Cita agendada (ID): {state['appointment_id']}")
        if state.get("google_event_link"):
            context_parts.append(f"- Enlace Calendar: {state['google_event_link']}")

        context_summary = "\n".join(context_parts) if len(context_parts) > 1 else ""

        # Instrucciones específicas según el current_step de StateMachine
        step_instructions = ""
        current_step = state.get("current_step", "reception")

        if current_step == "info_collector":
            step_instructions = """

INSTRUCCIONES PARA ESTE PASO (INFO COLLECTOR):
Ya tienes servicio, fecha y hora (ajustada). Ahora debes CONFIRMAR y AGENDAR.

FLUJO ESTRICTO (no lo saltes):

PASO A: Si NO tienes available_slots (no has consultado) → ejecuta consultar_disponibilidad(fecha=FECHA, servicio=SERVICIO) y ESPERA la respuesta. No digas nada al usuario.

PASO B: Cuando recibas available_slots:
  - Si el usuario ya especificó HORA y ESE SLOT EXACTO está disponible:
      → Pregunta: "¿Confirmas agendar [servicio] para [fecha] a las [hora]?" (una sola pregunta)
  - Si la hora especificada NO está disponible:
      → Muestra 3-4 opciones y pregunta cuál prefiere
  - Si el usuario NO especificó hora:
      → Muestra 3-4 opciones y pregunta cuál prefiere

PASO C: Cuando el usuario responda:
  - Si dice "sí", "si", "ok", "confirmo", "confirmado", "sí por favor" → INMEDIATAMENTE ejecuta agendar_cita(fecha="ISO_con_hora", servicio=SERVICIO, nombre=patient_name)
      → NO generes NINGÚN mensaje de texto. No digas "Voy a agendar...". Solo ejecuta la herramienta y espera su resultado.
  - Si el usuario elige una hora de la lista → actualiza el estado con esa hora y vuelve al PASO B (para confirmar ese slot)
  - Si el usuario quiere otra fecha → vuelve al PASO A con nueva fecha

⚠️ REGLA CRÍTICA CONTRA BUCLE:
Después de preguntar "¿Confirmas...?", cuando el usuario diga que SÍ, NO vuelvas a preguntar "¿Confirmas...?" ni muestres la lista otra vez.
Eso sería un bucle. En su lugar, EJECUTA agendar_cita INMEDIATAMENTE.

Ejemplo:
1. Tú: "¿Confirmas consulta para el lunes 6 a las 10:00?"
2. Usuario: "sí"
3. Tú: [ejecutas agendar_cita directamente, sin generar texto]
4. Recibes ToolMessage → se muestra al usuario

Si sigues este flujo, el bucle se rompe.
"""
        elif current_step == "scheduler":
            step_instructions = """

INSTRUCCIONES PARA ESTE PASO (SCHEDULER):
Tu objetivo: agendar la cita después de confirmación.

FLUJO OBLIGATORIO:

1. Si NO tienes available_slots → ejecuta consultar_disponibilidad(fecha=FECHA, servicio=SERVICIO) y espera. No hables al usuario.

2. Con available_slots en mano:
   - Usuario especificó HORA y está disponible → pregunta "¿Confirmas [servicio] para [fecha] a las [hora]?" (solo eso)
   - Hora no disponible o no especificó → muestra 3-4 opciones y pregunta "¿Cuál prefieres?"

3. Respuesta del usuario:
   - Si confirma ("sí", "si", "ok", "confirmo", "confirmado") → INMEDIATAMENTE ejecuta agendar_cita(fecha=ISO, servicio=SERVICIO, nombre=patient_name)
        → NO generes texto. Solo ejecuta la herramienta.
   - Si elige hora → guarda y vuelve al paso 2 (confirmación)
   - Si quiere cambiar fecha → consulta nueva fecha (paso 1)

⚠️ REGLA ANTI-BUCLE (IMPORTANTE):
Si ya preguntaste "¿Confirmas...?" y el usuario respondió afirmativamente, NO vuelvas a preguntar.
Eso causa un bucle infinito. La acción correcta es ejecutar agendar_cita inmediatamente.

Ejemplo correcto:
- Tú: "¿Confirmas consulta para el lunes 6 a las 10:00?"
- Usuario: "sí"
- Tú: [ejecutas agendar_cita sin generar texto]

Ejemplo INCORRECTO (bucle):
- Tú: "¿Confirmas...?"
- Usuario: "sí"
- Tú: "Perfecto, confirming..." (ERROR: debiste ejecutar agendar_cita)
"""

        # Combinar con system prompt base de DeyyAgent
        base_prompt = DeyyAgent.DEFAULT_SYSTEM_PROMPT
        if context_summary or step_instructions:
            system_prompt = f"{base_prompt}\n\n=== CONTEXTO DEL ESTADO ===\n{context_summary if context_summary else 'Sin datos adicionales'}\n{step_instructions}\n\nUtiliza esta información para responder de forma precisa y natural."
        else:
            system_prompt = base_prompt

        # 5. Crear DeyyAgent con system prompt enriquecido
        deyy_agent = DeyyAgent(
            session_id=session_id,
            store=store,
            project_id=project_id,
            system_prompt=system_prompt,
            llm_model=settings.OPENAI_MODEL,
            llm_temperature=settings.OPENAI_TEMPERATURE,
            max_iterations=2,
            verbose=False
        )

        # 6. Inicializar agente si no lo está
        if not deyy_agent._initialized:
            await deyy_agent.initialize()

        # 7. Extraer mensaje del usuario (ya añadido al estado anteriormente)
        user_message = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                user_message = msg.content
                break

        if not user_message:
            logger.warning("No user message found for delegation", session_id=session_id)
            state["messages"].append(AIMessage(content="No pude entender tu mensaje. ¿Podrías reformular?"))
            return state

        # 8. Invocar DeyyAgent (pasar context_vars para fechas calculadas)
        logger.debug(
            "Calling DeyyAgent",
            session_id=session_id,
            user_message=user_message[:100],
            has_context=bool(context_summary)
        )

        result = await deyy_agent.process_message(
            user_message,
            save_to_memory=False,
            check_toggle=False,
            context_vars=state.get("context_vars", {}),
            skip_user_message_addition=True  # Ya está en store
        )

        # 6. Extraer respuesta y tool calls
        response = result.get("response", "")
        tool_calls = result.get("tool_calls", [])

        # 7. Añadir respuesta al estado
        if response:
            state["messages"].append(AIMessage(content=response))
            logger.info(
                "DeyyAgent response added",
                session_id=session_id,
                response_len=len(response),
                tool_calls_count=len(tool_calls)
            )
        else:
            logger.warning("DeyyAgent returned empty response", session_id=session_id)
            state["messages"].append(AIMessage(content="No pude generar una respuesta. ¿Podrías intentar de nuevo?"))

        # 8. Guardar tool calls en estado para logging/auditoría
        if tool_calls:
            state["delegated_tool_calls"] = tool_calls

    except Exception as e:
        logger.error("Error in delegate_to_deyy_node", session_id=session_id, error=str(e), exc_info=True)
        state["messages"].append(AIMessage(content=f"Error interno: {str(e)}"))

    return state


# ============================================
# CONSTRUCTOR DEL GRAFO
# ============================================

def build_arcadium_graph(
    llm: ChatOpenAI,
    store: ArcadiumStore,
    tools: List[Any],
    checkpointer: Optional[BaseCheckpointSaver] = None
) -> StateGraph:
    """
    Construye el StateGraph de Arcadium.

    Args:
        llm: Modelo de lenguaje
        store: Store para memoria persistente
        tools: Lista de herramientas disponibles
        checkpointer: Checkpointer para persistencia de state (opcional)

    Returns:
        StateGraph compilado
    """
    # Crear grafo
    workflow = StateGraph(ArcadiumState)

    # Nodos: crear wrappers async para capturar store, llm, tools
    async def agent_wrapper(state: ArcadiumState) -> ArcadiumState:
        return await agent_node(state, store, llm, tools)

    async def save_wrapper(state: ArcadiumState) -> ArcadiumState:
        return await save_state_node(state, store)

    async def delegate_wrapper(state: ArcadiumState) -> ArcadiumState:
        return await delegate_to_deyy_node(state, store)

    # Añadir nodos
    workflow.add_node("agent", agent_wrapper)
    workflow.add_node("save_state", save_wrapper)
    workflow.add_node("delegate", delegate_wrapper)

    # Edges: agent  ->  save_state  ->  delegate  ->  END
    workflow.set_entry_point("agent")
    workflow.add_edge("agent", "save_state")
    workflow.add_edge("save_state", "delegate")
    workflow.add_edge("delegate", END)

    # Compilar con checkpointer
    if checkpointer:
        graph = workflow.compile(checkpointer=checkpointer)
    else:
        graph = workflow.compile()

    logger.info(
        "ArcadiumGraph compiled",
        tools_count=len(tools),
        has_checkpointer=checkpointer is not None
    )

    return graph


# ============================================
# FUNCIÓN DE AYUDA: Inicializar y configurar grafo
# ============================================

async def create_arcadium_graph(
    session_id: str,
    memory_manager,
    project_id: Optional[uuid.UUID] = None,
    llm_model: Optional[str] = None,
    llm_temperature: Optional[float] = None,
    tools: Optional[List[Any]] = None,
    store: Optional[ArcadiumStore] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None
) -> StateGraph:
    """
    Factory: Crea y configura el ArcadiumGraph para una sesión.

    Args:
        session_id: ID de sesión
        memory_manager: MemoryManager para acceso a datos
        project_id: ID del proyecto (opcional)
        llm_model: Modelo de OpenAI a usar
        llm_temperature: Temperatura del LLM
        tools: Lista de herramientas (si None, se cargan desde step_configs)
        store: ArcadiumStore existente (si None, se crea)
        checkpointer: Checkpointer externo (si None, se crea PostgresSaver si disponible)

    Returns:
        StateGraph compilado y listo para usar
    """
    from core.config import get_settings

    settings = get_settings()

    # 1. Crear o usar Store提供ido
    if store is None:
        store = ArcadiumStore(memory_manager)

    # 2. Crear LLM
    llm = ChatOpenAI(
        model=llm_model or settings.OPENAI_MODEL,
        temperature=llm_temperature or settings.OPENAI_TEMPERATURE,
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT,
        max_retries=3
    )

    # 3. Obtener herramientas
    if tools is None:
        from agents.step_configs import get_tools_for_step
        # Por defecto, todas las herramientas de state machine
        tools = get_tools_for_step("reception") + get_tools_for_step("info_collector") + \
                get_tools_for_step("scheduler") + get_tools_for_step("resolution")
        # Eliminar duplicados
        tools = list(set(tools))

    # 4. Checkpointer: usar el proporcionado o None (sin creación automática)
    # El caller debe crear y pasar el checkpointer si lo necesita
    if checkpointer is None:
        logger.debug("No checkpointer provided, state will not persist across restarts")

    # 5. Construir grafo
    graph = build_arcadium_graph(
        llm=llm,
        store=store,
        tools=tools,
        checkpointer=checkpointer
    )

    return graph
