#!/usr/bin/env python3
"""
Script para truncar (borrar contenido) de todas las tablas sin eliminar la estructura.
"""
import asyncio
import sys
from pathlib import Path

# Agregar el directorio raíz del proyecto al PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from core.config import get_settings

async def truncate_all_tables():
    """Trunca todas las tablas del esquema public manteniendo la estructura."""
    settings = get_settings()

    # Crear engine directo para DDL (no usar get_async_session porque puede tener sesión abierta)
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Deshabilitar triggers temporalmente para evitar problemas de FK
        await session.execute(text("SET session_replication_role = replica;"))

        # Lista de tablas a truncar (en orden para no violar constraints)
        tables = [
            "tool_call_logs",
            "appointments",
            "messages",
            "langchain_memory",
            "conversations",
            "agent_toggles",
            "user_profiles",
            "agent_states"
        ]

        for table in tables:
            try:
                await session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
                print(f"✅ Tabla '{table}' truncada")
            except Exception as e:
                print(f"⚠️  Error truncando {table}: {e}")

        # Reactivar triggers
        await session.execute(text("SET session_replication_role = DEFAULT;"))

        await session.commit()
        print("\n✨ Limpieza completada!")

if __name__ == "__main__":
    asyncio.run(truncate_all_tables())
