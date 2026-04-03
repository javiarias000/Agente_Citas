#!/usr/bin/env python3
"""
Verifica el estado de la base de datos y las migraciones
"""

import asyncio
from pathlib import Path
import psycopg2
from db.migrate import get_connection_string, list_migrations, get_applied_migrations

def print_header(text: str):
    """Imprime encabezado con color"""
    print(f"\n\033[1;34m{'='*60}\033[0m")
    print(f"\033[1;34m{text}\033[0m")
    print(f"\033[1;34m{'='*60}\033[0m\n")


def check_connection():
    """Verifica conexión a la base de datos"""
    print_header("🔌 Database Connection")

    try:
        db_url = get_connection_string()
        print(f"Database URL: {db_url.split('@')[0]}***")

        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        # Version
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"✅ PostgreSQL: {version.split(',')[0]}")

        # Current database
        cursor.execute("SELECT current_database();")
        db_name = cursor.fetchone()[0]
        print(f"✅ Database: {db_name}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False


def check_tables():
    """Verifica tablas creadas"""
    print_header("📊 Database Tables")

    try:
        db_url = get_connection_string()
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                table_name,
                pg_size_pretty(pg_total_relation_size(quote_ident(table_name)::regclass)) as size
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)

        tables = cursor.fetchall()

        if not tables:
            print("⚠️  No tables found!")
            return False

        print(f"Found {len(tables)} tables:\n")
        print(f"{'Table':<30} {'Size':<10}")
        print("-" * 42)

        expected_tables = {
            'conversations',
            'messages',
            'appointments',
            'tool_call_logs',
            'langchain_memory',
            'schema_migrations'
        }
        found_tables = set()

        for table, size in tables:
            status = "✅" if table in expected_tables else "⚠️"
            print(f"{status} {table:<27} {size:<10}")
            found_tables.add(table)

        missing = expected_tables - found_tables
        if missing:
            print(f"\n❌ Missing tables: {', '.join(missing)}")
            return False

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error checking tables: {e}")
        return False


def check_migrations():
    """Verifica estado de migraciones"""
    print_header("🔄 Migration Status")

    try:
        migrations = list_migrations()
        db_url = get_connection_string()
        conn = psycopg2.connect(db_url)
        applied = get_applied_migrations(conn)
        conn.close()

        pending = [m for m in migrations if m.stem not in applied]
        applied_list = [m for m in migrations if m.stem in applied]

        print(f"Total migrations: {len(migrations)}")
        print(f"Applied: {len(applied_list)}")
        print(f"Pending: {len(pending)}\n")

        if applied_list:
            print("Applied migrations:")
            for m in applied_list:
                print(f"  ✅ {m.name}")

        if pending:
            print("\nPending migrations:")
            for m in pending:
                print(f"  ⏳ {m.name}")

        if not pending:
            print("\n✅ All migrations are up to date!")
            return True
        else:
            print(f"\n⚠️  {len(pending)} migration(s) pending")
            return False

    except Exception as e:
        print(f"❌ Error checking migrations: {e}")
        return False


def check_indexes():
    """Verifica índices importantes"""
    print_header("📈 Database Indexes")

    try:
        db_url = get_connection_string()
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                t.relname as table_name,
                i.relname as index_name,
                a.attname as column_name
            FROM pg_class t
            JOIN pg_index ix ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE t.relkind = 'r'
            AND t.relname IN (
                'conversations', 'messages', 'appointments',
                'langchain_memory', 'tool_call_logs'
            )
            ORDER BY t.relname, i.relname;
        """)

        indexes = cursor.fetchall()

        if indexes:
            print(f"Found {len(indexes)} indexes:\n")
            print(f"{'Table':<20} {'Index':<25} {'Column':<20}")
            print("-" * 70)

            current_table = None
            for table, index, column in indexes:
                if table != current_table:
                    print(f"\n{table}:")
                    current_table = table
                print(f"  {index:<25} {column:<20}")
        else:
            print("⚠️  No indexes found!")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error checking indexes: {e}")
        return False


def check_constraints():
    """Verifica constraints (FOREIGN KEY, CHECK, etc)"""
    print_header("🔗 Database Constraints")

    try:
        db_url = get_connection_string()
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                tc.table_name,
                tc.constraint_name,
                tc.constraint_type,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
            LEFT JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'public'
            AND tc.table_name IN (
                'conversations', 'messages', 'appointments',
                'langchain_memory', 'tool_call_logs'
            )
            ORDER BY tc.table_name, tc.constraint_name;
        """)

        constraints = cursor.fetchall()

        if constraints:
            print(f"Found {len(constraints)} constraints:\n")
            print(f"{'Table':<20} {'Constraint':<30} {'Type':<5} {'Column':<15}")
            print("-" * 75)

            current_table = None
            for table, name, ctype, column, fk_table, fk_column in constraints:
                if table != current_table:
                    print(f"\n{table}:")
                    current_table = table

                fk_info = f" → {fk_table}.{fk_column}" if fk_table else ""
                print(f"  {name:<30} {ctype:<5} {column:<15}{fk_info}")
        else:
            print("⚠️  No constraints found!")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error checking constraints: {e}")
        return False


async def run_all_checks():
    """Ejecuta todas las verificaciones"""
    print("\n" + "="*60)
    print("ARCADIUM DATABASE VERIFICATION")
    print("="*60)

    results = {
        "Connection": check_connection(),
        "Tables": check_tables(),
        "Migrations": check_migrations(),
        "Indexes": check_indexes(),
        "Constraints": check_constraints()
    }

    print_header("📊 Summary")

    for check, status in results.items():
        icon = "✅" if status else "❌"
        print(f"{icon} {check}")

    all_ok = all(results.values())

    if all_ok:
        print("\n\033[1;32m✅ All checks passed! Database is ready.\033[0m\n")
    else:
        print("\n\033[1;31m❌ Some checks failed. Run migrations:\033[0m")
        print("   python db/migrate.py\n")

    return all_ok


if __name__ == "__main__":
    asyncio.run(run_all_checks())
