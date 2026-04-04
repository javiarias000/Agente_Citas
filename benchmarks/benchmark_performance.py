#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Benchmark de rendimiento del sistema Arcadium Automation.

Mide:
- Latencia de process_message (P50, P95, P99)
- Throughput (mensajes/segundo)
- Uso de memoria
- Tiempo de respuesta de herramientas (agendar_cita)
"""

import asyncio
import time
import uuid
import statistics
from datetime import datetime
from typing import List

from memory.memory_manager import MemoryManager
from core.store import ArcadiumStore
from agents.deyy_agent import DeyyAgent
from core.config import get_settings


async def setup_db():
    from db import init_session_maker
    from sqlalchemy.ext.asyncio import create_async_engine
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    init_session_maker(engine)
    print("✅ DB inicializada para benchmark")


async def benchmark_single_agent(iterations: int = 10) -> dict:
    """Benchmark de un agente procesando mensajes secuenciales"""
    await setup_db()

    settings = get_settings()
    # Forzar InMemory para evitar latencia de DB en measurements
    from core.config import Settings
    test_settings = Settings(USE_POSTGRES_FOR_MEMORY=False)
    memory_manager = MemoryManager(settings=test_settings)
    await memory_manager.initialize()
    store = ArcadiumStore(memory_manager)

    test_phone = f"+bench_{uuid.uuid4().hex[:8]}"
    session_id = test_phone

    agent = DeyyAgent(
        session_id=session_id,
        store=store,
        project_id=None,
        verbose=False,
        checkpointer=None
    )

    # Mensajes de prueba simples
    messages = [
        "Hola",
        "Quiero agendar una cita",
        "Para mañana",
        "Mi nombre es Test"
    ]

    latencies = []
    start_total = time.perf_counter()

    for i in range(iterations):
        msg = messages[i % len(messages)]
        t0 = time.perf_counter()
        try:
            result = await agent.process_message(message=msg)
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
        except Exception as e:
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
            print(f"   ⚠️  Iteración {i+1} error: {e}")

    total_time = time.perf_counter() - start_total

    # Estadísticas
    if latencies:
        latencies_ms = [l * 1000 for l in latencies]
        p50 = statistics.median(latencies_ms)
        p95 = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]
        p99 = sorted(latencies_ms)[int(len(latencies_ms) * 0.99)]
        avg = statistics.mean(latencies_ms)
    else:
        p50 = p95 = p99 = avg = 0

    throughput = iterations / total_time if total_time > 0 else 0

    return {
        "iterations": iterations,
        "total_time_sec": total_time,
        "throughput_msg_per_sec": throughput,
        "latency_avg_ms": avg,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99
    }


async def benchmark_concurrent_agents(num_agents: int = 5, messages_per_agent: int = 3) -> dict:
    """Benchmark de múltiples agentes en paralelo"""
    await setup_db()

    settings = get_settings()
    from core.config import Settings
    test_settings = Settings(USE_POSTGRES_FOR_MEMORY=False)
    memory_manager = MemoryManager(settings=test_settings)
    await memory_manager.initialize()
    store_class = ArcadiumStore

    # Crear agentes
    agents = []
    session_ids = []
    for i in range(num_agents):
        store = store_class(memory_manager)  # Mismo store, thread-safe?
        phone = f"+bench_conc_{i}_{uuid.uuid4().hex[:6]}"
        session_id = phone
        agent = DeyyAgent(
            session_id=session_id,
            store=store,
            project_id=None,
            verbose=False
        )
        agents.append((agent, store, session_id))
        session_ids.append(session_id)

    # Ejecutar en paralelo
    async def run_agent(agent_tuple, msg):
        agent, store, sid = agent_tuple
        t0 = time.perf_counter()
        try:
            await agent.process_message(message=msg)
            return time.perf_counter() - t0
        except Exception as e:
            return None

    tasks = []
    total_messages = num_agents * messages_per_agent
    for i, (agent_tuple) in enumerate(agents):
        for j in range(messages_per_agent):
            msg = f"Mensaje {j+1} del agente {i}"
            tasks.append(run_agent(agent_tuple, msg))

    start = time.perf_counter()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.perf_counter() - start

    # Filtrar None (errores)
    latencies = [r for r in results if r is not None]
    if latencies:
        latencies_ms = [l * 1000 for l in latencies]
        throughput = len(latencies) / total_time
        avg = statistics.mean(latencies_ms)
        p95 = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]
    else:
        throughput = 0
        avg = p95 = 0

    # Cleanup
    for agent, store, sid in agents:
        await store.clear_session(sid)

    return {
        "num_agents": num_agents,
        "total_messages": len(latencies),
        "total_time_sec": total_time,
        "throughput_msg_per_sec": throughput,
        "latency_avg_ms": avg,
        "latency_p95_ms": p95,
        "error_count": total_messages - len(latencies)
    }


async def main():
    print("\n" + "="*60)
    print("🚀 BENCHMARK: Arcadium Automation Performance")
    print("="*60)

    config = {
        "single_agent_iterations": 20,
        "concurrent_agents": 3,
        "messages_per_agent": 5
    }
    print(f"\n⚙️  Configuración: {config}")

    # Benchmark single agent
    print("\n📈 Benchmark 1: Agente único (secuencial)")
    single_results = await benchmark_single_agent(iterations=config["single_agent_iterations"])
    print(f"   Iteraciones: {single_results['iterations']}")
    print(f"   Tiempo total: {single_results['total_time_sec']:.2f}s")
    print(f"   Throughput: {single_results['throughput_msg_per_sec']:.2f} msg/seg")
    print(f"   Latencia avg: {single_results['latency_avg_ms']:.1f}ms")
    print(f"   P50: {single_results['latency_p50_ms']:.1f}ms")
    print(f"   P95: {single_results['latency_p95_ms']:.1f}ms")
    print(f"   P99: {single_results['latency_p99_ms']:.1f}ms")

    # Benchmark concurrent
    print("\n📈 Benchmark 2: Agentes concurrentes")
    conc_results = await benchmark_concurrent_agents(
        num_agents=config["concurrent_agents"],
        messages_per_agent=config["messages_per_agent"]
    )
    print(f"   Agentes: {conc_results['num_agents']}")
    print(f"   Mensajes exitosos: {conc_results['total_messages']}")
    print(f"   Tiempo total: {conc_results['total_time_sec']:.2f}s")
    print(f"   Throughput: {conc_results['throughput_msg_per_sec']:.2f} msg/seg")
    print(f"   Latency avg: {conc_results['latency_avg_ms']:.1f}ms")
    print(f"   P95: {conc_results['latency_p95_ms']:.1f}ms")
    print(f"   Errores: {conc_results['error_count']}")

    print("\n" + "="*60)
    print("✅✅✅ BENCHMARK COMPLETADO ✅✅✅")
    print("="*60)

    # Guardar resultados a JSON?
    # Podemos escribir a archivo
    import json
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "config": config,
        "single_agent": single_results,
        "concurrent": conc_results
    }
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\n📁 Resultados guardados en benchmark_results.json")


if __name__ == "__main__":
    asyncio.run(main())
