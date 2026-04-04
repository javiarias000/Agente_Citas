-- ============================================
-- Migración 005: Tabla agent_states para State Machine
-- Fecha: 2026-04-03
-- Descripción: Almacena el estado de SupportState por sesión
-- ============================================

-- Crear tabla agent_states
CREATE TABLE IF NOT EXISTS agent_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id VARCHAR(255) NOT NULL,
    state JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_agent_states_session_id ON agent_states(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_states_updated_at ON agent_states(updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_states_project_session ON agent_states(project_id, session_id);

-- Trigger para actualizar updated_at automáticamente
CREATE OR REPLACE FUNCTION update_agent_states_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_agent_states_updated_at ON agent_states;

CREATE TRIGGER update_agent_states_updated_at
    BEFORE UPDATE ON agent_states
    FOR EACH ROW
    EXECUTE FUNCTION update_agent_states_updated_at();

-- Comentarios
COMMENT ON COLUMN agent_states.id IS 'ID único del estado';
COMMENT ON COLUMN agent_states.project_id IS 'Proyecto asociado (opcional, para multi-tenant)';
COMMENT ON COLUMN agent_states.session_id IS 'ID de sesión (teléfono normalizado o UUID)';
COMMENT ON COLUMN agent_states.state IS 'Estado completo de SupportState en formato JSON (current_step, intención, datos de cita, etc.)';
COMMENT ON COLUMN agent_states.created_at IS 'Timestamp de creación';
COMMENT ON COLUMN agent_states.updated_at IS 'Timestamp de última actualización (auto-updated)';

-- Políticas RLS (Row Level Security) si está habilitado
-- NOTA: Descomentar si se usa Supabase/PostgreSQL con RLS
-- ALTER TABLE agent_states ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "Users can view own agent states" ON agent_states
--     FOR SELECT USING (auth.uid() = project_id::text::uuid);
