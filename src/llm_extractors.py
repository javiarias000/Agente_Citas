"""
Extractores LLM — ÚNICOS puntos donde se llama al LLM en todo el grafo.

Cada función:
- Hace EXACTAMENTE 1 llamada al LLM.
- El LLM retorna SIEMPRE JSON válido (sin markdown, sin código).
- Sin tools, sin razonamiento externo al JSON.

El LLM solo:
1. Detecta intención cuando las keywords no bastan.
2. Extrae servicio/fecha/nombre de texto libre.
3. Genera la respuesta final en estilo "Deyy".

FIXES APLICADOS:
- [CRÍTICO] generate_deyy_response no incluía el historial de conversación
  → el LLM generaba respuestas sin coherencia entre turnos (repetía preguntas,
  ignoraba lo que el usuario había dicho antes).
  → Ahora recibe los últimos N mensajes del historial y los incluye en el prompt.
- [MEDIO]  extract_booking_data no usaba el historial para extraer datos
  de mensajes anteriores (ej: nombre dicho 2 turnos atrás).
  → Ahora recibe el historial reciente como contexto adicional.
- [MEDIO]  extract_intent_llm sin historial → "Sí" sin contexto es "otro",
  con historial es una confirmación.
  → Ahora recibe los últimos 3 mensajes como contexto.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

logger = structlog.get_logger("langgraph.llm")

# Número de mensajes del historial a incluir en cada llamada LLM
_HISTORY_WINDOW = 6  # últimos 6 mensajes (3 turnos de conversación)
_INTENT_WINDOW = 3  # menos contexto para clasificación de intención


# ═══════════════════════════════════════════
# 1. EXTRACT INTENT (fallback de keywords)
# ═══════════════════════════════════════════

_EXTRACT_INTENT_SYSTEM = """\
Eres un clasificador de intenciones para una clínica dental en Ecuador.

Clasifica el mensaje del usuario en UNO de estos intents:
- agendar     → quiere agendar una cita
- cancelar    → quiere cancelar una cita existente
- reagendar   → quiere cambiar fecha/hora de una cita
- consultar   → quiere ver horarios disponibles o sus citas
- otro        → saludos, preguntas no relacionadas, etc.

IMPORTANTE: Considera el historial de la conversación para clasificar correctamente.
Por ejemplo, "sí" después de que el asistente ofreció una cita = confirmación de "agendar".

Responde SOLO con este JSON, sin markdown, sin explicación:
{"intent": "agendar", "confidence": 0.95}
"""


async def extract_intent_llm(
    message: str,
    llm,
    history: Optional[List[BaseMessage]] = None,
) -> tuple[str, float]:
    """Detecta intención vía LLM cuando las keywords no bastaron.

    FIX: ahora recibe el historial reciente para clasificar correctamente
    mensajes ambiguos como "sí", "no", "dale" que dependen del contexto.

    Args:
        message: último mensaje del usuario
        llm: instancia del LLM
        history: historial reciente de mensajes (opcional)

    Returns:
        (intent, confidence)
    """
    history_block = _format_history_block(history, limit=_INTENT_WINDOW)

    user_content = ""
    if history_block:
        user_content += f"Historial reciente:\n{history_block}\n\n"
    user_content += f"Mensaje actual: {message}"

    prompt = _build_prompt(_EXTRACT_INTENT_SYSTEM, user_content)
    response = await llm.ainvoke(prompt)
    text = _clean_json(response.content)

    try:
        data = json.loads(text)
        intent = data.get("intent", "otro")
        confidence = float(data.get("confidence", 0.5))
        logger.info("intent extraído por LLM", intent=intent, confidence=confidence)
        return intent, confidence
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "extract_intent_llm JSON inválido", text=text[:200], error=str(e)
        )
        return "otro", 0.0


# ═══════════════════════════════════════════
# 2. EXTRACT BOOKING DATA (servicio, fecha, nombre)
# ═══════════════════════════════════════════

_EXTRACT_DATA_SYSTEM = """\
Eres un extractor de datos para agendar citas dentales.

INSTRUCCIONES:
1. Extrae SOLO lo que el usuario dijo explícitamente. NO inventes.
2. Para fechas relativas, usa las fechas de referencia del contexto.
   - "mañana" = manana_fecha del contexto
   - "pasado mañana" = manana_fecha + 1 día
   - "el viernes" = la próxima fecha que sea viernes (usa fecha_hoy como referencia)
3. El servicio debe coincidir con uno de: consulta, limpieza, empaste,
   extraccion, endodoncia, ortodoncia, cirugia, implantes, estetica,
   odontopediatria, blanqueamiento, revision.
   Si no coincide exactamente, elige el más cercano.
4. El nombre solo se extrae si el usuario se presentó ("soy Juan", "me llamo María").
   También revisa el historial — si el usuario ya dijo su nombre antes, úsalo.
5. Retorna SOLO JSON, sin markdown.

Formato de respuesta:
{
  "service": "limpieza" | null,
  "datetime_iso": "2026-04-10T15:00" | null,
  "patient_name": "Juan Pérez" | null,
  "confidence": 0.95,
  "needs_more_info": true,
  "missing": ["service", "datetime"]
}
"""


async def extract_booking_data(
    message: str,
    context: Dict[str, Any],
    llm,
    history: Optional[List[BaseMessage]] = None,
) -> Dict[str, Any]:
    """Extrae servicio, fecha y nombre del mensaje del usuario.

    FIX: ahora recibe el historial reciente para extraer datos que el usuario
    mencionó en turnos anteriores (ej: nombre dado 2 mensajes atrás).

    Args:
        message: texto del último mensaje del usuario
        context: dict con fecha_hoy, manana_fecha, dia_semana_hoy, missing_fields
        llm: instancia del ChatOpenAI
        history: historial reciente (opcional)

    Returns:
        Dict con los campos extraídos (pueden ser null).
    """
    fecha_hoy = context.get("fecha_hoy", "desconocida")
    manana_fecha = context.get("manana_fecha", "desconocida")
    dia_hoy = context.get("dia_semana_hoy", "desconocido")
    manana_dia = context.get("manana_dia", "desconocido")

    history_block = _format_history_block(history, limit=_HISTORY_WINDOW)

    user_content = (
        f"Hoy es {fecha_hoy} ({dia_hoy}). Mañana es {manana_fecha} ({manana_dia}).\n"
    )
    if history_block:
        user_content += f"\nHistorial de la conversación:\n{history_block}\n"
    user_content += f"\nMensaje actual del usuario: {message}"

    prompt = _build_prompt(_EXTRACT_DATA_SYSTEM, user_content)
    response = await llm.ainvoke(prompt)
    text = _clean_json(response.content)

    try:
        data = json.loads(text)
        logger.info(
            "datos extraídos por LLM",
            extracted={
                k: v
                for k, v in data.items()
                if k not in ("needs_more_info", "missing") and v is not None
            },
        )
        return data
    except json.JSONDecodeError:
        logger.warning("extract_booking_data JSON inválido", text=text[:200])
        return {
            "service": None,
            "datetime_iso": None,
            "patient_name": None,
            "confidence": 0.0,
            "missing": ["service", "datetime", "patient_name"],
        }


# ═══════════════════════════════════════════
# 3. GENERATE RESPUESTA DEYY
# ═══════════════════════════════════════════

_GENERATE_RESPONSE_SYSTEM = """\
Eres Deyy, asistente virtual de recepción de Arcadium Rehabilitación Oral (Ecuador).

REGLAS INQUEBRANTABLES:
1. Habla en español usando "usted" (no "tú", no "vos").
2. MÁXIMO 2 líneas de texto por mensaje.
3. MÁXIMO 2 emojis, y SOLO de este set: 😊 👋 📅 ✅ ❌ 🦷 ⏰ 📞
4. NUNCA anuncies lo que vas a hacer ("Voy a revisar la disponibilidad...").
5. NUNCA digas "Estoy aquí para ayudarle" ni frases robóticas similares.
6. Sé cálida pero profesional.
7. Si hay slots disponibles, muestra máximo 4 los más cercanos.
8. Si falta información, pregunta por UNA sola cosa a la vez.
9. Si se agendó exitosamente, confirma fecha + hora + servicio.
10. Si hay error, sugiere llamar a la clínica: 📞.
11. NUNCA repitas una pregunta que ya hiciste en el historial.
12. Si el usuario ya dio un dato (nombre, servicio, fecha), NO lo pidas de nuevo.

SITUACIÓN ACTUAL:
{context}
"""


async def generate_deyy_response(
    state: Dict[str, Any],
    llm,
    history: Optional[List[BaseMessage]] = None,
) -> str:
    """Genera el texto final que Deyy envía por WhatsApp.

    FIX: ahora incluye el historial de conversación en el prompt para que
    el LLM tenga coherencia entre turnos y no repita preguntas ya hechas.

    Args:
        state: estado actual del grafo (contexto de la cita)
        llm: instancia del LLM
        history: historial de mensajes (opcional, mejora coherencia)

    Returns:
        Texto del mensaje que Deyy enviará.
    """
    # Construir resumen del estado actual
    context_parts = []

    intent = state.get("intent")
    if intent:
        context_parts.append(f"Intención del usuario: {intent}")

    missing = state.get("missing_fields", [])
    if missing:
        context_parts.append(
            f"Datos que faltan: {', '.join(missing)}. Pídelos de a uno."
        )

    patient_name = state.get("patient_name")
    if patient_name:
        context_parts.append(f"Nombre del paciente: {patient_name}")

    selected_service = state.get("selected_service")
    if selected_service:
        context_parts.append(f"Servicio seleccionado: {selected_service}")

    datetime_pref = state.get("datetime_preference")
    if datetime_pref:
        context_parts.append(f"Fecha/hora preferida: {datetime_pref}")

    slots = state.get("available_slots", [])
    if slots:
        readable = _format_slots(slots[:4])
        context_parts.append(f"Slots disponibles: {readable}")

    selected_slot = state.get("selected_slot")
    if selected_slot:
        context_parts.append(f"Usuario eligió slot: {selected_slot}")

    appt_id = state.get("appointment_id")
    if appt_id:
        svc = state.get("selected_service", "")
        slot = state.get("selected_slot") or state.get("datetime_preference", "")
        context_parts.append(
            f"Cita agendada exitosamente: {svc} el {slot}. Confirma al usuario."
        )

    error = state.get("last_error")
    if error:
        context_parts.append(f"Error ocurrido: {error}. Sugiere llamar a la clínica.")

    turns = state.get("conversation_turns", 0)
    if turns >= 8:
        context_parts.append(
            "Ya van muchos mensajes. Considera ofrecer llamar a la clínica."
        )

    # NUEVO: Incluir memorias semánticas (si existen)
    semantic_memories = state.get("semantic_memory_context", "")
    if semantic_memories:
        context_parts.append(f"INFORMACIÓN PREVIA DEL USUARIO:\n{semantic_memories}")

    context_str = (
        "\n".join(context_parts) if context_parts else "Sin contexto específico."
    )

    # FIX: incluir historial reciente para coherencia conversacional
    history_block = _format_history_block(history, limit=_HISTORY_WINDOW)

    user_content = "Genera SOLO el mensaje para el usuario, sin explicaciones extras."
    if history_block:
        user_content = (
            f"Historial de la conversación (para contexto, NO repetir lo ya dicho):\n"
            f"{history_block}\n\n"
            f"{user_content}"
        )

    prompt = _build_prompt(
        _GENERATE_RESPONSE_SYSTEM.format(context=context_str),
        user_content,
    )

    response = await llm.ainvoke(prompt)
    return response.content.strip()


# ═══════════════════════════════════════════
# HELPERS PRIVADOS
# ═══════════════════════════════════════════


def _build_prompt(system: str, user: str) -> list:
    from langchain_core.messages import HumanMessage, SystemMessage

    return [SystemMessage(content=system), HumanMessage(content=user)]


def _clean_json(text: str) -> str:
    """Elimina markdown y code fences de la respuesta del LLM."""
    if isinstance(text, list):
        text = str(text[0]) if text else "{}"
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _format_history_block(
    history: Optional[List[BaseMessage]],
    limit: int = 6,
) -> str:
    """
    Convierte los últimos `limit` mensajes del historial a texto legible
    para incluir en el prompt del LLM.

    Formato:
        Usuario: Hola, quiero una limpieza
        Deyy: ¿Me puede indicar su nombre completo?
        Usuario: Jorge Arias
    """
    if not history:
        return ""

    recent = history[-limit:]
    lines = []
    for msg in recent:
        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
            lines.append(f"Usuario: {msg.content}")
        elif isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai":
            lines.append(f"Deyy: {msg.content}")
    return "\n".join(lines)


def _format_slots(slots: list) -> str:
    """Convierte lista de ISO a texto legible: 'lunes 10:00, martes 14:30'."""
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Guayaquil")
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    readable = []
    for s in slots:
        try:
            dt = datetime.fromisoformat(s) if s else None
            if dt:
                dia = dias[dt.weekday()]
                readable.append(f"{dia} {dt.strftime('%H:%M')}")
        except Exception:
            readable.append(str(s))
    return ", ".join(readable)
