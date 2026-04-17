#!/usr/bin/env python3
"""
Review LangSmith traces para Agente_Citas_Lanchain.
Muestra: duración, tokens, errores, y flujo de ejecución.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from langsmith import Client
import json

load_dotenv()

API_KEY = os.getenv("LANGCHAIN_API_KEY")
PROJECT = os.getenv("LANGCHAIN_PROJECT", "Agente_Citas_Lanchain")

if not API_KEY:
    print("❌ LANGCHAIN_API_KEY no configurado en .env")
    exit(1)

client = Client(api_key=API_KEY)


def format_duration(ms):
    """Convierte ms a formato legible."""
    if not ms:
        return "N/A"
    s = ms / 1000
    if s < 1:
        return f"{ms:.0f}ms"
    return f"{s:.1f}s"


def analyze_runs(limit=20, hours=24):
    """Obtiene y analiza últimos runs."""
    print(f"\n{'='*80}")
    print(f"🔍 LangSmith Traces: {PROJECT}")
    print(f"{'='*80}\n")

    # Filtrar por fecha
    start_time = datetime.utcnow() - timedelta(hours=hours)

    try:
        runs = list(client.list_runs(
            project_name=PROJECT,
            limit=limit,
            start_time=start_time
        ))
    except Exception as e:
        print(f"❌ Error conectando a LangSmith: {e}")
        return

    if not runs:
        print("⚠️  No hay traces en las últimas 24 horas")
        return

    print(f"📊 Últimos {len(runs)} traces:\n")

    total_tokens = 0
    total_duration = 0
    error_count = 0

    for i, run in enumerate(runs, 1):
        status_emoji = {
            "success": "✅",
            "error": "❌",
            "pending": "⏳",
        }.get(run.status, "❓")

        duration = (run.end_time - run.start_time).total_seconds() * 1000 if run.end_time else None
        tokens = run.total_tokens or 0

        if run.status == "error":
            error_count += 1

        if duration:
            total_duration += duration
        if tokens:
            total_tokens += tokens

        # Extraer info útil del run
        run_name = run.name or "unnamed"
        phone = "?"
        service = "?"
        intent = "?"

        if run.inputs:
            inputs = run.inputs
            phone = inputs.get("phone_number", inputs.get("sender", "?"))[:12]
            service = inputs.get("selected_service", "?")
            intent = inputs.get("intent", "?")

        print(f"{i:2}. {status_emoji} {run_name}")
        print(f"    📱 {str(phone):15} | 🎯 {str(intent):10} | 🦷 {str(service):15}")
        print(f"    ⏱️  {format_duration(duration):10} | 🔤 {tokens:>5} tokens")

        # Mostrar errores si hay
        if run.status == "error" and run.error:
            print(f"    ❌ Error: {run.error[:100]}")

        print()

    # Resumen
    print(f"{'='*80}")
    print(f"📈 RESUMEN")
    print(f"{'='*80}")
    print(f"Total runs:     {len(runs)}")
    print(f"Errores:        {error_count}")
    print(f"Éxito rate:     {((len(runs) - error_count) / len(runs) * 100):.1f}%")
    print(f"Total tokens:   {total_tokens:,}")
    print(f"Duración total: {format_duration(total_duration)}")
    if len(runs) > 0:
        print(f"Duración promedio: {format_duration(total_duration / len(runs))}")
    print()


def show_error_details(limit=5):
    """Muestra detalles de últimos errores."""
    print(f"\n{'='*80}")
    print(f"❌ ÚLTIMOS ERRORES")
    print(f"{'='*80}\n")

    try:
        runs = list(client.list_runs(
            project_name=PROJECT,
            filter='eq(status, "error")',
            limit=limit,
        ))
    except Exception as e:
        print(f"Error: {e}")
        return

    if not runs:
        print("✅ No hay errores recientemente\n")
        return

    for run in runs:
        print(f"🔴 {run.name} ({run.status})")
        print(f"   Hora: {run.start_time.isoformat()}")
        if run.error:
            print(f"   Error: {run.error[:200]}")
        if run.outputs and "response" in run.outputs:
            print(f"   Respuesta: {run.outputs['response'][:150]}")
        print()


def show_detailed_trace(run_id):
    """Muestra detalles completos de un run."""
    try:
        run = client.read_run(run_id)
        print(f"\n{'='*80}")
        print(f"🔍 Detalles: {run.name}")
        print(f"{'='*80}\n")

        print(f"Status: {run.status}")
        print(f"Duración: {(run.end_time - run.start_time).total_seconds():.2f}s")
        print(f"Tokens: {run.total_tokens}")

        print(f"\nInputs:")
        print(json.dumps(run.inputs, indent=2))

        print(f"\nOutputs:")
        print(json.dumps(run.outputs, indent=2))

        if run.error:
            print(f"\nError: {run.error}")

        print()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "errors":
        show_error_details(limit=10)
    elif len(sys.argv) > 1 and sys.argv[1].startswith("run:"):
        run_id = sys.argv[1].replace("run:", "")
        show_detailed_trace(run_id)
    else:
        # Default: mostrar resumen + errores
        analyze_runs(limit=20)
        show_error_details(limit=5)
