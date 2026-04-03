-- ============================================
-- Arcadium Automation - Migración Inicial
-- Fecha: 2025-04-02
-- Descripción: Creación completa de esquema
-- ============================================

-- Habilitar extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- Tabla: conversations
-- ============================================
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(20) NOT NULL,
    platform VARCHAR(50) DEFAULT 'whatsapp' NOT NULL,
    status VARCHAR(50) DEFAULT 'active' NOT NULL,
    meta_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para conversations
CREATE INDEX IF NOT EXISTS idx_conversations_phone_number ON conversations(phone_number);
CREATE INDEX IF NOT EXISTS idx_conversations_status_updated ON conversations(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_platform_status ON conversations(platform, status);

-- Trigger para updated_at
CREATE OR REPLACE FUNCTION update_conversations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_conversations_updated_at ON conversations;
CREATE TRIGGER trigger_update_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW
    EXECUTE FUNCTION update_conversations_updated_at();

-- ============================================
-- Tabla: messages
-- ============================================
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
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

-- Índices para messages
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction);
CREATE INDEX IF NOT EXISTS idx_messages_processed ON messages(processed);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC);

-- ============================================
-- Tabla: appointments
-- ============================================
CREATE TABLE IF NOT EXISTS appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(20) NOT NULL,
    appointment_date TIMESTAMPTZ NOT NULL,
    service_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) DEFAULT 'scheduled' NOT NULL CHECK (
        status IN ('scheduled', 'cancelled', 'completed', 'no_show')
    ),
    notes TEXT,
    meta_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para appointments
CREATE INDEX IF NOT EXISTS idx_appointments_phone_date ON appointments(phone_number, appointment_date DESC);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_appointments_appointment_date ON appointments(appointment_date);

-- Trigger para updated_at
CREATE OR REPLACE FUNCTION update_appointments_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_appointments_updated_at ON appointments;
CREATE TRIGGER trigger_update_appointments_updated_at
    BEFORE UPDATE ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION update_appointments_updated_at();

-- ============================================
-- Tabla: tool_call_logs
-- ============================================
CREATE TABLE IF NOT EXISTS tool_call_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(100) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    input_data JSONB NOT NULL,
    output_data JSONB,
    success BOOLEAN DEFAULT TRUE NOT NULL,
    error_message TEXT,
    execution_time_ms BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para tool_call_logs
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_session_tool ON tool_call_logs(session_id, tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_created_at ON tool_call_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_tool_name ON tool_call_logs(tool_name);

-- ============================================
-- Tabla: langchain_memory (NUEVA)
-- ============================================
CREATE TABLE IF NOT EXISTS langchain_memory (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('human', 'ai')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para langchain_memory
CREATE INDEX IF NOT EXISTS idx_langchain_memory_session_created ON langchain_memory(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_langchain_memory_created_at ON langchain_memory(created_at DESC);

-- Partitioning opcional por mes (para grandes volúmenes)
-- Descomentar si esperas > 1M de registros
-- CREATE TABLE langchain_memory_y2025m04 PARTITION OF langchain_memory
--     FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');

-- ============================================
-- Tabla: migrations (para track de migraciones)
-- ============================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    version VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Insertar migración inicial
INSERT INTO schema_migrations (version, name)
VALUES ('001_initial_schema', 'Initial Arcadium Automation schema')
ON CONFLICT (version) DO NOTHING;

-- ============================================
-- Vistas útiles
-- ============================================

-- Vista: conversation_stats
CREATE OR REPLACE VIEW conversation_stats AS
SELECT
    c.id,
    c.phone_number,
    c.platform,
    c.status,
    c.created_at,
    COUNT(m.id) as total_messages,
    COUNT(CASE WHEN m.direction = 'inbound' THEN 1 END) as inbound_count,
    COUNT(CASE WHEN m.direction = 'outbound' THEN 1 END) as outbound_count,
    MAX(m.created_at) as last_message_at
FROM conversations c
LEFT JOIN messages m ON m.conversation_id = c.id
GROUP BY c.id;

-- Vista: appointment_stats
CREATE OR REPLACE VIEW appointment_stats AS
SELECT
    phone_number,
    status,
    COUNT(*) as total,
    MIN(appointment_date) as next_appointment,
    MAX(appointment_date) as last_appointment
FROM appointments
GROUP BY phone_number, status;

-- Vista: recent_activity
CREATE OR REPLACE VIEW recent_activity AS
SELECT
    'message' as type,
    m.id,
    m.conversation_id,
    c.phone_number,
    m.direction,
    m.created_at
FROM messages m
JOIN conversations c ON m.conversation_id = c.id
UNION ALL
SELECT
    'appointment' as type,
    a.id,
    NULL as conversation_id,
    a.phone_number,
    CASE WHEN a.status = 'scheduled' THEN 'outbound' ELSE 'system' END as direction,
    a.created_at
FROM appointments a
ORDER BY created_at DESC
LIMIT 100;

-- ============================================
-- Funciones útiles
-- ============================================

-- Función: get_conversation_history
CREATE OR REPLACE FUNCTION get_conversation_history(
    p_phone_number VARCHAR(20),
    p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    message_id UUID,
    direction VARCHAR(20),
    content TEXT,
    created_at TIMESTAMPTZ,
    message_type VARCHAR(50)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.direction,
        m.content,
        m.created_at,
        m.message_type
    FROM messages m
    JOIN conversations c ON m.conversation_id = c.id
    WHERE c.phone_number = p_phone_number
    ORDER BY m.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Función: cleanup_old_memories
CREATE OR REPLACE FUNCTION cleanup_old_memories(
    p_days_old INTEGER DEFAULT 30
)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM langchain_memory
    WHERE created_at < NOW() - INTERVAL '1 day' * p_days_old;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Row Level Security (opcional - para Supabase)
-- ============================================
-- Descomentar si necesitas RLS
-- ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE langchain_memory ENABLE ROW LEVEL SECURITY;

-- Política: Solo acceder a datos propios (ejemplo)
-- CREATE POLICY "Users can access own data" ON conversations
--     FOR ALL USING (phone_number = current_setting('app.current_phone')::varchar);

-- ============================================
-- Permisos
-- ============================================
-- Asegurar que el usuario de la app tiene todos los permisos
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO arcadium_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO arcadium_user;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO arcadium_user;

-- ============================================
-- Comentarios
-- ============================================
COMMENT ON TABLE conversations IS 'Conversaciones de WhatsApp (una por número)';
COMMENT ON TABLE messages IS 'Mensajes individuales de cada conversación';
COMMENT ON TABLE appointments IS 'Citas agendadas por AppointmentService';
COMMENT ON TABLE tool_call_logs IS 'Log de todas las tool calls del agente (audit trail)';
COMMENT ON TABLE langchain_memory IS 'Memoria de conversación para LangChain (una tabla, múltiples session_id)';

-- ============================================
-- Fin de la migración
-- ============================================
-- Verificar que todo se creó correctamente:
-- \dt
-- SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';
