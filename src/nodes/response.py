"""response — nodos de generación de respuesta."""

from __future__ import annotations

from typing import Any, Dict

import structlog

from langchain_core.messages import ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from functools import partial

from src.state import ArcadiumState
from src.llm_extractors import generate_deyy_response
from config.calendar_mapping import get_email_for_short_key
from memory_agent_integration.memory_tools import upsert_memory_arcadium, save_patient_memory
from agents.langchain_compat import create_openai_tools_agent
from src.nodes_backup import (
    _build_llm_context,
    _format_datetime_readable,
    _format_slots,
    _GENERATE_RESPONSE_SYSTEM_WITH_TOOLS,
    _last_human_text,
)

logger = structlog.get_logger("langgraph.nodes.response")


async def node_generate_response(
    state: ArcadiumState,
    *,
    llm=None,
) -> Dict[str, Any]:
    """
    Genera el mensaje final de Deyy.
    1 llamada al LLM. Sin tools. Solo texto→texto.
    """
    if not llm:
        fallback = "Lo siento, hubo un error. Por favor intente nuevamente o llame a la clínica. 📞"
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content=fallback)]}

    context = _build_llm_context(state)
    history = state.get("messages", [])
    text = await generate_deyy_response(context, llm, history=history)

    from langchain_core.messages import AIMessage

    return {"messages": [AIMessage(content=text)]}



async def node_generate_response_with_tools(
    state: Dict[str, Any],
    *,
    llm=None,
    vector_store=None,
) -> Dict[str, Any]:

    if not llm:
        from langchain_core.messages import AIMessage

        fallback = "Lo siento, hubo un error. Por favor intente nuevamente o llame a la clínica. 📞"
        return {"messages": [AIMessage(content=fallback)]}

    # ✅ contador de iteraciones
    iterations = state.get("_tool_iterations", 0) + 1

    # ✅ contexto
    context_dict = _build_llm_context(state)
    import json
    context_json = json.dumps(context_dict, indent=2, ensure_ascii=False)

    # Pretty print del contexto en los logs para observabilidad
    logger.info("CONTEXTO LLM (FUENTE DE VERDAD):\n%s", context_json)

    context_parts = [
        f"DATOS ESTRUCTURADOS DEL SISTEMA (FUENTE DE VERDAD):\n{context_json}"
    ]

    # CRÍTICO: siempre incluir la hora/fecha actual de Ecuador.
    # El system prompt dice "usa SIEMPRE hora_actual_ecuador" pero sin este dato
    # el LLM no puede evaluar si un slot ya pasó o si está en el futuro.
    system_time = context_dict.get("system_time", {})
    hora_ec = system_time.get("hora_ecuador")
    fecha_ec = system_time.get("fecha_hoy")
    dia_ec = system_time.get("dia_semana")
    if hora_ec:
        context_parts.append(
            f"Sincronización Temporal: Hora actual en Ecuador: {hora_ec} del {dia_ec} {fecha_ec}. "
            "REGLA CRÍTICA: Los slots PASADOS respecto a esta hora NO deben ofrecerse bajo ninguna circunstancia."
        )

    intent = context_dict.get("flow", {}).get("intent")
    if intent:
        context_parts.append(f"Intención detectada: {intent}")

    missing = context_dict.get("flow", {}).get("missing_fields", [])
    if missing:
        context_parts.append(
            f"Estado de validación: Faltan los siguientes campos: {', '.join(missing)}. "
            "Instrucción: Pídelos de uno en uno, no todos a la vez."
        )

    user = context_dict.get("user", {})
    patient_name = user.get("name")
    if patient_name:
        context_parts.append(f"Paciente: {patient_name}")

    selected_service = user.get("selected_service")
    if selected_service:
        context_parts.append(f"Servicio: {selected_service}")

    # Cuando la operación fue confirmada, mostrar el slot AGENDADO (no la preferencia original).
    # La preferencia original (datetime_preference) puede diferir del slot real (selected_slot)
    # y confunde al LLM haciéndole pensar que "el horario ya pasó".
    booked_slot_ctx = context_dict.get("availability", {}).get("booked_slot")
    if booked_slot_ctx:
        context_parts.append(f"Cita agendada en: {_format_datetime_readable(booked_slot_ctx)}")
    elif context_dict.get("availability", {}).get("requested_datetime"):
        datetime_pref = context_dict["availability"]["requested_datetime"]
        context_parts.append(f"Preferencia temporal del usuario: {datetime_pref}")

    # ✅ slots disponibles — mostrar los 4 más cercanos a la hora solicitada
    slots = context_dict.get("availability", {}).get("slots_available", [])
    exact_match = None  # Inicializar para evitar UnboundLocalError
    if slots:
        preferred_slots = slots
        # --- LÓGICA DE COMPARACIÓN DE SLOTS ---
        if datetime_pref:
            logger.info("DEBUG_MATCH: Iniciando comparacion de slots", pref=datetime_pref, slots=slots)
            try:
                # 1. Normalizamos la preferencia a 16 caracteres (YYYY-MM-DDTHH:MM)
                # Esto ignora segundos y diferencias de zona horaria (Z vs -05:00)
                pref_clean = datetime_pref[:16]

                for s in slots:
                    try:
                        # 2. Normalizamos el slot del calendario de la misma forma
                        slot_clean = s[:16]

                        # 3. Comparación de strings pura
                        if pref_clean == slot_clean:
                            logger.info("DEBUG_MATCH: ¡MATCH EXITOSO!", slot=s, pref=datetime_pref)
                            exact_match = s
                            break
                    except Exception as e:
                        logger.warning(f"Error comparando slot individual {s}: {e}")
            
            except Exception as e:
                logger.error(f"DEBUG_MATCH: Error general en lógica de match: {e}")

        # --- GENERACIÓN DE CONTEXTO PARA EL LLM ---
        readable = _format_slots(preferred_slots[:4])
        context_parts.append(f"Slots disponibles (formato legible): {readable}")

        if exact_match:
            # INSTRUCCIÓN DOMINANTE: Se coloca al inicio para que el LLM no la ignore
            context_parts.insert(0,
                f"🚨 ORDEN DIRECTA DEL SISTEMA: El horario solicitado ({datetime_pref}) "
                f"ESTÁ CONFIRMADO COMO DISPONIBLE en el calendario. "
                f"Tienes PROHIBIDO decir que no hay disponibilidad o que la agenda está llena. "
                f"Confirma la cita al usuario inmediatamente."
            )
        elif slots and len(slots) > 0:
            # Si hay slots pero no son el exacto, reforzamos que sí hay opciones
            context_parts.append(
                f"SITUACIÓN ACTUAL: Hay {len(slots)} espacios disponibles hoy. "
                "Si el usuario pidió un horario que está en la lista de slots disponibles, "
                "debes proceder con el agendamiento. No des respuestas negativas genéricas."
            )


    selected_slot = state.get("selected_slot")
    if selected_slot:
        context_parts.append(f"Usuario eligió slot: {selected_slot}")

    # FIX: leer desde state (plano), no desde context_dict (anidado).
    # context_dict viene de _build_llm_context que usa claves anidadas como
    # calendar.google_event_id, flow.confirmation_sent — leer plano siempre daba None/False.
    ctype = state.get("confirmation_type")
    confirmation_sent = state.get("confirmation_sent", False)
    appt_id = state.get("appointment_id")
    # CRÍTICO: usar google_event_id como fuente de verdad — appointment_id puede ser
    # "gcal_..." o "pending_db", pero solo google_event_id garantiza que el evento existe.
    google_event_id = state.get("google_event_id")
    lookup_done = state.get("calendar_lookup_done", False)
    cal_found = state.get("calendar_appointment_found", False)
    existing_appts = state.get("existing_appointments", [])
    awaiting = state.get("awaiting_confirmation", False)

    # ── VERDAD ABSOLUTA: Prioridad máxima sobre cualquier intent ──────────────────
    # Si existe un google_event_id y se ha marcado la confirmación como enviada,
    # la cita EXISTE y el LLM DEBE confirmarla, sin importar el intent actual.
    if google_event_id and confirmation_sent:
        _booked = state.get("selected_slot", "")
        _booked_hr = _format_datetime_readable(_booked) if _booked else "el horario confirmado"
        context_parts.insert(0,
            f"✅ VERDAD ABSOLUTA DEL SISTEMA: La operación fue exitosa. "
            f"Google Calendar ID: {google_event_id}. "
            f"Hora agendada: {_booked_hr}. "
            "PROHIBIDO decir que la hora ya pasó, que no hay disponibilidad, "
            "o mencionar horarios distintos al agendado. "
            "Tu ÚNICA misión es confirmar la cita al usuario con entusiasmo y claridad."
        )

    # ── GUARDIAS CRÍTICAS: prevenir confirmaciones falsas ──────────────────
    # Regla global: si confirmation_sent=False, NINGUNA operación fue ejecutada.

    # REGLA DE ORO ABSOLUTA: Si el intent es agendar/reagendar/cancelar y no se ha enviado
    # la confirmación, el LLM tiene PROHIBIDO decir que la operación fue exitosa.
    if intent in ("agendar", "reagendar", "cancelar") and not confirmation_sent:
        # Intercepción agresiva: Si el flujo es agendar y hay slots pero NO se ha ejecutado el booking,
        # el LLM NO puede confirmar.
        if intent == "agendar" and slots and not confirmation_sent:
             context_parts.insert(0,
                "🚨 ALERTA DE SEGURIDAD CRÍTICA: EL SISTEMA NO HA EJECUTADO EL BOOKING. "
                "Tienes PROHIBIDO usar palabras como 'agendada', 'confirmada', 'listo' o el emoji ✅. "
                "Aunque veas que hay un slot que coincide, NO confirmes la cita. "
                "Cualquier frase que sugiera que la cita ya existe es una MENTIRA. "
                "Tu ÚNICA misión es mostrar los slots disponibles y pedir al usuario que confirme uno."
            )
        else:
            context_parts.insert(0,
                "🚨 ALERTA DE SEGURIDAD CRÍTICA: El sistema NO ha ejecutado ninguna operación de reserva. "
                "Tienes PROHIBIDO usar palabras como 'agendada', 'confirmada', 'listo' o el emoji ✅. "
                "Cualquier frase que sugiera que la cita ya existe o fue creada es una MENTIRA y una alucinación. "
                "Sigue estrictamente el flujo: si faltan datos, pídelos; si hay slots, ofrécelos. "
                "NUNCA confirmes el éxito hasta que confirmation_sent sea True."
            )

    # ── PUERTA DE VERDAD (Truth Gate) ──────────────────────────────────────
    # Si el flujo es crítico y faltan datos esenciales, el LLM debe ser restringido
    # para que no alucine la respuesta.

    if intent == "agendar":
        # PRIORIDAD MÁXIMA: Verificamos si la cita ya fue creada en este turno.
        # FIX: google_event_id + confirmation_sent — evitar que una cita EXISTENTE
        # (encontrada por check_existing para un paciente que ya tiene cita) se confunda
        # con una cita RECIÉN CREADA. confirmation_sent solo se setea en node_book_appointment.
        if google_event_id and confirmation_sent:
            _sl = state.get("selected_slot", "")
            _sl_hr = _format_datetime_readable(_sl) if _sl else "el horario confirmado"
            context_parts.insert(0,
                f"✅ VERDAD ABSOLUTA: LA CITA HA SIDO CREADA EXITOSAMENTE (ID: {google_event_id}). "
                f"Slot agendado: {_sl_hr}. "
                "PROHIBIDO decir que la hora ya pasó o que no hay disponibilidad. "
                "Confirma al usuario SU CITA con el horario exacto indicado arriba."
            )
        elif not lookup_done and not slots:
            context_parts.append(
                "🚫 BLOQUEO DE RESPUESTA: No se ha verificado la disponibilidad en el calendario. "
                "PROHIBIDO confirmar cualquier horario. Debes informar que estás verificando "
                "la disponibilidad y esperar a que el sistema proporcione los slots."
            )
        elif not slots and state.get("_slots_checked", False):
            # BLOQUEO solo cuando node_check_availability corrió Y no encontró slots.
            # Condición anterior usaba cal_found para inferir "no slots", pero eso
            # dispara incorrectamente cuando hay una cita en OTRO día (cal_found=True,
            # slots=[], pero check_availability nunca corrió para el día solicitado).
            context_parts.append(
                "🚫 BLOQUEO DE RESPUESTA: El calendario no devolvió slots disponibles para la fecha solicitada. "
                "NO inventes horarios. Informa que no hay disponibilidad en ese horario "
                "y sugiere otro día o franja horaria."
            )
        elif not confirmation_sent:
            # EL CASO CRÍTICO: Hay slots, pero NO se ha ejecutado el booking.
            # El LLM NO puede confirmar, solo puede ofrecer los slots.
            context_parts.append(
                "🚫 BLOQUEO DE CONFIRMACIÓN: Tienes slots disponibles, pero la cita AÚN NO ha sido creada. "
                "Tienes PROHIBIDO decir 'Su cita ha sido agendada' o 'está confirmada'. "
                "Tu ÚNICA misión es mostrar los slots disponibles y pedir al usuario que confirme uno."
            )

    if intent in ("reagendar", "cancelar"):
        # Si quiere modificar pero no sabemos si tiene cita
        if not lookup_done:
            context_parts.append(
                "🚫 BLOQUEO DE RESPUESTA: Aún no se ha verificado si el usuario tiene una cita activa. "
                "PROHIBIDO decir 'He encontrado su cita' o 'Procedo a cancelarla'. "
                "Informa que estás consultando el sistema."
            )
        elif not cal_found:
            # VERDAD ABSOLUTA: El sistema confirmó que NO hay citas.
            # El LLM debe ser restringido agresivamente para que no use memoria residual.
            context_parts.insert(0,
                "🚨 ALERTA DE SEGURIDAD CRÍTICA: Se verificó Google Calendar y NO EXISTE ninguna cita activa "
                "para este teléfono. Tienes PROHIBIDO mencionar cualquier horario previo, cita existente "
                "o referirte a una 'cita programada'. Cualquier dato que sugiera que el usuario tiene una cita "
                "es una ALUCINACIÓN. Tu ÚNICA respuesta debe ser informar que no hay citas registradas "
                "y ofrecer agendar una nueva."
            )
            context_parts.append(
                "🚫 BLOQUEO DE RESPUESTA: Se verificó el sistema y NO hay citas activas. "
                "PROHIBIDO confirmar cualquier cancelación o cambio. "
                "Informa claramente que no existe una cita registrada para este teléfono."
            )

    # 1. Agendar en progreso (slots mostados, esperando selección del usuario)
    if awaiting and ctype == "book" and not confirmation_sent and not google_event_id:
        context_parts.append(
            "⚠️ INSTRUCCIÓN CRÍTICA: La cita AÚN NO ha sido creada en el sistema. "
            "Muestra los horarios disponibles y pide al usuario que confirme cuál prefiere. "
            "PROHIBIDO decir 'Su cita ha sido agendada' o frases similares hasta que el sistema lo confirme."
        )

    # 2. Reagendar/cancelar sin operación ejecutada — basado en INTENT (ignora ctype stale)
    # Esto cubre el caso donde ctype="book" de un turno anterior pero intent ya es reagendar/cancelar.
    if intent in ("reagendar", "cancelar") and not confirmation_sent:
        if lookup_done and not cal_found:
            context_parts.append(
                "⚠️ INSTRUCCIÓN CRÍTICA: Se verificó Google Calendar y NO existe ninguna cita activa "
                "para este usuario en el sistema. "
                "PROHIBIDO confirmar reagendamiento o cancelación. "
                "Informa que no hay cita activa y ofrece agendar una nueva si lo desea."
            )
        else:
            context_parts.append(
                "⚠️ INSTRUCCIÓN CRÍTICA: La operación de reagendamiento/cancelación AÚN NO se ha ejecutado. "
                "PROHIBIDO decir 'Su cita ha sido reagendada' o 'Su cita ha sido cancelada'. "
                "Solo usa esas frases cuando el sistema confirme explícitamente que la operación fue exitosa."
            )

    # ── Forcing tool: resultado de verificación en Calendar ──────────────
    # IMPORTANTE: Solo aplica si el booking NO ha sido ejecutado aún.
    # Si confirmation_sent + google_event_id → la cita ya fue creada, estas guards no aplican.
    booking_done = bool(google_event_id and confirmation_sent)
    if not booking_done and lookup_done and cal_found and existing_appts and intent == "agendar":
        # Usuario quiere agendar pero YA TIENE cita(s)
        lines = []
        for appt in existing_appts[:2]:
            svc_name = appt.get("summary", "cita")
            start_dt = appt.get("start", "")
            lines.append(f"• {svc_name} — {_format_datetime_readable(start_dt)}")
        appts_str = "\n".join(lines)
        context_parts.append(
            f"SITUACIÓN REAL: Se consultó Google Calendar y el paciente YA TIENE cita(s) agendada(s):\n"
            f"{appts_str}\n"
            "Informa al usuario sobre su(s) cita(s) existente(s) y pregunta si desea "
            "reagendar, cancelar o agregar una cita adicional."
        )
    elif not booking_done and lookup_done and not cal_found and existing_appts and intent == "agendar":
        # Calendar verificado: hay citas futuras pero en OTROS días (sin conflicto hoy).
        # Informar como contexto SIN bloquear el nuevo agendamiento.
        lines = []
        for appt in existing_appts[:2]:
            svc_name = appt.get("summary", "cita")
            start_dt = appt.get("start", "")
            lines.append(f"• {svc_name} — {_format_datetime_readable(start_dt)}")
        context_parts.append(
            f"ℹ️ CONTEXTO: El paciente tiene cita(s) en otro(s) día(s):\n"
            + "\n".join(lines)
            + "\nEstas citas NO interfieren con el nuevo agendamiento solicitado. "
            "Procede a agendar la nueva cita para la fecha solicitada. "
            "Puedes mencionarlas brevemente si es relevante."
        )
    elif not booking_done and lookup_done and not cal_found and not existing_appts and intent == "agendar":
        # Verificación real en Calendar: este usuario no tiene ninguna cita futura.
        context_parts.append(
            "⚠️ VERDAD ABSOLUTA DEL SISTEMA: Se verificó Google Calendar en tiempo real y este usuario "
            "NO tiene ninguna cita activa registrada. "
            "PROHIBIDO decir 'ya tiene una cita' o 'su cita está a las...'. "
            "Procede directamente a agendar la nueva cita."
        )

    if confirmation_sent and ctype == "cancel":
        # Cancelación ejecutada exitosamente
        svc = context_dict.get("selected_service", "la cita")
        context_parts.append(
            f"La cita de {svc} ha sido cancelada exitosamente. Confirma al usuario con formato: "
            f'"Su cita de {{servicio}} ha sido cancelada exitosamente."'
        )
    elif confirmation_sent and ctype == "reschedule" and google_event_id:
        # Reagendamiento ejecutado exitosamente — verificado por google_event_id
        svc = context_dict.get("selected_service", "")
        slot = context_dict.get("selected_slot") or context_dict.get("datetime_preference", "")
        context_parts.append(
            f"La cita de {svc} ha sido reagendada para {slot}. Confirma con formato: "
            f'"Su cita de {{servicio}} ha sido reagendada para el {{día}} a las {{hora}}."'
        )
    elif confirmation_sent and google_event_id and (not ctype or ctype == "book"):
        # Booking nuevo ejecutado exitosamente — verificado por google_event_id en Calendar
        svc = context_dict.get("selected_service", "")
        slot = context_dict.get("selected_slot") or context_dict.get("datetime_preference", "")
        context_parts.append(
            f"Cita agendada exitosamente (Google Calendar ID: {google_event_id}): "
            f"{svc} el {slot}. Confirma con formato: "
            f'"Su cita de {{servicio}} ha sido agendada para el {{día}} a las {{hora}}."'
        )
    elif ctype == "cancel" and not confirmation_sent:
        # Esperando confirmación de cancelación
        svc = context_dict.get("selected_service", "la cita")
        lookup_done = context_dict.get("calendar_lookup_done", False)
        found = context_dict.get("calendar_appointment_found", False)
        if lookup_done and not found:
            context_parts.append(
                "IMPORTANTE: Se consultó Google Calendar y NO se encontró ninguna cita activa "
                "para este número de teléfono. Informa al usuario que no tienes citas agendadas "
                "a su nombre y ofrece agendar una nueva si lo desea."
            )
        else:
            # Mostrar la cita existente desde existing_appointments (datetime_preference
            # ya no se sobreescribe con el tiempo de la cita vieja para cancelar tampoco).
            existing = context_dict.get("existing_appointments", [])
            old_dt = existing[0].get("start", "") if existing else ""
            old_dt_info = f" del {_format_datetime_readable(old_dt)}" if old_dt else ""
            context_parts.append(
                f"El usuario quiere CANCELAR su cita de {svc}{old_dt_info}. "
                "Pide confirmación explícita con formato: "
                f"'¿Confirma que desea cancelar su cita de {svc}{old_dt_info}?'"
            )
    elif ctype == "reschedule" and not confirmation_sent:
        # Esperando nueva fecha para reagendar
        svc = context_dict.get("selected_service", "la cita")
        lookup_done = context_dict.get("calendar_lookup_done", False)
        found = context_dict.get("calendar_appointment_found", False)
        if lookup_done and not found:
            context_parts.append(
                "IMPORTANTE: Se consultó Google Calendar y NO se encontró ninguna cita activa "
                "para este número de teléfono. Informa al usuario que no tienes citas agendadas "
                "a su nombre y ofrece agendar una nueva si lo desea."
            )
        else:
            # Mostrar la cita existente desde existing_appointments (datetime_preference
            # ya no se sobreescribe con el tiempo de la cita vieja para reagendar).
            existing = context_dict.get("existing_appointments", [])
            old_dt = existing[0].get("start", "") if existing else ""
            old_dt_info = f" del {_format_datetime_readable(old_dt)}" if old_dt else ""
            context_parts.append(
                f"El usuario quiere REAGENDAR su cita de {svc}{old_dt_info}. "
                "Si ya indicó la nueva hora en este mensaje, procesa el reagendamiento directamente. "
                "Si no, pregunta por la nueva fecha y hora preferida."
            )

    error = context_dict.get("last_error")
    if error:
        context_parts.append(f"Error ocurrido: {error}. Sugiere llamar a la clínica.")
        # Si no hay appointment_id ni calendar credentials, guiar a la clínica
        if not appt_id and not context_dict.get("google_event_id"):
            context_parts.append(
                "No se encontró una cita activa para este usuario. "
                "Sugiere llamar directamente a la clínica para gestionar la cita."
            )

    turns = context_dict.get("conversation_turns", 0)
    if turns >= 8:
        context_parts.append(
            "Ya van muchos mensajes. Considera ofrecer llamar a la clínica."
        )

    semantic = state.get("semantic_memory_context", "")
    intent = context_dict.get("flow", {}).get("intent")

    # ── MEMORIA TIPADA (estilo Claude Code) ──────────────────────────────────
    # El semantic_memory_context puede contener secciones marcadas:
    #   "PERFIL DEL PACIENTE" → tipo 'user' → SIEMPRE en contexto
    #   "CONTEXTO ADICIONAL"  → tipos feedback/project/reference → según intent
    #   "MEMORIAS SEMÁNTICAS" → vector search → según intent
    #
    # Regla: el perfil 'user' (alergias, preferencias permanentes) siempre aplica.
    # Los demás tipos solo en intents no críticos para evitar alucinaciones.
    if semantic:
        profile_section = ""
        extra_section = ""

        # Separar perfil de contexto adicional/semántico
        lines = semantic.split("\n")
        in_profile = False
        in_extra = False
        profile_lines: list[str] = []
        extra_lines: list[str] = []

        for line in lines:
            if line.startswith("PERFIL DEL PACIENTE"):
                in_profile = True
                in_extra = False
                profile_lines.append(line)
            elif line.startswith("CONTEXTO ADICIONAL") or line.startswith("MEMORIAS SEMÁNTICAS"):
                in_extra = True
                in_profile = False
                extra_lines.append(line)
            elif in_profile:
                profile_lines.append(line)
            elif in_extra:
                extra_lines.append(line)

        profile_section = "\n".join(profile_lines).strip()
        extra_section = "\n".join(extra_lines).strip()

        # Perfil del paciente: siempre incluir (incluso en intents críticos)
        if profile_section:
            context_parts.append(profile_section)

        # Contexto adicional: solo en intents no críticos
        if extra_section and intent not in ("agendar", "cancelar", "reagendar"):
            context_parts.append(extra_section)


    # ── GUARD CRÍTICO: confirmation_result ───────────────────────────────
    # Se evalúa ANTES del razonamiento para cortar cualquier alucinación.
    confirmation_result = state.get("confirmation_result")
    if confirmation_result == "unknown" and not confirmation_sent:
        context_parts.append(
            "⚠️ ALERTA CRÍTICA: El sistema detectó que la última respuesta del usuario "
            "NO fue una confirmación clara (resultado: 'desconocido'). "
            "PROHIBIDO ABSOLUTO: decir 'cancelada', 'agendada', 'exitosamente', '✅' o cualquier frase "
            "que implique que una operación fue completada. "
            "ACCIÓN OBLIGATORIA: Pide al usuario que confirme explícitamente con 'sí' o 'no'."
        )

    # ── PASOS DE RAZONAMIENTO (Estilo n8n) ──────────────────────────────
    # Obligamos al LLM a seguir un proceso estructurado antes de responder.
    context_parts.append(
        "INSTRUCCIÓN DE PROCESAMIENTO (Sigue estos pasos estrictamente):\n"
        "PASO 1 - CONTEXTO DEL SISTEMA (PRIORIDAD MÁXIMA): El JSON estructurado de arriba es la ÚNICA FUENTE DE VERDAD. "
        "Ignora cualquier dato del historial de conversación que contradiga el JSON del sistema. "
        "Los mensajes anteriores del asistente son solo referencia — NUNCA son más confiables que el JSON actual.\n"
        "PASO 2 - REVISIÓN DE HISTORIAL: Lee los mensajes anteriores SOLO para entender qué pidió el cliente "
        "en este turno. Si el historial contradice el JSON del sistema, el JSON SIEMPRE gana.\n"
        "PASO 3 - RAZONAMIENTO (Think): ¿confirmation_sent es True? → la operación SÍ se ejecutó. "
        "¿confirmation_sent es False? → NINGUNA operación fue ejecutada, sin importar qué diga el historial.\n"
        "PASO 4 - VALIDACIÓN DE SALIDA: Si confirmation_sent=False, está PROHIBIDO decir que una cita "
        "fue creada, cancelada o reagendada. Si confirmation_result='unknown', pide confirmación de nuevo.\n"
        "PASO 5 - RESPUESTA: Responde de forma amable, corta (máx 3-4 líneas) y basada solo en la verdad del sistema."
    )

    context_str = (
        "\n".join(context_parts) if context_parts else "Sin contexto específico."
    )

    system_prompt = _GENERATE_RESPONSE_SYSTEM_WITH_TOOLS.format(context=context_str)

    # ✅ mensajes LLM
    from langchain_core.messages import SystemMessage, AIMessage, ToolMessage

    # ── Filtro de bajo nivel: trim_messages antes de enviar al LLM ──────────
    # Garantiza que solo el contexto reciente y relevante llegue a OpenAI.
    # Estrategia "last": preserva los mensajes más recientes hasta MAX_LLM_TOKENS.
    # start_on="human": el bloque recortado siempre empieza con un HumanMessage
    # (requisito de la API de OpenAI para evitar 400 "first message must be human").
    from langchain_core.messages import trim_messages as _trim

    MAX_LLM_TOKENS = 3_000
    raw_history = list(state.get("messages", []))
    try:
        history = _trim(
            raw_history,
            strategy="last",
            token_counter=llm,
            max_tokens=MAX_LLM_TOKENS,
            include_system=False,
            allow_partial=False,
            start_on="human",
        )
    except Exception:
        # Fallback count-based si el modelo no soporta token counting
        history = raw_history[-10:]

    # Sanear historial: OpenAI rechaza (400) si un AIMessage con tool_calls
    # no está seguido por ToolMessages con cada tool_call_id.
    # Eliminamos los AIMessages con tool_calls huérfanos (sin respuesta).
    sanitized = []
    i = 0
    while i < len(history):
        msg = history[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            # Recolectar IDs esperados
            expected_ids = {tc["id"] for tc in msg.tool_calls}
            # Ver si los siguientes mensajes responden todos los tool_calls
            j = i + 1
            found_ids = set()
            while j < len(history) and isinstance(history[j], ToolMessage):
                found_ids.add(history[j].tool_call_id)
                j += 1
            if expected_ids <= found_ids:
                # Completo: incluir AIMessage + sus ToolMessages
                sanitized.extend(history[i:j])
                i = j
            else:
                # Incompleto: descartar este AIMessage (y sus ToolMessages parciales si los hay)
                logger.warning(
                    "generate_response: descartando AIMessage con tool_calls huérfanos",
                    expected=list(expected_ids),
                    found=list(found_ids),
                )
                i = j  # saltar también los ToolMessages parciales
        else:
            sanitized.append(msg)
            i += 1

    lm_messages = [SystemMessage(content=system_prompt)] + sanitized

    # ✅ tool binding — siempre exponer save_patient_memory si hay phone
    phone_number = state.get("phone_number", "")
    memory_tools = []
    if phone_number:
        # save_patient_memory: tool principal tipado (siempre disponible)
        memory_tools.append(save_patient_memory)
        # upsert_memory_arcadium: legado vectorial (solo si hay vector_store)
        if vector_store:
            memory_tools.append(upsert_memory_arcadium)
    else:
        logger.warning("No hay phone_number en estado, omitiendo memory tools")

    try:
        if memory_tools:
            llm_with_tools = llm.bind_tools(memory_tools)
            response = await llm_with_tools.ainvoke(lm_messages)
        else:
            response = await llm.ainvoke(lm_messages)

        return {
            "messages": [response],
            "_tool_iterations": iterations,
        }

    except Exception as e:
        logger.error("Error en node_generate_response_with_tools", error=str(e))

        from langchain_core.messages import AIMessage

        return {
            "messages": [
                AIMessage(content="Lo siento, hubo un error generando la respuesta.")
            ],
            "_tool_iterations": iterations,
            "last_error": str(e),
            "should_escalate": True,
        }



async def node_get_appointment_history(
    state: ArcadiumState,
    *,
    calendar_service=None,
    calendar_services=None,
) -> Dict[str, Any]:
    """
    Obtiene historial de citas del usuario (próximos 30 días).
    DETERMINISTA — cero LLM.

    Busca en Google Calendar eventos del usuario y retorna lista de citas.
    """
    calendar_service = _resolve_calendar_service(state, calendar_services, calendar_service)
    if not calendar_service:
        logger.warning("node_get_appointment_history: sin calendar_service, saltando")
        return {"existing_appointments": [], "calendar_lookup_done": True}

    phone = state.get("phone_number", "")
    if not phone:
        logger.warning("node_get_appointment_history: sin phone_number")
        return {"existing_appointments": [], "calendar_lookup_done": True}

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Guayaquil")
        now = datetime.now(tz)
        future = now + timedelta(days=30)

        events = await calendar_service.search_events_by_query(
            q=phone,
            start_date=now,
            end_date=future,
        )

        matching = []
        for event in events:
            desc = event.get("description", "") or ""
            if phone in desc or phone.lstrip("+") in desc:
                matching.append(event)

        logger.info(
            "node_get_appointment_history: citas encontradas",
            phone=phone,
            count=len(matching),
        )

        return {
            "existing_appointments": matching[:10],
            "calendar_lookup_done": True,
        }

    except Exception as e:
        logger.error("node_get_appointment_history: error consultando Calendar", error=str(e))
        return {
            "existing_appointments": [],
            "calendar_lookup_done": True,
        }



async def node_execute_memory_tools(
    state: ArcadiumState,
    *,
    vector_store=None,
) -> Dict[str, Any]:
    """
    Ejecuta los tool calls de upsert_memory_arcadium presentes en el último mensaje AI.

    Extrae tool_calls y guarda las memorias directamente en el vector_store.
    Devuelve ToolMessages con确认.
    """
    if not vector_store:
        logger.warning("vector_store no disponible, omitiendo ejecución de memory tools")
        return {}

    messages = state.get("messages", [])
    if not messages:
        return {}

    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", [])
    if not tool_calls:
        return {}

    tool_messages = []
    user_id = state.get("phone_number", "")

    for tc in tool_calls:
        tool_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        tool_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
        tool_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)

        if tool_name not in ("upsert_memory_arcadium", "save_patient_memory"):
            logger.warning("Tool desconocido en node_execute_memory_tools", tool_name=tool_name)
            continue

        if not user_id:
            logger.warning("No hay phone_number en estado, omitiendo tool call")
            continue

        from langchain_core.messages import ToolMessage

        try:
            if tool_name == "save_patient_memory":
                # ── Memoria tipada (estilo Claude Code) → guarda en patient_memories ──
                from db import get_async_session
                from services.patient_memory_service import PatientMemoryService

                mem_type  = tool_args.get("type", "user")
                mem_name  = tool_args.get("name", f"mem_{str(uuid.uuid4())[:8]}")
                mem_desc  = tool_args.get("description", "")
                mem_body  = tool_args.get("body", "")

                async with get_async_session() as session:
                    svc = PatientMemoryService(session)
                    await svc.upsert(
                        phone=user_id,
                        type=mem_type,
                        name=mem_name,
                        description=mem_desc,
                        body=mem_body,
                    )

                logger.info(
                    "save_patient_memory ejecutado",
                    phone=user_id,
                    type=mem_type,
                    name=mem_name,
                )
                result_msg = f"Memoria '{mem_name}' ({mem_type}) guardada para {user_id}"

            else:
                # ── Memoria vectorial legada → guarda en vector_store ─────────────
                content   = tool_args.get("content", "")
                context   = tool_args.get("context", "")
                memory_id = tool_args.get("memory_id")

                namespace = ("memories", user_id)
                mem_id = memory_id or str(uuid.uuid4())
                value = {
                    "content": content,
                    "context": context,
                    "timestamp": datetime.now(tz=TIMEZONE).isoformat(),
                }

                if vector_store:
                    await vector_store.aput(namespace, key=mem_id, value=value)
                    logger.info(
                        "upsert_memory_arcadium ejecutado",
                        user_id=user_id,
                        memory_id=mem_id,
                        content=content[:50],
                    )
                    result_msg = f"Memoria vectorial guardada. ID: {mem_id}"
                else:
                    result_msg = "vector_store no disponible — memoria no guardada"

            if tool_id:
                tool_messages.append(ToolMessage(content=result_msg, tool_call_id=tool_id))
            else:
                logger.warning("Tool call sin id, omitiendo ToolMessage")

        except Exception as e:
            logger.error("Error en node_execute_memory_tools", error=str(e), exc_info=True)
            if tool_id:
                tool_messages.append(
                    ToolMessage(content=f"Error guardando memoria: {str(e)}", tool_call_id=tool_id)
                )

    if tool_messages:
        return {"messages": tool_messages}
    return {}



def edge_after_generate_response(state: ArcadiumState) -> str:
    """
    Routing condicional después de generate_response_with_tools.

    Si el último mensaje AI tiene tool_calls y no se ha excedido el límite de iteraciones,
    va a execute_memory_tools. En caso contrario, va a save_state.
    """
    messages = state.get("messages", [])
    if not messages:
        return "save_state"

    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", None)

    if tool_calls:
        iterations = state.get("_tool_iterations", 0)
        if iterations >= 2:
            logger.warning(
                "Límite de tool-iterations alcanzado, omitiendo tool calls",
                iterations=iterations,
            )
            return "save_state"
        logger.debug(
            "Tool calls detectados, enrutando a execute_memory_tools",
            iterations=iterations,
            tool_calls_count=len(tool_calls),
        )
        return "execute_memory_tools"

    return "save_state"
