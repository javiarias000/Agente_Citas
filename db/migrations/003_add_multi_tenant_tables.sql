-- ============================================
-- Arcadium Automation - Migración: Multi-Tenancy y Gestión
-- Fecha: 2025-04-03
-- Descripción: Añade tablas para proyectos, configuraciones de agente,
--              toggles por conversación, usuarios, y añade project_id a tablas existentes
-- ============================================

-- Habilitar extensiones
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. TABLA: projects
-- ============================================
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL,
    api_key VARCHAR(64) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    whatsapp_webhook_url VARCHAR(500),
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para projects
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);
CREATE INDEX IF NOT EXISTS idx_projects_active ON projects(is_active);

-- Trigger para updated_at
CREATE OR REPLACE FUNCTION update_projects_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_projects_updated_at ON projects;
CREATE TRIGGER trigger_update_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_projects_updated_at();

-- ============================================
-- 2. TABLA: project_agent_configs
-- ============================================
CREATE TABLE IF NOT EXISTS project_agent_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_name VARCHAR(100) DEFAULT 'DeyyAgent' NOT NULL,
    system_prompt TEXT DEFAULT 'Eres un asistente AI útil para el proyecto {project_name}. {custom_instructions}' NOT NULL,
    custom_instructions TEXT,
    max_iterations INTEGER DEFAULT 10 NOT NULL,
    temperature DECIMAL(3,2) DEFAULT 0.7 NOT NULL CHECK (temperature >= 0.0 AND temperature <= 2.0),
    enabled_tools JSONB DEFAULT '["agendar_cita", "consultar_disponibilidad", "obtener_citas_cliente", "cancelar_cita"]' NOT NULL,
    calendar_enabled BOOLEAN DEFAULT FALSE NOT NULL,
    google_calendar_id VARCHAR(255),
    calendar_timezone VARCHAR(50) DEFAULT 'America/Guayaquil',
    calendar_mapping JSONB DEFAULT '{}',
    global_agent_enabled BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_project_agent_config_project UNIQUE (project_id)
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_project_agent_configs_project ON project_agent_configs(project_id);

-- Trigger
DROP TRIGGER IF EXISTS trigger_update_project_agent_configs_updated_at ON project_agent_configs;
CREATE TRIGGER trigger_update_project_agent_configs_updated_at
    BEFORE UPDATE ON project_agent_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_projects_updated_at();

-- ============================================
-- 3. TABLA: agent_toggles
-- ============================================
CREATE TABLE IF NOT EXISTS agent_toggles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
    toggled_by VARCHAR(100),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_agent_toggle_conversation UNIQUE (conversation_id)
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_agent_toggles_conversation ON agent_toggles(conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_toggles_project ON agent_toggles(project_id);

-- Trigger
DROP TRIGGER IF EXISTS trigger_update_agent_toggles_updated_at ON agent_toggles;
CREATE TRIGGER trigger_update_agent_toggles_updated_at
    BEFORE UPDATE ON agent_toggles
    FOR EACH ROW
    EXECUTE FUNCTION update_projects_updated_at();

-- ============================================
-- 4. TABLA: users
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'agent' NOT NULL CHECK (role IN ('admin', 'manager', 'agent', 'viewer')),
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_user_email UNIQUE (email)
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- Trigger
DROP TRIGGER IF EXISTS trigger_update_users_updated_at ON users;
CREATE TRIGGER trigger_update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_projects_updated_at();

-- ============================================
-- 5. TABLA: user_projects (many-to-many)
-- ============================================
CREATE TABLE IF NOT EXISTS user_projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role_in_project VARCHAR(50) DEFAULT 'member' NOT NULL CHECK (role_in_project IN ('admin', 'manager', 'agent', 'viewer')),
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_user_project UNIQUE (user_id, project_id)
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_user_projects_user ON user_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_user_projects_project ON user_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_user_projects_role ON user_projects(role_in_project);

-- ============================================
-- 6. MODIFICAR TABLA: conversations (añadir project_id)
-- ============================================
-- Añadir columnas
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_enabled BOOLEAN DEFAULT TRUE;

-- Crear índice
CREATE INDEX IF NOT EXISTS idx_conversations_project_id ON conversations(project_id);

-- Añadir constraintUnique después de populate data
-- (Se llenará en script de seed, luego se activa)
-- ALTER TABLE conversations ADD CONSTRAINT uq_conversation_project_phone UNIQUE (project_id, phone_number);

-- ============================================
-- 7. MODIFICAR TABLA: messages (añadir project_id)
-- ============================================
ALTER TABLE messages ADD COLUMN IF NOT EXISTS project_id UUID;

-- Crear índice
CREATE INDEX IF NOT EXISTS idx_messages_project_id ON messages(project_id);

-- ============================================
-- 8. MODIFICAR TABLA: appointments (añadir project_id)
-- ============================================
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS project_id UUID;

-- Crear índice
CREATE INDEX IF NOT EXISTS idx_appointments_project_id ON appointments(project_id);

-- ============================================
-- 9. MODIFICAR TABLA: tool_call_logs (añadir project_id)
-- ============================================
ALTER TABLE tool_call_logs ADD COLUMN IF NOT EXISTS project_id UUID;

-- Crear índice
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_project_id ON tool_call_logs(project_id);

-- ============================================
-- 10. MODIFICAR TABLA: langchain_memory (añadir project_id)
-- ============================================
ALTER TABLE langchain_memory ADD COLUMN IF NOT EXISTS project_id UUID;

-- Crear índice
CREATE INDEX IF NOT EXISTS idx_langchain_memory_project_id ON langchain_memory(project_id);

-- ============================================
-- COMENTARIOS
-- ============================================
COMMENT ON COLUMN conversations.project_id IS 'Proyecto al que pertenece esta conversación';
COMMENT ON COLUMN conversations.agent_enabled IS 'Si el agente está habilitado para esta conversación';
COMMENT ON COLUMN messages.project_id IS 'Proyecto al que pertenece este mensaje';
COMMENT ON COLUMN appointments.project_id IS 'Proyecto al que pertenece esta cita';
COMMENT ON COLUMN tool_call_logs.project_id IS 'Proyecto al que pertenece este tool call';
COMMENT ON COLUMN langchain_memory.project_id IS 'Proyecto al que pertenece esta memoria';

-- ============================================
-- FIN MIGRACIÓN
-- ============================================
-- NOTA: Después de ejecutar esta migración:
-- 1. Ejecutar script de seed para crear proyecto 'default' y asignar datos existentes
-- 2. Actualizar约束 Unique en conversations una vez poblado project_id
-- 3. Reindexar si es necesario
