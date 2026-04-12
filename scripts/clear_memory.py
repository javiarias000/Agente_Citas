#!/usr/bin/env python3
"""
Limpia el historial de conversaciones del agente.
- langchain_memory / agent_states: tablas legacy
- checkpoints / checkpoint_blobs / checkpoint_writes: historial real de LangGraph (PostgresSaver)

Uso:
    python scripts/clear_memory.py                          # limpia todo
    python scripts/clear_memory.py --phone +593984865981   # limpia un número (prefijo deyy_)
    python scripts/clear_memory.py --thread deyy_+593984865981  # limpia thread exacto
    python scripts/clear_memory.py --dry-run               # solo muestra cuántos registros borrará
"""
import asyncio
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from core.config import get_settings


async def clear_memory(session_id: str = None, thread_id: str = None, dry_run: bool = False):
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # ── Contar ──────────────────────────────────────────────────────────
        if thread_id:
            n_cp = (await session.execute(
                text("SELECT COUNT(*) FROM checkpoints WHERE thread_id = :tid"), {"tid": thread_id}
            )).scalar()
            n_blobs = (await session.execute(
                text("SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = :tid"), {"tid": thread_id}
            )).scalar()
            n_writes = (await session.execute(
                text("SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id = :tid"), {"tid": thread_id}
            )).scalar()
            n_memory = (await session.execute(
                text("SELECT COUNT(*) FROM langchain_memory WHERE session_id = :sid"), {"sid": session_id or thread_id}
            )).scalar()
            n_states = (await session.execute(
                text("SELECT COUNT(*) FROM agent_states WHERE session_id = :sid"), {"sid": session_id or thread_id}
            )).scalar()
        else:
            n_cp = (await session.execute(text("SELECT COUNT(*) FROM checkpoints"))).scalar()
            n_blobs = (await session.execute(text("SELECT COUNT(*) FROM checkpoint_blobs"))).scalar()
            n_writes = (await session.execute(text("SELECT COUNT(*) FROM checkpoint_writes"))).scalar()
            n_memory = (await session.execute(text("SELECT COUNT(*) FROM langchain_memory"))).scalar()
            n_states = (await session.execute(text("SELECT COUNT(*) FROM agent_states"))).scalar()

        print(f"[{datetime.now(timezone.utc).isoformat()}] Limpieza de memoria del agente")
        print(f"  checkpoints        : {n_cp} registros")
        print(f"  checkpoint_blobs   : {n_blobs} registros")
        print(f"  checkpoint_writes  : {n_writes} registros")
        print(f"  langchain_memory   : {n_memory} registros")
        print(f"  agent_states       : {n_states} registros")

        if dry_run:
            print("  [dry-run] No se borró nada.")
            await engine.dispose()
            return

        # ── Borrar ──────────────────────────────────────────────────────────
        if thread_id:
            await session.execute(
                text("DELETE FROM checkpoint_writes WHERE thread_id = :tid"), {"tid": thread_id}
            )
            await session.execute(
                text("DELETE FROM checkpoint_blobs WHERE thread_id = :tid"), {"tid": thread_id}
            )
            await session.execute(
                text("DELETE FROM checkpoints WHERE thread_id = :tid"), {"tid": thread_id}
            )
            sid = session_id or thread_id
            await session.execute(
                text("DELETE FROM langchain_memory WHERE session_id = :sid"), {"sid": sid}
            )
            await session.execute(
                text("DELETE FROM agent_states WHERE session_id = :sid"), {"sid": sid}
            )
            print(f"  Thread '{thread_id}' limpiado.")
        else:
            # Orden importa: writes/blobs antes que checkpoints (FK)
            await session.execute(text("DELETE FROM checkpoint_writes"))
            await session.execute(text("DELETE FROM checkpoint_blobs"))
            await session.execute(text("DELETE FROM checkpoints"))
            await session.execute(text("DELETE FROM langchain_memory"))
            await session.execute(text("DELETE FROM agent_states"))
            total = n_cp + n_blobs + n_writes + n_memory + n_states
            print(f"  Todo el historial borrado ({total} registros en total).")

        await session.commit()
        print("  Listo.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Limpia memoria del agente")
    parser.add_argument("--phone", help="Limpia un número de teléfono (construye thread_id como deyy_<phone>)")
    parser.add_argument("--thread", help="Limpia thread_id exacto (e.g. deyy_+593984865981)")
    parser.add_argument("--session", help="(legacy) alias de --phone")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra cuántos registros borrará")
    args = parser.parse_args()

    tid = None
    sid = None
    if args.thread:
        tid = args.thread
    elif args.phone:
        tid = f"deyy_{args.phone}"
        sid = args.phone
    elif args.session:
        tid = f"deyy_{args.session}"
        sid = args.session

    asyncio.run(clear_memory(session_id=sid, thread_id=tid, dry_run=args.dry_run))
