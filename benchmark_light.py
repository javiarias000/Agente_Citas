#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Benchmark ligero: StateMachineAgent con InMemory.
Mide latencias mínimas sin interferencias de DB lenta.
"""

import asyncio
import time
import uuid
import statistics
from datetime import datetime

from memory.memory_manager import MemoryManager
from core.store import ArcadiumStore
from agents.state_machine_agent import StateMachineAgent
from core.config import get_settings, Settings


async def setup_memory():
    settings = Settings(USE_POSTGRES_FOR_MEMORY=False)
    memory_manager = MemoryManager(settings)
    await memory_manager.initialize()
    return ArcadiumStore(memory_manager)


async def benchmark_state_machine(iterations: int = 10):
    """Benchmark de StateMachineAgent"""
    print("🚀 Benchmark StateMachineAgent (InMemory)")
    print(f"   Iteraciones: {iterations}")

    store = await setup_memory()
    phone = f"+bench_sm_{uuid.uuid4().hex[:8]}"
    session_id = phone

    agent = StateMachineAgent(
        session_id=session_id,
        store=store,
        project_id=None,
        verbose=False
    )
    await agent.initialize()

    messages = ["Hola", "Quiero una cita", "Para mañana a las 10am"]
    latencies = []

    print("\n📈 Ejecutando iteraciones...")
    for i in range(iterations):
        msg = messages[i % len(messages)]
        t0 = time.perf_counter()
        try:
            result = await agent.process_message(msg)
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
            status = result.get('status', 'unknown')
            print(f"   Iter {i+1}: {status} - {latencies[-1]*1000:.1f}ms")
        except Exception as e:
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
            print(f"   Iter {i+1}: ERROR - {e}")

    # Stats
    if latencies:
        lat_ms = [l * 1000 for l in latencies]
        avg = statistics.mean(lat_ms)
        median = statistics.median(lat_ms)
        p95 = sorted(lat_ms)[int(len(lat_ms) * 0.95)]
        p99 = sorted(lat_ms)[int(len(lat_ms) * 0.99)]
        total_time = sum(latencies)
        throughput = iterations / total_time if total_time > 0 else 0
    else:
        avg = median = p95 = p99 = throughput = 0

    print("\n📊 Resultados:")
    print(f"   Total tiempo: {total_time:.2f}s")
    print(f"   Throughput: {throughput:.2f} msg/seg")
    print(f"   Latencia avg: {avg:.1f}ms")
    print(f"   Latencia mediana (P50): {median:.1f}ms")
    print(f"   P95: {p95:.1f}ms")
    print(f"   P99: {p99:.1f}ms")

    # Cleanup
    await store.clear_session(session_id)

    print("\n✅ Benchmark completado")
    return {
        "iterations": iterations,
        "total_time_sec": total_time,
        "throughput_msg_per_sec": throughput,
        "latency_avg_ms": avg,
        "latency_p50_ms": median,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
        "timestamp": datetime.utcnow().isoformat()
    }


async def main():
    try:
        results = await benchmark_state_machine(iterations=15)
        # Guardar JSON
        import json
        with open("benchmark_state_machine.json", "w") as f:
            json.dump(results, f, indent=2)
        print("\n📁 Resultados guardados en benchmark_state_machine.json")
        return True
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)
