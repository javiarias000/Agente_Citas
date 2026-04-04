#!/usr/bin/env python3
"""
Script para probar la conexión a Supabase/PostgreSQL
"""

import os
import sys
from urllib.parse import urlparse
from pathlib import Path

# Cargar variables de entorno desde .env si existe
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"✓ Cargado .env desde {env_path}")

# Parse DATABASE_URL
database_url = os.getenv("DATABASE_URL")
if not database_url:
    print("✗ ERROR: DATABASE_URL no está definida en el archivo .env")
    sys.exit(1)

print("=== TEST DE CONEXIÓN A SUPABASE ===\n")
print(f"URL: {database_url[:50]}...")  # Mostrar solo los primeros 50 caracteres por seguridad

# Parsear la URL
parsed = urlparse(database_url)
print(f"\nComponentes de la URL:")
print(f"  Driver: {parsed.scheme}")
print(f"  Username: {parsed.username}")
print(f"  Host: {parsed.hostname}")
print(f"  Port: {parsed.port}")
print(f"  Database: {parsed.path.lstrip('/')}")

# Verificar variables de entorno común que pueden afectar
print("\nVariables de entorno relevantes:")
for var in ['PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE']:
    print(f"  {var}: {os.getenv(var, 'no definida')}")

# Intentar conectar
print("\n--- Intentando conexión ---")
try:
    import psycopg2
    print("✓ psycopg2 disponible")

    # Intentar conexión
    conn = psycopg2.connect(database_url, connect_timeout=10)
    print("✓ Conexión exitosa!")

    # Verificar servidor y versión
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()
    print(f"\nVersión de PostgreSQL: {version[0][:80]}...")

    # Listar tablas
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cursor.fetchall()
    print(f"\nTablas en la base de datos ({len(tables)}):")
    for table in tables[:10]:  # mostrar max 10
        print(f"  - {table[0]}")
    if len(tables) > 10:
        print(f"  ... y {len(tables) - 10} más")

    cursor.close()
    conn.close()
    print("\n✅ Base de datos funcionando correctamente!")

except ImportError as e:
    print(f"✗ Error: {e}")
    print("\nPara instalar psycopg2:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

except Exception as e:
    print(f"✗ Error de conexión: {type(e).__name__}: {e}")
    print("\n Posibles causas:")
    print("  1. Contraseña incorrecta")
    print("  2. IP no autorizada en Supabase")
    print("  3. Usuario no existe")
    print("  4. Base de datos no existe")
    print("  5. Problema de red/firewall")
    print("\nPara verificar la contraseña:")
    print("  - Ve a tu proyecto Supabase (supabase.com)")
    print("  - Settings > Database > Connection string")
    print("  - Copia la contraseña correcta")
    print("  - Actualiza el .env con la nueva contraseña")
    sys.exit(1)
