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
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger("langgraph.llm")


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

Responde SOLO con este JSON, sin markdown, sin explicación:
{"intent": "agendar", "confidence": 0.95}
"""


async def extract_intent_llm(
    message: str,
    llm,
) -> tuple[str, float]:
    """Detecta intención vía LLM cuando las keywords no bastaron.

    Returns:
        (intent, confidence)
    """
    prompt = _build_prompt(_EXTRACT_INTENT_SYSTEM, f"Mensaje: {message}")

    response = await llm.ainvoke(prompt)
    text = _clean_json(response.content)

    try:
        data = json.loads(text)
        return data.get("intent", "otro"), float(data.get("confidence", 0.5))
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("extract_intent_llm respondió JSON inválido", text=text[:200], error=str(e))
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
) -> Dict[str, Any]:
    """Extrae servicio, fecha y nombre del mensaje del usuario.

    Args:
        message: texto del usuario
        context: dict con fecha_hoy, manana_fecha, dia_semana_hoy
        llm: instancia del ChatOpenAI (o compatible)

    Returns:
        Dict con los campos extraídos (pueden ser null).
    """
    fecha_hoy = context.get("fecha_hoy", "desconocida")
    manana_fecha = context.get("manana_fecha", "desconocida")
    dia_hoy = context.get("dia_semana_hoy", "desconocido")
    manana_dia = context.get("manana_dia", "desconocido")

    prompt = _build_prompt(
        _EXTRACT_DATA_SYSTEM,
        f"Hoy es {fecha_hoy} ({dia_hoy}). Mañana es {manana_fecha} ({manana_dia}).\n"
        f"Mensaje del usuario: {message}"
    )

    response = await llm.ainvoke(prompt)
    text = _clean_json(response.content)

    try:
        data = json.loads(text)
        return data
    except json.JSONDecodeError:
        logger.warning("extract_booking_data respondió JSON inválido", text=text[:200])
        return {
            "service": None,
            "datetime_iso": None,
            "patient_name": None,
            "confidence": 0.0,
            "missing": ["service", "datetime", "patient_name"],
        }


# ═══════════════════════════════════════════
# 3. GENERATE RESPUESTA DEYYY
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

CONTEXT: {context}
"""


async def generate_deyy_response(
    state: Dict[str, Any],
    llm,
) -> str:
    """Genera el texto final que Deyy envía por WhatsApp.

    Construye un contexto resumido del estado y le pide al LLM
    que genere la respuesta.
    """
    context_parts = []

    intent = state.get("intent")
    if intent:
        context_parts.append(f"Intención del usuario: {intent}")

    missing = state.get("missing_fields", [])
    if missing:
        context_parts.append(f"Datos que faltan: {', '.join(missing)}. Pídelos de a uno.")

    patient_name = state.get("patient_name")
    if patient_name:
        context_parts.append(f"Nombre del paciente: {patient_name}")

    slots = state.get("available_slots", [])
    if slots:
        # Mostrar máximo 4 slots en formato legible
        readable = _format_slots(slots[:4])
        context_parts.append(f"Slots disponibles: {readable}")

    selected_slot = state.get("selected_slot")
    if selected_slot:
        context_parts.append(f"Usuario eligió slot: {selected_slot}")

    appt_id = state.get("appointment_id")
    if appt_id:
        svc = state.get("selected_service", "")
        slot = state.get("selected_slot", "")
        context_parts.append(f"Cita agendada exitosamente: {svc} el {slot}. Confirma al usuario.")

    errors = state.get("last_error")
    if errors:
        context_parts.append(f"Error ocurrido: {errors}. Sugiere llamar a la clínica.")

    turns = state.get("conversation_turns", 0)
    if turns >= 8:
        context_parts.append("Ya van muchos mensajes. Considera ofrecer llamar a la clínica.")

    context_str = "\n".join(context_parts)

    prompt = _build_prompt(
        _GENERATE_RESPONSE_SYSTEM.format(context=context_str),
        "Genera SOLO el mensaje para el usuario, sin explicaciones extras.",
    )

    response = await llm.ainvoke(prompt)
    return response.content.strip()


# ═══════════════════════════════════════════
# HELPERS PRIVADOS
# ═══════════════════════════════════════════

def _build_prompt(system: str, user: str) -> list:
    from langchain_core.messages import SystemMessage, HumanMessage
    return [SystemMessage(content=system), HumanMessage(content=user)]


def _clean_json(text: str) -> str:
    """Elimina markdown y code fences de la respuesta del LLM."""
    if isinstance(text, list):
        text = str(text[0]) if text else "{}"
    # Strip ```json ... ```  or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


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
            readable.append(s)
    return ", ".join(readable)
