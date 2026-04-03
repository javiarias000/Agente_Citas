#!/usr/bin/env python3
"""
Script rápido para crear el esquema completo en PostgreSQL
Alternativa simple a migrations/ si solo quieres crear tablas una vez
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from datetime import datetime, timezone
from pathlib import Path

def main():
    print("\n🗃️  Creating Arcadium Database Schema\n")

    # Leer connection string desde .env
    env_path = Path(__file__).parent.parent / '.env'
    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        print("   Copy .env.example to .env and fill in your values")
        return

    with open(env_path) as f:
        for line in f:
            if line.startswith('DATABASE_URL='):
                db_url = line.split('=', 1)[1].strip()
                break
        else:
            print("❌ DATABASE_URL not found in .env")
            return

    # Añadir sslmode si no existe
    if '?' not in db_url:
        db_url += '?sslmode=require'
    elif 'sslmode=' not in db_url:
        db_url += '&sslmode=require'

    print(f"📋 Connecting to: {db_url.split('@')[0]}***\n")

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()

        print("🔌 Connected!\n")

        # Habilitar extensiones
        print("Enabling extensions...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";")
        print("  ✅ Extensions enabled\n")

        # ============================================
        # 1. conversations
        # ============================================
        print("Creating table: conversations...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                phone_number VARCHAR(20) NOT NULL,
                platform VARCHAR(50) DEFAULT 'whatsapp' NOT NULL,
                status VARCHAR(50) DEFAULT 'active' NOT NULL,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_phone_number
            ON conversations(phone_number);
            CREATE INDEX IF NOT EXISTS idx_conversations_status_updated
            ON conversations(status, updated_at);
        """)
        print("  ✅ conversations created")

        # ============================================
        # 2. messages
        # ============================================
        print("Creating table: messages...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                direction VARCHAR(20) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
                message_type VARCHAR(50) DEFAULT 'text' NOT NULL,
                content TEXT,
                raw_payload JSONB DEFAULT '{}',
                processed BOOLEAN DEFAULT FALSE NOT NULL,
                processing_error TEXT,
                agent_response TEXT,
                tool_calls JSONB DEFAULT '[]',
                execution_time_ms BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
            ON messages(conversation_id, created_at DESC);
        """)
        print("  ✅ messages created")

        # ============================================
        # 3. appointments
        # ============================================
        print("Creating table: appointments...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                phone_number VARCHAR(20) NOT NULL,
                appointment_date TIMESTAMPTZ NOT NULL,
                service_type VARCHAR(100) NOT NULL,
                status VARCHAR(50) DEFAULT 'scheduled' NOT NULL,
                notes TEXT,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_appointments_phone_date
            ON appointments(phone_number, appointment_date DESC);
        """)
        print("  ✅ appointments created")

        # ============================================
        # 4. tool_call_logs
        # ============================================
        print("Creating table: tool_call_logs...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_call_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id VARCHAR(100) NOT NULL,
                tool_name VARCHAR(100) NOT NULL,
                input_data JSONB NOT NULL,
                output_data JSONB,
                success BOOLEAN DEFAULT TRUE NOT NULL,
                error_message TEXT,
                execution_time_ms BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_call_logs_session_tool
            ON tool_call_logs(session_id, tool_name);
        """)
        print("  ✅ tool_call_logs created")

        # ============================================
        # 5. langchain_memory (NUEVA)
        # ============================================
        print("Creating table: langchain_memory...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS langchain_memory (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(255) NOT NULL,
                type VARCHAR(20) NOT NULL CHECK (type IN ('human', 'ai')),
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_langchain_memory_session_created
            ON langchain_memory(session_id, created_at);
        """)
        print("  ✅ langchain_memory created")

        # ============================================
        # 6. Trigger para updated_at
        # ============================================
        print("Creating triggers for updated_at...")
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)

        for table in ['conversations', 'appointments']:
            cursor.execute(f"""
                DROP TRIGGER IF EXISTS update_{table}_updated_at ON {table};
                CREATE TRIGGER update_{table}_updated_at
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW
                    EXECUTE FUNCTION update_updated_at_column();
            """)
        print("  ✅ triggers created\n")

        print("="*60)
        print("✅ SCHEMA CREATED SUCCESSFULLY!")
        print("="*60)

        # Mostrar tablas
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = [row[0] for row in cursor.fetchall()]

        print(f"\n📊 Tables created ({len(tables)}):")
        for table in tables:
            print(f"   ✓ {table}")

        # Verificar que todas existan
        expected = {'conversations', 'messages', 'appointments',
                   'tool_call_logs', 'langchain_memory'}
        if not expected.issubset(set(tables)):
            missing = expected - set(tables)
            print(f"\n⚠️  Missing tables: {missing}")

        print("\n💡 Next steps:")
        print("   1. Verify: python db/verify.py")
        print("   2. Run the app: ./run.sh dev")
        print("   3. Send test: ./run.sh example\n")

        conn.close()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
