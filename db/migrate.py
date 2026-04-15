#!/usr/bin/env python3
"""
Script de migración de base de datos
Ejecuta los scripts SQL en orden
"""

import asyncio
from pathlib import Path
from typing import List
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import structlog

logger = structlog.get_logger("migrate")


def get_connection_string() -> str:
    """Obtiene connection string desde .env"""
    from dotenv import load_dotenv
    import os

    # Cargar .env (buscar en raíz del proyecto)
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.error("No .env file found", path=env_path)
        raise SystemExit(1)

    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        logger.error("DATABASE_URL not set in .env")
        raise SystemExit(1)

    # Convertir asyncpg a psycopg2
    db_url = db_url.replace('postgresql+psycopg://', 'postgresql://')
    db_url = db_url.replace('postgresql+asyncpg://', 'postgresql://')

    return db_url


def get_migrations_dir() -> Path:
    """Directorio de migraciones"""
    return Path(__file__).parent / 'migrations'


def list_migrations() -> List[Path]:
    """Lista todos los archivos SQL de migración ordenados"""
    migrations_dir = get_migrations_dir()
    if not migrations_dir.exists():
        logger.error("Migrations directory not found", dir=migrations_dir)
        return []

    migrations = sorted(migrations_dir.glob('*.sql'))
    return migrations


def create_migrations_table(conn) -> None:
    """Crea tabla schema_migrations si no existe"""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                version VARCHAR(50) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)
        conn.commit()
        logger.info("Migrations table ensured")


def get_applied_migrations(conn) -> List[str]:
    """Obtiene lista de migraciones ya aplicadas"""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version;")
            results = cur.fetchall()
            return [row[0] for row in results]
        except Exception:
            # Tabla no existe aún
            return []


def apply_migration(conn, migration_file: Path) -> bool:
    """Aplica una migración SQL"""
    version = migration_file.stem  # Nombre sin extensión
    name = migration_file.name

    logger.info("Applying migration", version=version, file=name)

    try:
        with open(migration_file, 'r', encoding='utf-8') as f:
            sql = f.read()

        with conn.cursor() as cur:
            # Ejecutar en transacción
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            # Ejecutar todo el script completo (sin dividir) para preservar bloques $$
            try:
                cur.execute(sql)
            except Exception as e:
                # Ignorar errores de "already exists" y "duplicate key"
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in [
                    'already exists', 'duplicate', 'permission denied for schema'
                ]):
                    logger.debug("Migration skipped (objects already exist)")
                else:
                    logger.error("SQL Error", error=str(e))
                    raise

        logger.info("Migration applied successfully", version=version)
        return True

    except Exception as e:
        logger.error("Failed to apply migration", version=version, error=str(e))
        return False


def record_migration(conn, version: str, name: str) -> None:
    """Registra migración en tabla schema_migrations"""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO schema_migrations (version, name)
            VALUES (%s, %s)
            ON CONFLICT (version) DO NOTHING;
        """, (version, name))
        conn.commit()


def run_migrations_sync() -> None:
    """
    Ejecuta todas las migraciones pendientes (síncrono).
    Para llamar desde asyncio con asyncio.to_thread().
    """
    print("\n🗃️  Arcadium Database Migration\n")

    # Obtener connection string
    try:
        db_url = get_connection_string()
    except SystemExit:
        raise

    print(f"📋 Database: {db_url.split('@')[0]}***")  # Ocultar password

    migrations = list_migrations()
    if not migrations:
        print("⚠️  No migrations found in db/migrations/")
        return

    print(f"📦 Migrations found: {len(migrations)}")
    for mig in migrations:
        print(f"   - {mig.name}")

    # Conectar a DB
    print("\n🔌 Connecting to database...")
    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        raise SystemExit(1)

    try:
        # Crear tabla de migraciones
        create_migrations_table(conn)

        # Obtener migraciones aplicadas
        applied = get_applied_migrations(conn)
        print(f"✅ Applied migrations: {len(applied)}")

        # Filtrar pendientes
        pending = [m for m in migrations if m.stem not in applied]

        if not pending:
            print(f"\n✅ All migrations are up to date!\n")
            return

        print(f"\n⏳ Pending migrations: {len(pending)}")

        # Aplicar pendientes
        for migration in pending:
            print(f"\nApplying: {migration.name}...")
            if apply_migration(conn, migration):
                record_migration(conn, migration.stem, migration.name)
                print(f"   ✓ Applied successfully")
            else:
                print(f"   ✗ Failed")
                raise SystemExit(1)

        print(f"\n✅ All migrations applied successfully!\n")

        # Mostrar tablas creadas
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            tables = cur.fetchall()

        print(f"📊 Database tables ({len(tables)}):")
        for (table,) in tables:
            print(f"   - {table}")

    finally:
        conn.close()
        print("\n🔒 Connection closed\n")


async def run_migrations() -> None:
    """Wrapper async para ejecutar migraciones"""
    import asyncio
    await asyncio.to_thread(run_migrations_sync)


async def reset_database() -> None:
    """¡PELIGROSO! Elimina todas las tablas y vuelve a crear"""
    print("\n⚠️  DANGER ZONE")
    print("This will DROP ALL TABLES and reapply migrations!")
    response = input("\nType 'YES I AM SURE' to continue: ")

    if response != "YES I AM SURE":
        print("✗ Cancelled\n")
        return

    db_url = get_connection_string()
    print(f"\n🗑️  Dropping all tables...")

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True

        with conn.cursor() as cur:
            # Obtener todas las tablas
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
            """)
            tables = [row[0] for row in cur.fetchall()]

            # Drop en orden correcto (por foreign keys)
            drop_order = [
                'tool_call_logs',
                'messages',
                'appointments',
                'langchain_memory',
                'conversations',
                'schema_migrations'
            ]

            for table in drop_order:
                if table in tables:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
                    print(f"   Dropped: {table}")

        conn.close()
        print("✓ All tables dropped")

        # Aplicar migraciones de nuevo
        await run_migrations()

    except Exception as e:
        print(f"❌ Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Arcadium Database Migrations')
    parser.add_argument('--reset', action='store_true',
                       help='⚠️  DROP ALL TABLES and reapply migrations')
    parser.add_argument('--show', action='store_true',
                       help='Show pending migrations without applying')

    args = parser.parse_args()

    if args.reset:
        asyncio.run(reset_database())
    elif args.show:
        migrations = list_migrations()
        db_url = get_connection_string()
        conn = psycopg2.connect(db_url)
        applied = get_applied_migrations(conn)
        conn.close()

        pending = [m for m in migrations if m.stem not in applied]
        print("\nPending migrations:")
        for m in pending:
            print(f"  - {m.name}")
        print()
    else:
        run_migrations_sync()
