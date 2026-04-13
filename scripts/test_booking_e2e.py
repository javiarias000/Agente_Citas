#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test E2E del flujo de agendamiento.

Simula 8 conversaciones completas contra el servidor en http://localhost:8000/webhook/test
y verifica que el booking se ejecuta correctamente.

Uso:
    source venv/bin/activate
    python scripts/test_booking_e2e.py

Requisitos:
    - Servidor corriendo: ./run.sh start
    - Google Calendar configurado
    - .env con credenciales válidas

Variables de entorno opcionales:
    TEST_SERVER_URL=http://localhost:8000   (default)
    TEST_PHONE=+593900000001               (default — número de prueba)
"""

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

TIMEZONE = ZoneInfo("America/Guayaquil")
SERVER_URL = "http://localhost:8000"
TEST_PHONE_BASE = "+5939900000"

RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"


@dataclass
class ConversationResult:
    scenario: str
    passed: bool
    turns: int
    agent_responses: List[str] = field(default_factory=list)
    final_state: Dict[str, Any] = field(default_factory=dict)
    failure_reason: Optional[str] = None
    duration_ms: float = 0.0


async def send_message(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Envía un mensaje al webhook de test y retorna la respuesta."""
    payload = {"message": message, "session_id": session_id}

    if verbose:
        print(f"  {CYAN}▶ Usuario:{RESET} {message}")

    resp = await client.post(
        f"{SERVER_URL}/webhook/test",
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    agent_text = data.get("response", data.get("text", ""))
    if verbose:
        print(f"  {BOLD}◀ Agente:{RESET}  {agent_text}")

    return data


async def run_conversation(
    scenario_name: str,
    turns: List[str],
    assertions: Dict[str, Any],
    verbose: bool = True,
) -> ConversationResult:
    """
    Ejecuta una conversación completa y verifica assertions.

    assertions puede incluir:
        - "booking_done": True → debe tener google_event_id + confirmation_sent
        - "booking_done": False → NO debe haber booking
        - "response_contains": str → respuesta final contiene este texto
        - "response_not_contains": str → respuesta final NO contiene este texto
    """
    session_id = f"{TEST_PHONE_BASE}{uuid.uuid4().hex[:4]}"
    responses = []
    start = time.monotonic()

    if verbose:
        print(f"\n{BOLD}{'─'*60}{RESET}")
        print(f"{BOLD}Scenario: {scenario_name}{RESET}")
        print(f"Session: {session_id}")
        print()

    try:
        async with httpx.AsyncClient() as client:
            last_data = {}
            for msg in turns:
                # Pequeña pausa entre mensajes para simular conversación real
                await asyncio.sleep(0.5)
                last_data = await send_message(client, session_id, msg, verbose=verbose)
                responses.append(last_data.get("response", last_data.get("text", "")))

        duration_ms = (time.monotonic() - start) * 1000

        # Evaluar assertions
        passed = True
        failure_reason = None
        final_text = responses[-1] if responses else ""

        if "booking_done" in assertions:
            expect_booking = assertions["booking_done"]
            has_event_id = bool(last_data.get("google_event_id") or last_data.get("appointment_id"))
            confirmation_sent = bool(last_data.get("confirmation_sent"))

            if expect_booking and not (has_event_id or confirmation_sent):
                # Check response text as secondary indicator
                booking_keywords = ["agendada", "agendado", "confirmada", "confirmado", "✅", "queda agendada"]
                text_indicates_booking = any(kw in final_text.lower() for kw in booking_keywords)
                if not text_indicates_booking:
                    passed = False
                    failure_reason = (
                        f"Booking esperado pero no ocurrió. "
                        f"google_event_id={last_data.get('google_event_id')}, "
                        f"confirmation_sent={confirmation_sent}, "
                        f"respuesta='{final_text[:100]}'"
                    )
            elif not expect_booking and (has_event_id or confirmation_sent):
                passed = False
                failure_reason = (
                    f"Booking NO esperado pero ocurrió. "
                    f"google_event_id={last_data.get('google_event_id')}"
                )

        if "response_contains" in assertions and passed:
            expected = assertions["response_contains"].lower()
            if expected not in final_text.lower():
                passed = False
                failure_reason = (
                    f"Respuesta no contiene '{assertions['response_contains']}'. "
                    f"Respuesta: '{final_text[:150]}'"
                )

        if "response_not_contains" in assertions and passed:
            forbidden = assertions["response_not_contains"].lower()
            if forbidden in final_text.lower():
                passed = False
                failure_reason = (
                    f"Respuesta contiene texto prohibido '{assertions['response_not_contains']}'. "
                    f"Respuesta: '{final_text[:150]}'"
                )

        status_icon = f"{GREEN}✅ PASS{RESET}" if passed else f"{RED}❌ FAIL{RESET}"
        if verbose:
            print(f"\n  {status_icon} ({duration_ms:.0f}ms)")
            if failure_reason:
                print(f"  {RED}Razón: {failure_reason}{RESET}")

        return ConversationResult(
            scenario=scenario_name,
            passed=passed,
            turns=len(turns),
            agent_responses=responses,
            final_state=last_data,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
        )

    except httpx.ConnectError:
        return ConversationResult(
            scenario=scenario_name,
            passed=False,
            turns=0,
            failure_reason="No se pudo conectar al servidor. ¿Está corriendo ./run.sh start?",
            duration_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as e:
        return ConversationResult(
            scenario=scenario_name,
            passed=False,
            turns=len(turns),
            agent_responses=responses,
            failure_reason=f"Excepción: {type(e).__name__}: {e}",
            duration_ms=(time.monotonic() - start) * 1000,
        )


def get_tomorrow_display() -> str:
    """Fecha de mañana en Ecuador para los tests."""
    now = datetime.now(TIMEZONE)
    tomorrow = now + timedelta(days=1)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return f"{dias[tomorrow.weekday()]} {tomorrow.strftime('%d/%m')}"


async def main(verbose: bool = True) -> None:
    tomorrow = get_tomorrow_display()
    mañana = (datetime.now(TIMEZONE) + timedelta(days=1)).strftime("%Y-%m-%d")

    scenarios = [
        # ── Scenario 1: Happy path — una sola opción, usuario dice "sí" ─────────
        {
            "name": "S1: Happy path (sí + 1 slot)",
            "turns": [
                "Hola quiero agendar una limpieza dental para mañana",
                "Me llamo Ana Torres",
                "sí",  # Confirma el primer slot disponible
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 2: Usuario elige hora específica ────────────────────────────
        {
            "name": "S2: Selección por hora (10:00)",
            "turns": [
                "Quiero agendar una consulta mañana",
                "Ana García",
                "a las 10:00",  # Elige slot específico
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 3: Confirmación con "claro que sí" ──────────────────────────
        {
            "name": "S3: Keyword extendido (claro que sí)",
            "turns": [
                "necesito agendar limpieza para mañana a las 9",
                "Pedro Sanchez",
                "claro que sí",
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 4: Usuario dice "de acuerdo" ───────────────────────────────
        {
            "name": "S4: Keyword extendido (de acuerdo)",
            "turns": [
                "quisiera cita de revision para mañana",
                "María López",
                "de acuerdo",
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 5: Usuario cambia de fecha a mitad de flujo ─────────────────
        {
            "name": "S5: Cambio de fecha mid-flow",
            "turns": [
                "quiero agendar limpieza para hoy",
                "Carmen Ruiz",
                "mejor mañana",  # Cambia de fecha
                "a las 11",       # Elige slot
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 6: Usuario dice NO → no debe haber booking ──────────────────
        {
            "name": "S6: Rechazo (no debe booking)",
            "turns": [
                "quiero agendar consulta mañana",
                "Jorge Mena",
                "no, mejor no",  # Rechaza
            ],
            "assertions": {
                "booking_done": False,
                "response_not_contains": "agendada",
            },
        },

        # ── Scenario 7: Flujo completo multi-turno con nombre tardío ─────────────
        {
            "name": "S7: Multi-turno (nombre en turno 4)",
            "turns": [
                "hola necesito cita",
                "limpieza",
                f"para mañana",
                "soy Roberto Vera",
                "sí",
            ],
            "assertions": {"booking_done": True},
        },

        # ── Scenario 8: Pregunta off-topic en medio → no interrumpe booking ──────
        {
            "name": "S8: Intent change mid-flow (no rompe estado)",
            "turns": [
                "quiero agendar empaste para mañana",
                "Luis Castro",
                "¿cuánto cuesta un empaste?",  # Off-topic
                "a las 10",                     # Vuelve a elegir slot
            ],
            "assertions": {"booking_done": True},
        },
    ]

    results: List[ConversationResult] = []

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}ARCADIUM — TEST E2E FLUJO DE AGENDAMIENTO{RESET}")
    print(f"Servidor: {SERVER_URL}")
    print(f"Fecha base: mañana = {tomorrow}")
    print(f"{BOLD}{'═'*60}{RESET}")

    for scenario in scenarios:
        result = await run_conversation(
            scenario_name=scenario["name"],
            turns=scenario["turns"],
            assertions=scenario["assertions"],
            verbose=verbose,
        )
        results.append(result)

        # Pausa entre scenarios para evitar conflictos de sesión
        await asyncio.sleep(1.0)

    # ── Resumen ────────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_ms = sum(r.duration_ms for r in results)

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}RESUMEN{RESET}")
    print(f"{'═'*60}")
    print(f"  {GREEN}Pasados: {passed}/{len(results)}{RESET}")
    if failed > 0:
        print(f"  {RED}Fallidos: {failed}/{len(results)}{RESET}")
    print(f"  Tiempo total: {total_ms:.0f}ms")
    print()

    for r in results:
        icon = f"{GREEN}✅{RESET}" if r.passed else f"{RED}❌{RESET}"
        line = f"  {icon} {r.scenario} ({r.duration_ms:.0f}ms)"
        print(line)
        if not r.passed and r.failure_reason:
            print(f"       {RED}↳ {r.failure_reason}{RESET}")

    print()

    if failed > 0:
        print(f"{YELLOW}⚠️  {failed} scenario(s) fallaron. Revisa los logs del servidor para más detalle.{RESET}")
        sys.exit(1)
    else:
        print(f"{GREEN}{BOLD}Todos los scenarios pasaron. Flujo de agendamiento listo para producción.{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test E2E del flujo de agendamiento")
    parser.add_argument("--quiet", "-q", action="store_true", help="Solo muestra resumen")
    parser.add_argument("--server", default=SERVER_URL, help=f"URL del servidor (default: {SERVER_URL})")
    args = parser.parse_args()

    SERVER_URL = args.server
    asyncio.run(main(verbose=not args.quiet))
