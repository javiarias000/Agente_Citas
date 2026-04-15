"""
Entry point para LangGraph Studio / langgraph dev.

Construye el grafo (V1 o V2 según USE_GRAPH_V2) usando los mismos
servicios que el orquestador de producción, pero sin FastAPI.

Uso:
    langgraph dev          # arranca Studio en localhost:2024
"""

from __future__ import annotations

import os
import asyncio
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

# ── Construir grafo (síncrono, se ejecuta al importar) ───────────────────────


def _build() :
    from langchain_openai import ChatOpenAI
    from src.store import InMemoryStore, PostgresStore
    from sqlalchemy.ext.asyncio import create_async_engine

    use_v2 = os.getenv("USE_GRAPH_V2", "false").lower() in ("1", "true", "yes")

    # LLM
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "1000")),
    )

    # Store — PostgreSQL si hay DATABASE_URL, sino in-memory
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        engine = create_async_engine(db_url, pool_pre_ping=True)
        store = PostgresStore(engine)
    else:
        store = InMemoryStore()

    # Calendar service (opcional — si falta, el agente responde sin agendar)
    calendar_service = None
    if os.getenv("GOOGLE_CALENDAR_ENABLED", "false").lower() in ("1", "true", "yes"):
        try:
            from services.google_calendar_service import GoogleCalendarService as _GCal
            from src.calendar_service import GoogleCalendarService as _AsyncWrapper
            calendar_id = os.getenv("GOOGLE_CALENDAR_DEFAULT_ID", "primary")
            tz = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "America/Guayaquil")
            svc = _GCal(calendar_id=calendar_id, timezone=tz)
            calendar_service = _AsyncWrapper(svc)
        except Exception as e:
            print(f"[studio] Calendar no disponible: {e}")

    # DB service (appointment CRUD)
    db_service = None
    if db_url:
        try:
            from services.appointment_service import AppointmentService
            db_service = AppointmentService(engine)
        except Exception as e:
            print(f"[studio] DB service no disponible: {e}")

    # Compilar
    if use_v2:
        from src.graph_v2 import compile_graph_v2
        return compile_graph_v2(
            llm=llm,
            store=store,
            calendar_service=calendar_service,
            db_service=db_service,
        )
    else:
        from src.graph import compile_graph
        return compile_graph(
            llm=llm,
            store=store,
            calendar_service=calendar_service,
            db_service=db_service,
        )


# LangGraph Studio busca un objeto compilado a nivel de módulo.
graph = _build()
