"""Script interactivo para probar el agente de citas de Arcadium.

Uso:
    python scripts/test_agent_interactive.py                    # Modo conversacional
    python scripts/test_agent_interactive.py --scenario agendar  # Flujo predefinido
    python scripts/test_agent_interactive.py --scenario cancelar
    python scripts/test_agent_interactive.py --scenario consultar

En modo conversacional, escribe mensajes y presiona Enter para interactuar
con el agente. Escribe 'exit' o 'salir' para terminar.
"""

import argparse
import asyncio
import json
import sys
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, AIMessage

from src.graph import compile_graph
from src.state import create_initial_arcadium_state, VALID_SERVICES
from src.store import InMemoryStore


# ── Colores para terminal ──────────────────────────────────

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


TZ = ZoneInfo("America/Guayaquil")


# ── Realistic Mock LLM ─────────────────────────────────────

class MockLLM:
    """LLM mock que genera respuestas realistas basadas en el contexto.

    Para extract_intent: retorna JSON válido con intent detectado por keywords.
    Para extract_data: extrae servicio, fecha, nombre del texto.
    Para generate_response: genera respuestas en estilo Deyy (pre-definidas por contexto).
    """

    def __init__(self):
        self.call_count = 0

    async def ainvoke(self, messages, **kwargs):
        self.call_count += 1

        system_text = ""
        human_text = ""
        for m in messages:
            content = m.content if isinstance(m.content, str) else str(m.content)
            if getattr(m, "type", None) == "system":
                system_text = content.lower()
            elif getattr(m, "type", None) == "human":
                human_text = content

        # ── Extract intent classification ──
        if "clasificador de intenciones" in system_text:
            intent = self._detect_intent(human_text)
            confidence = 0.92 if intent != "otro" else 0.5
            return self._json_response({"intent": intent, "confidence": confidence})

        # ── Extract booking data ──
        if "extractor de datos" in system_text:
            data = self._extract_data(human_text)
            return self._json_response(data)

        # ── Generate Deyy response ──
        if "deyy" in system_text:
            response_text = self._generate_response(system_text)
            result = MagicMock()
            result.content = response_text
            result.type = "ai"
            return result

        # Fallback
        result = MagicMock()
        result.content = "Entendido."
        result.type = "ai"
        return result

    def _detect_intent(self, text: str) -> str:
        text = text.lower().strip()

        agenda_kw = ["agendar", "cita", "reservar", "turno", "limpieza",
                     "consulta", "revisar", "me duele", "dolor", "quiero ir",
                     "necesito", "revision", "blanqueamiento"]
        cancelar_kw = ["cancelar", "cancelo", "cancela", "no puedo",
                       "anular", "anulo", "mejor no", "olvidalo", "no voy"]
        reagendar_kw = ["reagendar", "cambiar", "reprogramar", "otra fecha",
                        "otro dia", "otro día", "otro horario", "mover"]
        consultar_kw = ["disponible", "hay espacio", "hay lugar", "horarios",
                        "horario", "cuando puedo", "cuándo puedo",
                        "mis citas", "proxima cita", "próxima cita"]

        scores = {"agendar": 0, "cancelar": 0, "reagendar": 0, "consultar": 0}
        for kw in agenda_kw:
            if kw in text:
                scores["agendar"] += 1
        for kw in cancelar_kw:
            if kw in text:
                scores["cancelar"] += 1
        for kw in reagendar_kw:
            if kw in text:
                scores["reagendar"] += 1
        for kw in consultar_kw:
            if kw in text:
                scores["consultar"] += 1

        best = max(scores, key=scores.get)
        # "consulta como servicio" != "consultar como intent"
        # Si dice "cita de consulta" → agendar, no consultar
        if "cita de consulta" in text and scores["agendar"] > 0:
            best = "agendar"
        return best if scores[best] > 0 else "otro"

    def _extract_data(self, text: str) -> dict:
        text_lower = text.lower().strip()
        now = datetime.now(TZ)

        result = {
            "service": None,
            "datetime_iso": None,
            "patient_name": None,
            "confidence": 0.8,
            "needs_more_info": False,
            "missing": [],
        }

        # Extract service
        for svc in VALID_SERVICES:
            if svc in text_lower or svc.replace("ó", "o") in text_lower:
                result["service"] = svc
                break

        # Extract name: "me llamo X", "soy X"
        name_patterns = [
            r"(?:me llamo|mi nombre es|soy)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)",
        ]
        for pat in name_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip().rstrip(".")
                if len(name) > 1 and name.lower() not in ("un", "una", "el", "la"):
                    result["patient_name"] = name
                    break

        # Extract date: "mañana", "pasado mañana", "el lunes", "el viernes"
        dias_map = {
            "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
            "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5,
            "domingo": 6,
        }
        if "mañana" in text_lower or "manana" in text_lower:
            tomorrow = now + timedelta(days=1)
            # Check for time mention
            time_str = self._extract_time(text)
            hour, minute = (10, 0) if time_str is None else time_str
            dt = tomorrow.replace(hour=hour, minute=minute, second=0)
            result["datetime_iso"] = dt.replace(tzinfo=TZ).isoformat()
        elif "pasado manana" in text_lower or "pasado mañana" in text_lower:
            day_after = now + timedelta(days=2)
            time_str = self._extract_time(text)
            hour, minute = (10, 0) if time_str is None else time_str
            dt = day_after.replace(hour=hour, minute=minute, second=0)
            result["datetime_iso"] = dt.replace(tzinfo=TZ).isoformat()
        else:
            for dia_name, dia_num in dias_map.items():
                if dia_name in text_lower:
                    days_ahead = (dia_num - now.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    target = now + timedelta(days=days_ahead)
                    time_str = self._extract_time(text)
                    hour, minute = (10, 0) if time_str is None else time_str
                    dt = target.replace(hour=hour, minute=minute, second=0)
                    result["datetime_iso"] = dt.replace(tzinfo=TZ).isoformat()
                    break

        # If no service or datetime found, mark as needing more info
        missing = []
        if not result["service"]:
            missing.append("service")
        if not result["datetime_iso"]:
            missing.append("datetime")
        if missing:
            result["needs_more_info"] = bool(missing)
            result["missing"] = missing

        return result

    def _extract_time(self, text: str) -> Optional[tuple]:
        m = re.search(r"(\d{1,2}):?(\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?", text, re.IGNORECASE)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            if "pm" in text.lower() and hour < 12:
                hour += 12
            if "am" in text.lower() and hour == 12:
                hour = 0
            return (hour, minute)
        return None

    def _generate_response(self, system_context: str) -> str:
        """Genera respuestas realistas de Deyy basadas en el estado."""
        context_lower = system_context.lower()

        # Missing patient_name
        if "datos que faltan" in context_lower and "patient_name" in context_lower:
            if "servicio" not in context_lower:
                return "¡Hola! 😊 ¿Para qué servicio necesita una cita? Tenemos: limpieza, consulta, blanqueamiento, entre otros."
            return "Perfecto. ¿A nombre de quién desea agendar la cita?"

        # Missing datetime_preference
        if "datos que faltan" in context_lower and "datetime_preference" in context_lower:
            manana = (datetime.now(TZ) + timedelta(days=1)).strftime("%A %d")
            return f"¿Para qué fecha y hora le gustaría? Por ejemplo: mañana, el lunes, etc. 😊"

        # Missing service
        if "datos que faltan" in context_lower and "selected_service" in context_lower:
            return f"¿Para qué servicio necesita la cita? 😊 Tenemos:\n" + \
                   ", ".join(list(VALID_SERVICES.keys())[:6]) + ", etc."

        # Available slots
        if "slots disponibles" in context_lower:
            # Extract from context
            slots_match = re.search(r"slots disponibles: (.+)", context_lower)
            if slots_match:
                slots = slots_match.group(1)
                return f"Tengo estos horarios disponibles:\n{slots}\n\n¿Cuál le funciona mejor? 😊"

        # Appointment confirmed / booked
        if "cita agendada exitosamente" in context_lower:
            confirm_match = re.search(r"cita agendada exitosamente: (.+)", context_lower)
            if confirm_match:
                details = confirm_match.group(1)
                return f"¡Listo! ✅ He agendado su cita: {details}. Le llegará un recordatorio. 📅"

        # Appointment cancelled
        if "cancelar" in context_lower and "cita" in context_lower:
            return "Su cita ha sido cancelada correctamente. Si necesita una nueva fecha, estaré encantada de ayudarle. 😊"

        # Error
        if "error ocurrido" in context_lower:
            return "Lo siento, hubo un inconveniente técnico. Por favor intente nuevamente o llame a la clínica. 📞"

        # Too many turns
        if "muchos mensajes" in context_lower:
            return "Veo que ya hemos intercambiado varios mensajes. Le sugiero llamar directamente a la clínica para agendarle personalmente. 📞"

        # Greeting / general
        if "saludo" in context_lower or context_lower.strip().startswith("hola"):
            return "¡Hola! 😊 ¿En qué puedo ayudarle hoy? Puedo agendar, cancelar o consultar citas."

        # Default: ask for missing info
        if "datos que faltan" in context_lower:
            return "¿Podría darme más información para continuar? 😊"

        return "¿En qué más puedo ayudarle? 😊"

    def _json_response(self, data: dict) -> MagicMock:
        result = MagicMock()
        result.content = json.dumps(data, ensure_ascii=False)
        result.type = "ai"
        return result


# ── Mock services ──────────────────────────────────────────


class MockCalendarService:
    """Mock de Google Calendar con horarios realistas."""

    async def get_available_slots(self, date, duration_minutes=30):
        if isinstance(date, str):
            date = datetime.fromisoformat(date).date()

        if date.weekday() >= 5:
            from src.state import _next_monday
            date = _next_monday(datetime.combine(date, datetime.min.time())).date()

        slots = []
        current = datetime.combine(date, datetime(time=9, minute=0))
        end_of_day = datetime.combine(date, datetime(time=17, minute=30))

        # "Ocupar" algunos slots para realismo
        import hashlib
        seed = int(hashlib.md5(str(date).encode()).hexdigest()[:4], 16)
        busy_slots = set()
        for i in range(3):
            idx = (seed + i * 7) % 16
            busy_slots.add(idx)

        idx = 0
        while current <= end_of_day and len(slots) < 5:
            if idx not in busy_slots:
                local_dt = current.replace(tzinfo=TZ)
                slots.append(local_dt.isoformat())
            current += timedelta(minutes=30)
            idx += 1

        print(f"  {GRAY}[Calendar: {len(slots)} slots disponibles]{RESET}")
        return slots

    async def create_event(self, start, end, title, description):
        print(f"  {GREEN}[Calendar: Evento '{title}' creado]{RESET}")
        return ("mock_event_123", "https://calendar.google.com/event?eid=mock")

    async def delete_event(self, event_id):
        print(f"  {YELLOW}[Calendar: Eliminado evento {event_id}]{RESET}")
        return True


class MockDBService:
    """Mock del servicio de DB."""

    async def create_appointment(self, session=None, **kwargs):
        print(f"  {GREEN}[DB: Cita creada - {kwargs.get('service_type', 'unknown')}]{RESET}")
        appt_mock = type("Mock", (), {"id": "appt_mock_001"})()
        return (True, "Created", appt_mock)

    async def cancel_appointment(self, session=None, appointment_id=None):
        print(f"  {YELLOW}[DB: Cita cancelada]{RESET}")
        return (True, "Cancelled")


# ── Inicialización ─────────────────────────────────────────


async def init_agent():
    """Inicializa el grafo con LLM mock y servicios mock."""
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Arcadium Automation — Prueba del Agente de Citas{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")
    print(f"{GRAY}Inicializando componentes (MockLLM)...{RESET}")

    llm = MockLLM()
    store = InMemoryStore()
    calendar = MockCalendarService()
    db = MockDBService()

    print(f"  {GREEN}LLM: MockLLM (sin API externa){RESET}")
    print(f"  {GREEN}Store: InMemoryStore{RESET}")
    print(f"  {GREEN}Calendar: MockCalendarService{RESET}")
    print(f"  {GREEN}DB: MockDBService{RESET}")

    compiled = compile_graph(
        llm=llm,
        store=store,
        calendar_service=calendar,
        db_service=db,
    )

    print(f"\n  {GREEN}{BOLD}Agente inicializado correctamente{RESET}\n")
    return compiled, store, llm


# ── Conversación interactiva ───────────────────────────────

phone_number = "+593999999999"
stored_messages = []


async def send_message(compiled, message: str, state_history: dict) -> tuple:
    """Envía un mensaje al agente y retorna la respuesta."""
    print(f"\n  {GRAY}[Enviando al agente...]{RESET}")

    state = create_initial_arcadium_state(phone_number=phone_number)

    if state_history:
        for k, v in state_history.items():
            if k == "messages":
                continue
            if v is not None:
                state[k] = v

    state["messages"] = list(stored_messages) + [HumanMessage(content=message)]

    try:
        result = await compiled.ainvoke(state)

        ai_text = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                ai_text = msg.content
                break

        return ai_text, result

    except Exception as e:
        return f"{RED}[Error del agente: {e}]{RESET}", {}


async def interactive_mode(compiled, store, llm):
    """Modo conversacional interactivo."""
    print(f"{BOLD}{BLUE}┌{'─'*56}┐{RESET}")
    print(f"{BOLD}{BLUE}│{RESET} {GREEN}Modo conversacional — escribe mensajes{RESET}            {BOLD}{BLUE}│{RESET}")
    print(f"{BOLD}{BLUE}│{RESET} {GRAY}Escribe 'exit' para salir, '.' para ver estado{RESET}      {BOLD}{BLUE}│{RESET}")
    print(f"{BOLD}{BLUE}└{'─'*56}┘{RESET}\n")

    state_history: Dict[str, Any] = {}

    while True:
        try:
            user_input = input(f"  {BOLD}{BLUE}Tú{RESET}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{GRAY}Sesión terminada.{RESET}")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "salir", "quit"):
            print(f"{GRAY}Sesión terminada.{RESET}")
            break
        if user_input == ".":
            _show_state(state_history)
            continue

        response, state_history = await send_message(compiled, user_input, state_history)

        stored_messages.extend([
            HumanMessage(content=user_input),
            AIMessage(content=response),
        ])

        _print_whatsapp_msg(response)
        _print_state_summary(state_history)


# ── Scenario runner ───────────────────────────────────────


async def run_scenario(compiled, messages: list):
    """Ejecuta una conversación predefinida multi-turno."""
    state_history: Dict[str, Any] = {}
    local_stored = []

    for i, user_msg in enumerate(messages):
        print(f"\n{BOLD}{BLUE}Tú{RESET}> {user_msg}")

        state = create_initial_arcadium_state(phone_number=phone_number)
        if state_history:
            for k, v in state_history.items():
                if k == "messages":
                    continue
                if v is not None:
                    state[k] = v
        state["messages"] = list(local_stored) + [HumanMessage(content=user_msg)]

        result = await compiled.ainvoke(state)

        ai_text = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                ai_text = msg.content
                break

        local_stored.extend([
            HumanMessage(content=user_msg),
            AIMessage(content=ai_text),
        ])
        state_history = result

        print(f"  {BOLD}{GREEN}Deyy{RESET}> {ai_text}")
        print(f"  {GRAY}├─ intent={result.get('intent')}  turns={result.get('conversation_turns')}  step={result.get('current_step', '?')}{RESET}")
        if result.get("available_slots"):
            slots_preview = " | ".join(s[:16] for s in result["available_slots"][:3])
            print(f"  {GRAY}├─ slots: [{slots_preview}]{RESET}")
        if result.get("missing_fields"):
            print(f"  {GRAY}├─ missing: {result['missing_fields']}{RESET}")
        if result.get("confirmation_result"):
            print(f"  {GRAY}├─ confirmation: {result['confirmation_result']}{RESET}")
        if result.get("appointment_id"):
            print(f"  {GREEN}└─ appointment: {result['appointment_id']}{RESET}")

        if i < len(messages) - 1:
            await asyncio.sleep(0.3)

    print(f"\n{BOLD}{CYAN}{'─'*56}{RESET}")
    print(f"{BOLD}{CYAN}  Estado final:{RESET}")
    print(f"  {GRAY}intent={state_history.get('intent')}  turns={state_history.get('conversation_turns')}  step={state_history.get('current_step', '?')}{RESET}")
    if state_history.get("appointment_id"):
        print(f"  {GREEN}appointment_id: {state_history['appointment_id']}{RESET}")
    if state_history.get("google_event_link"):
        print(f"  {GREEN}google_event_link: {state_history['google_event_link']}{RESET}")
    print()


# ── Helpers de impresión ───────────────────────────────────


def _print_whatsapp_msg(text: str):
    lines = text.split('\n')
    for i, line in enumerate(lines):
        prefix = f"  {BOLD}{GREEN}Deyy{RESET}> " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()


def _print_state_summary(state: dict):
    if not state:
        return
    parts = []
    if state.get("intent"):
        parts.append(f"intent={state['intent']}")
    if state.get("patient_name"):
        parts.append(f"paciente={state['patient_name']}")
    if state.get("selected_service"):
        parts.append(f"servicio={state['selected_service']}")
    if state.get("datetime_preference"):
        dt_str = state['datetime_preference'][:16]
        parts.append(f"fecha={dt_str}")
    if state.get("appointment_id"):
        parts.append(f"{GREEN}cita={state['appointment_id']}{RESET}")
    if state.get("should_escalate"):
        parts.append(f"{RED}ESCALAR{RESET}")

    if parts:
        print(f"  {GRAY}[Estado: {' | '.join(parts)}]{RESET}")


def _show_state(state: dict):
    print(f"\n{GRAY}{'─'*56}{RESET}")
    for k, v in state.items():
        if k == "messages":
            continue
        if v is not None and v != [] and v != 0:
            print(f"  {GRAY}{k}: {repr(v) if isinstance(v, str) and len(v) < 80 else v}{RESET}")
    print(f"{GRAY}{'─'*56}{RESET}\n")


# ── Escenarios predefinidos ────────────────────────────────

SCENARIOS = {
    "agendar": [
        "Hola, quiero agendar una limpieza para mañana",
        "a las 10:00 me funciona",
        "sí, confirmo esa hora",
    ],
    "cancelar": [
        "Quiero cancelar mi cita",
    ],
    "consultar": [
        "Hola, qué horarios hay disponibles para mañana?",
    ],
    "reagendar": [
        "Necesito cambiar la fecha de mi cita",
    ],
    "greeting": [
        "hola",
        "quiero agendar una consulta para el viernes",
    ],
    "full_booking": [
        "Hola, necesito agendar una cita de blanqueamiento para el lunes",
        "Me llamo Ana María Torres",
        "a las 14:00 estaría bien",
        "sí, confirmo esa cita",
    ],
}


# ── Main ───────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Prueba interactiva del agente de citas")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        help="Ejecuta un escenario predefinido",
    )
    args = parser.parse_args()

    try:
        compiled, store, llm = await init_agent()
    except Exception as e:
        print(f"\n{RED}Error inicializando agente: {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if args.scenario:
        print(f"\n{BOLD}{YELLOW}Escenario: {args.scenario}{RESET}\n")
        print(f"{BOLD}{BLUE}┌{'─'*56}┐{RESET}")
        print(f"{BOLD}{BLUE}│{RESET} {GRAY}Ejecutando escenario predefinido{RESET}              {BOLD}{BLUE}│{RESET}")
        print(f"{BOLD}{BLUE}└{'─'*56}┘{RESET}")
        await run_scenario(compiled, SCENARIOS[args.scenario])
    else:
        await interactive_mode(compiled, store, llm)


if __name__ == "__main__":
    asyncio.run(main())
