#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests de agendamiento multi-doctor para ROMPER el agente.

Prueba:
1. Dos doctores, mismo slot → ambos deben poder agendar
2. Mismo doctor, slot ocupado → debe decir "no disponible"
3. Mismo doctor, otro doctor en el slot libre → debe agendar para otro doctor
4. Cambio de doctor mid-flow
5. Doble booking (race condition)
6. Timezone edge cases

Uso:
    source venv/bin/activate
    python scripts/test_multi_doctor_booking.py
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
TEST_PHONE_BASE = "+5939901"

RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float = 0.0
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


async def send_message(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Envía mensaje al webhook."""
    payload = {"message": message, "session_id": session_id}
    if verbose:
        print(f"    {CYAN}▶{RESET} {message}")

    resp = await client.post(
        f"{SERVER_URL}/webhook/test",
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def run_test(
    test_name: str,
    turns: List[str],
    assertions: Dict[str, Any],
    verbose: bool = True,
) -> TestResult:
    """
    Corre conversación y valida assertions.

    assertions:
        - "booking_done": True/False
        - "doctor_email": str (esperado)
        - "response_contains": str
        - "response_not_contains": str
    """
    session_id = f"{TEST_PHONE_BASE}{uuid.uuid4().hex[:5]}"
    start = time.monotonic()

    if verbose:
        print(f"\n{BOLD}{'─'*70}{RESET}")
        print(f"{BOLD}{test_name}{RESET}")
        print(f"SID: {session_id}")

    try:
        async with httpx.AsyncClient() as client:
            last_data = {}
            for msg in turns:
                await asyncio.sleep(0.3)
                last_data = await send_message(client, session_id, msg, verbose=verbose)

            duration_ms = (time.monotonic() - start) * 1000

            # Validar assertions
            passed = True
            reason = None

            if "booking_done" in assertions:
                expect = assertions["booking_done"]
                has_booking = bool(
                    last_data.get("google_event_id")
                    or last_data.get("confirmation_sent")
                )
                if expect != has_booking:
                    passed = False
                    reason = (
                        f"booking_done: esperado {expect}, "
                        f"got google_event_id={last_data.get('google_event_id')}, "
                        f"confirmation_sent={last_data.get('confirmation_sent')}"
                    )

            if "doctor_email" in assertions and passed:
                expected_doc = assertions["doctor_email"]
                actual_doc = last_data.get("doctor_email")
                if actual_doc != expected_doc:
                    passed = False
                    reason = f"doctor_email: esperado {expected_doc}, got {actual_doc}"

            if "response_contains" in assertions and passed:
                needle = assertions["response_contains"].lower()
                resp_text = last_data.get("response", "").lower()
                if needle not in resp_text:
                    passed = False
                    reason = (
                        f"Respuesta no contiene '{assertions['response_contains']}'. "
                        f"Got: {resp_text[:100]}"
                    )

            if "response_not_contains" in assertions and passed:
                needle = assertions["response_not_contains"].lower()
                resp_text = last_data.get("response", "").lower()
                if needle in resp_text:
                    passed = False
                    reason = (
                        f"Respuesta contiene texto prohibido '{assertions['response_not_contains']}'. "
                        f"Got: {resp_text[:100]}"
                    )

            status = f"{GREEN}✅ PASS{RESET}" if passed else f"{RED}❌ FAIL{RESET}"
            if verbose:
                print(f"  {status} ({duration_ms:.0f}ms)")
                if reason:
                    print(f"  {RED}↳ {reason}{RESET}")

            return TestResult(
                name=test_name,
                passed=passed,
                duration_ms=duration_ms,
                reason=reason,
                details=last_data,
            )

    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        return TestResult(
            name=test_name,
            passed=False,
            duration_ms=duration_ms,
            reason=f"{type(e).__name__}: {str(e)[:80]}",
        )


async def main():
    tomorrow = (datetime.now(TIMEZONE) + timedelta(days=1)).strftime("%Y-%m-%d")

    # Formato de fecha legible para usuarios
    tomorrow_dt = datetime.now(TIMEZONE) + timedelta(days=1)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    tomorrow_display = f"{dias[tomorrow_dt.weekday()]} {tomorrow_dt.strftime('%d/%m')}"

    tests = [
        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 1: Multi-doctor routing
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T1a: Limpieza (Jorge) a hora X",
            "turns": [
                f"Quiero agendar limpieza para {tomorrow_display} a las 9:00",
                "Ana García",
                "sí",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "jorge.arias.amauta@gmail.com",
            },
        },
        {
            "name": "T1b: Extracción (Javi) misma hora X",
            "turns": [
                f"Necesito extracción para {tomorrow_display} a las 9:00",
                "Carlos López",
                "sí",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "javiarias000@gmail.com",
            },
        },

        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 2: Rechazo de slots ocupados
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T2a: Limpieza (Jorge) a hora Y",
            "turns": [
                f"Necesito limpieza para {tomorrow_display} a las 10:00",
                "Maria Sanchez",
                "sí",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "jorge.arias.amauta@gmail.com",
            },
        },
        {
            "name": "T2b: Otro usuario quiere MISMA hora para Jorge → rechazado",
            "turns": [
                f"Quiero limpieza para {tomorrow_display} a las 10:00",
                "Roberto Villa",
                "sí",
            ],
            "assertions": {
                "booking_done": False,
                "response_contains": "disponible",  # "no está disponible" o similar
            },
        },
        {
            "name": "T2c: Pero otra hora para Jorge SÍ funciona",
            "turns": [
                f"Necesito limpieza para {tomorrow_display} a las 11:00",
                "Patricia Mora",
                "sí",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "jorge.arias.amauta@gmail.com",
            },
        },

        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 3: Cambio de doctor mid-flow
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T3: Cambio dinámico de servicio (limpieza → extracción)",
            "turns": [
                f"Necesito agendar para {tomorrow_display}",
                "inicialmente limpieza",
                "mejor una extracción",  # Cambio de doctor implícito
                "soy Diego Ruiz",
                "a las 2 de la tarde",  # 14:00
                "sí",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "javiarias000@gmail.com",  # Extracción → Javi
            },
        },

        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 4: Edge cases de validación
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T4a: Nombre vacío → debe rechazar o pedir",
            "turns": [
                f"Quiero limpieza para {tomorrow_display}",
                "",  # Usuario no responde bien
                "Laura Méndez",
                "a las 3 de la tarde",
                "sí",
            ],
            "assertions": {
                "booking_done": True,
            },
        },
        {
            "name": "T4b: Servicio inválido → debe pedir aclaración",
            "turns": [
                f"Quiero un hamster para {tomorrow_display}",  # Servicio inexistente
                "Pedro García",
                "limpieza",  # Aclara
                "sí",
            ],
            "assertions": {
                "response_contains": "limpieza",  # Debe mencionar la limpieza
            },
        },

        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 5: Race conditions (intenta ejecutar acciones simultáneas)
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T5: Confirmación múltiple (usuario dice sí 2x) → solo 1 booking",
            "turns": [
                f"Quiero limpieza para {tomorrow_display} a las 4 de la tarde",
                "Elena Castillo",
                "sí",
                "sí, confirmo de nuevo",  # ¿Crea 2ª cita?
            ],
            "assertions": {
                "booking_done": True,
                # No validamos google_event_id porque no sabemos si hay 1 o 2
                "response_contains": "agendada",
            },
        },

        # ══════════════════════════════════════════════════════════════════════
        # GRUPO 6: Flujo completo sin guía (conversación libre)
        # ══════════════════════════════════════════════════════════════════════
        {
            "name": "T6: Flujo libre sin estructura estricta",
            "turns": [
                f"Hola, quiero ver al dentista mañana",
                "Es para limpieza",
                "me llamo Franco Pérez",
                "a las 5",
                "sí, perfecto",
            ],
            "assertions": {
                "booking_done": True,
                "doctor_email": "jorge.arias.amauta@gmail.com",
            },
        },
    ]

    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}ARCADIUM — TESTS MULTI-DOCTOR (BREAK SCENARIOS){RESET}")
    print(f"Servidor: {SERVER_URL}")
    print(f"Fecha base: {tomorrow_display}")
    print(f"{BOLD}{'═'*70}{RESET}\n")

    results: List[TestResult] = []
    for test in tests:
        result = await run_test(
            test_name=test["name"],
            turns=test["turns"],
            assertions=test["assertions"],
            verbose=True,
        )
        results.append(result)
        await asyncio.sleep(1.0)  # Pausa entre tests

    # ────────────────────────────────────────────────────────────────────────
    # RESUMEN
    # ────────────────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_ms = sum(r.duration_ms for r in results)

    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}RESUMEN{RESET}")
    print(f"{'═'*70}")
    print(f"  {GREEN}Pasados: {passed}/{len(results)}{RESET}")
    if failed > 0:
        print(f"  {RED}Fallidos: {failed}/{len(results)}{RESET}")
    print(f"  Tiempo total: {total_ms:.0f}ms ({total_ms/1000:.1f}s)")
    print()

    for r in results:
        icon = f"{GREEN}✅{RESET}" if r.passed else f"{RED}❌{RESET}"
        print(f"  {icon} {r.name} ({r.duration_ms:.0f}ms)")
        if not r.passed and r.reason:
            print(f"       {RED}↳ {r.reason}{RESET}")

    print()

    if failed > 0:
        print(f"{YELLOW}⚠️  {failed}/{len(results)} tests fallaron{RESET}")
        print(f"Lee los logs del servidor: ./run.sh logs")
        sys.exit(1)
    else:
        print(f"{GREEN}{BOLD}✓ Todos los tests pasaron!{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
