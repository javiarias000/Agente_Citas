#!/usr/bin/env python3
"""
Análisis profundo de flujo de agendamiento.
Revisa variables en cada nodo, coherencia de estado, y por qué fallan bookings.
"""

import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from langsmith import Client

load_dotenv()

API_KEY = os.getenv("LANGCHAIN_API_KEY")
PROJECT = os.getenv("LANGCHAIN_PROJECT", "Agente_Citas_Lanchain")

if not API_KEY:
    print("❌ LANGCHAIN_API_KEY no configurado")
    exit(1)

client = Client(api_key=API_KEY)


def analyze_trace_flow():
    """Obtiene traces recientes y analiza flujo de agendamiento."""
    print(f"\n{'='*100}")
    print(f"🔍 ANÁLISIS PROFUNDO DE FLUJO DE AGENDAMIENTO")
    print(f"{'='*100}\n")

    # Obtener último dia de traces
    start_time = datetime.utcnow() - timedelta(hours=24)

    try:
        runs = list(client.list_runs(
            project_name=PROJECT,
            limit=100,
            start_time=start_time
        ))
    except Exception as e:
        print(f"❌ Error: {e}")
        return

    # Agrupar por sesión (phone_number)
    sessions = {}
    for run in runs:
        if not run.inputs:
            continue

        phone = run.inputs.get("phone_number") or run.inputs.get("sender")
        if phone:
            if phone not in sessions:
                sessions[phone] = []
            sessions[phone].append(run)

    if not sessions:
        print("⚠️  No hay traces con phone_number")
        return

    print(f"📱 Encontradas {len(sessions)} sesiones diferentes\n")

    # Analizar cada sesión
    for phone, session_runs in sessions.items():
        print(f"\n{'='*100}")
        print(f"📞 SESIÓN: {phone}")
        print(f"{'='*100}")

        # Ordenar por timestamp
        session_runs.sort(key=lambda r: r.start_time)

        # Extraer flujo de nodos
        node_sequence = []
        for run in session_runs:
            if run.name and not run.name.startswith(("ChatOpenAI", "arcadium/")):
                node_sequence.append(run)

        print(f"🔗 Flujo: {' → '.join([r.name for r in node_sequence[:15]])}")
        print(f"   Total nodos: {len(node_sequence)}\n")

        # Analizar estado en cada nodo crítico
        critical_nodes = [
            "entry",
            "route_intent",
            "extract_data",
            "check_missing",
            "check_availability",
            "detect_confirmation",
            "book_appointment",
            "save_state"
        ]

        state_history = {}
        booking_success = False
        booking_error = None

        for run in node_sequence:
            node_name = run.name
            inputs = run.inputs or {}
            outputs = run.outputs or {}

            if node_name in critical_nodes:
                print(f"\n📌 {node_name}")
                print(f"   Status: {'✅' if run.status == 'success' else '❌'}")

                # Mostrar variables críticas
                if node_name == "entry":
                    print(f"   📱 phone_number: {inputs.get('phone_number')}")
                    print(f"   💬 message: {inputs.get('_incoming_message', 'N/A')[:60]}")

                if node_name == "route_intent":
                    print(f"   🎯 intent: {outputs.get('intent', 'N/A')}")
                    print(f"   📋 missing_fields: {outputs.get('missing_fields', [])}")

                if node_name == "extract_data":
                    print(f"   🦷 selected_service: {outputs.get('selected_service', 'N/A')}")
                    print(f"   📅 datetime_preference: {outputs.get('datetime_preference', 'N/A')}")
                    print(f"   👤 patient_name: {outputs.get('patient_name', 'N/A')}")

                if node_name == "check_availability":
                    slots = outputs.get('available_slots', [])
                    print(f"   ✨ available_slots: {len(slots)} encontrados")
                    if slots:
                        print(f"      Primeros 3: {slots[:3]}")
                    if "last_error" in outputs:
                        print(f"   ⚠️  Error: {outputs.get('last_error', '')[:80]}")

                if node_name == "detect_confirmation":
                    print(f"   ✅ confirmation_result: {outputs.get('confirmation_result', 'N/A')}")
                    print(f"   🎪 confirmation_type: {outputs.get('confirmation_type', 'N/A')}")
                    print(f"   ⏰ selected_slot: {outputs.get('selected_slot', 'N/A')}")

                if node_name == "book_appointment":
                    if run.status == "success":
                        booking_success = True
                        print(f"   ✅ google_event_id: {outputs.get('google_event_id', 'N/A')}")
                        print(f"   ✅ confirmation_sent: {outputs.get('confirmation_sent', False)}")
                        print(f"   ✅ appointment_id: {outputs.get('appointment_id', 'N/A')}")
                    else:
                        booking_error = run.error or "Unknown error"
                        print(f"   ❌ Error: {booking_error[:100]}")
                        if outputs.get('last_error'):
                            print(f"   ❌ last_error: {outputs.get('last_error')[:100]}")

                if node_name == "save_state":
                    print(f"   💾 has_appointment: {outputs.get('has_appointment', False)}")

                # Guardar estado para análisis
                state_history[node_name] = {
                    "status": run.status,
                    "inputs": inputs,
                    "outputs": outputs
                }

        # Resumen de la sesión
        print(f"\n{'─'*100}")
        print(f"📊 RESULTADO DE SESIÓN")
        print(f"{'─'*100}")

        # Verificar coherencia de estado
        issues = []

        # Verificar que confirmation_sent corresponda con google_event_id
        if "book_appointment" in state_history:
            book_state = state_history["book_appointment"]["outputs"]
            confirmation_sent = book_state.get("confirmation_sent", False)
            event_id = book_state.get("google_event_id")

            if confirmation_sent and not event_id:
                issues.append("❌ confirmation_sent=True pero NO hay google_event_id")
            elif event_id and not confirmation_sent:
                issues.append("⚠️  google_event_id existe pero confirmation_sent=False")

        # Verificar que tenga slots antes de booking
        if "check_availability" in state_history and "book_appointment" in state_history:
            check_state = state_history["check_availability"]["outputs"]
            slots = check_state.get("available_slots", [])
            if not slots:
                issues.append("❌ book_appointment intentó reservar pero sin available_slots")

        # Verificar campos requeridos
        if "entry" in state_history:
            entry_state = state_history["entry"]["inputs"]
            phone = entry_state.get("phone_number")
            if not phone:
                issues.append("❌ entry sin phone_number")

        if issues:
            for issue in issues:
                print(issue)
        else:
            if booking_success:
                print("✅ Flujo coherente - Agendamiento completado")
            else:
                print("⚠️  Flujo coherente pero sin booking")

        if booking_error:
            print(f"❌ Booking error: {booking_error[:150]}")

        # Resumen final
        print(f"\nEstatus final:")
        print(f"  - Booking success: {'✅' if booking_success else '❌'}")
        print(f"  - Estado coherente: {'✅' if not issues else '❌'}")


def show_failed_bookings():
    """Muestra agendamientos que fallaron."""
    print(f"\n{'='*100}")
    print(f"❌ AGENDAMIENTOS FALLIDOS")
    print(f"{'='*100}\n")

    start_time = datetime.utcnow() - timedelta(hours=24)

    try:
        # Buscar nodos book_appointment que fallaron
        runs = list(client.list_runs(
            project_name=PROJECT,
            limit=100,
            start_time=start_time,
            filter='eq(name, "book_appointment")'
        ))
    except Exception as e:
        print(f"Error: {e}")
        return

    failed = [r for r in runs if r.status == "error"]

    if not failed:
        print("✅ No hay agendamientos fallidos\n")
        return

    print(f"Encontrados {len(failed)} agendamientos fallidos:\n")

    for run in failed:
        phone = run.inputs.get("phone_number", "?") if run.inputs else "?"
        service = run.inputs.get("selected_service", "?") if run.inputs else "?"

        print(f"📱 {phone} | 🦷 {service}")
        print(f"   ❌ {run.error[:150] if run.error else 'Unknown error'}")

        if run.outputs and run.outputs.get("last_error"):
            print(f"   Last error: {run.outputs.get('last_error')[:150]}")
        print()


if __name__ == "__main__":
    analyze_trace_flow()
    show_failed_bookings()
