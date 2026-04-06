"""
Script de migración — convierte estados viejos al nuevo formato ArcadiumState.

Idempotente: seguro correr múltiples veces.
Lee agent_states con version != 2, convierte, guarda con version=2.

Uso:
    python scripts/migrate_state.py --dry-run   # sólo reporta
    python scripts/migrate_state.py             # aplica cambios
"""

import asyncio
import argparse
import json
import structlog
from typing import Dict, Any, List

logger = structlog.get_logger("migration")


async def migrate_states(engine, dry_run: bool = False) -> Dict[str, int]:
    """
    Migra todos los estados no-v2 al nuevo formato.

    Returns:
        Dict con contadores: {"read": N, "converted": M, "skipped": K, "errors": E}
    """
    from sqlalchemy import text

    stats = {"read": 0, "converted": 0, "skipped": 0, "errors": 0}

    # Leer todos los estados
    sql = text("SELECT id, session_id, state FROM agent_states")
    async with engine.begin() as conn:
        rows = await conn.execute(sql)

    for row in rows:
        stats["read"] += 1
        session_id = row[1]
        state = row[2]

        if isinstance(state, str):
            state = json.loads(state)

        # Skip si ya es v2
        if state.get("version") == 2:
            stats["skipped"] += 1
            continue

        try:
            new_state = _convert_state(state)
            new_state["version"] = 2

            if dry_run:
                logger.info(
                    "DRY-RUN: estado a migrar",
                    session_id=session_id,
                    old_version=state.get("version"),
                    new_keys=list(new_state.keys()),
                )
            else:
                # Guardar nuevo estado
                upsert_sql = text(
                    "UPDATE agent_states SET state = :data::jsonb "
                    "WHERE session_id = :sid"
                )
                await conn.execute(
                    upsert_sql,
                    {
                        "sid": session_id,
                        "data": json.dumps(new_state, default=str),
                    },
                )
                logger.info(
                    "Estado migrado",
                    session_id=session_id,
                    old_version=state.get("version"),
                )

            stats["converted"] += 1

        except Exception as e:
            logger.error("Error migrando estado", session_id=session_id, error=str(e))
            stats["errors"] += 1

    return stats


def _convert_state(old: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte un estado viejo al formato ArcadiumState.

    Mapeo:
    - current_step (SupportStep) → current_step (str)
    - patient_name, patient_phone → mismo nombre
    - selected_service → seleccionado_service (lowercase)
    - service_duration → mismo
    - datetime_preference → mismo
    - datetime_adjusted → nuevo default False
    - appointment_id, google_event_id, google_event_link → mismo
    - confirmation_sent → mismo
    - context_vars.fecha_hoy → fecha_hoy
    - context_vars.hora_actual → hora_actual
    - etc.
    """
    new: Dict[str, Any] = {}

    # Mapeo directo
    for field in [
        "patient_name",
        "patient_phone",
        "service_duration",
        "datetime_preference",
        "appointment_id",
        "google_event_id",
        "google_event_link",
        "confirmation_sent",
        "intent",
    ]:
        if field in old:
            new[field] = old[field]

    # current_step: SupportStep → string
    if "current_step" in old:
        step = old["current_step"]
        if isinstance(step, dict):
            step = step.get("value", str(step))
        new["current_step"] = str(step)

    # context_vars → flat fields
    ctx = old.get("context_vars") or {}
    for key in ["fecha_hoy", "hora_actual", "manana_fecha", "manana_dia", "dia_semana_hoy"]:
        if key in ctx:
            new[key] = ctx[key]

    # Defaults para campos nuevos
    new.setdefault("datetime_adjusted", False)
    new.setdefault("awaiting_confirmation", False)
    new.setdefault("confirmation_type", None)
    new.setdefault("available_slots", [])
    new.setdefault("selected_slot", None)
    new.setdefault("confirmation_result", None)
    new.setdefault("missing_fields", [])
    new.setdefault("last_error", None)
    new.setdefault("errors_count", 0)
    new.setdefault("should_escalate", False)
    new.setdefault("conversation_turns", old.get("conversation_turns", 0))

    return new


async def main():
    parser = argparse.ArgumentParser(description="Migrate agent states to ArcadiumState v2")
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not modify")
    parser.add_argument("--database-url", required=True, help="PostgreSQL connection URL")
    args = parser.parse_args()

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(args.database_url)

    logger.info("Iniciando migración", dry_run=args.dry_run)
    stats = await migrate_states(engine, dry_run=args.dry_run)
    logger.info("Migración completada", stats=stats)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
