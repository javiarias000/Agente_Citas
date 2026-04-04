-- ============================================
-- Arcadium Automation - Perfiles de Usuario
-- Fecha: 2026-04-03
-- Descripción: Tabla user_profiles para memoria a largo plazo
-- ============================================

-- Habilitar extensiones (si no existen)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Tabla: user_profiles
-- ============================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number VARCHAR(20) NOT NULL UNIQUE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    preferences JSONB DEFAULT '{}',
    last_appointment TIMESTAMPTZ,
    last_appointment_service VARCHAR(100),
    notes TEXT,
    extracted_facts JSONB DEFAULT '{}',
    total_conversations INTEGER DEFAULT 0 NOT NULL,
    first_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Índices para user_profiles
CREATE INDEX IF NOT EXISTS idx_user_profiles_phone_number ON user_profiles(phone_number);
CREATE INDEX IF NOT EXISTS idx_user_profiles_project_id ON user_profiles(project_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_last_seen ON user_profiles(last_seen DESC);

-- ============================================
-- Trigger para updated_at
-- ============================================
CREATE OR REPLACE FUNCTION update_user_profiles_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER trigger_update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_user_profiles_updated_at();

-- ============================================
-- Función para cleanup de langchain_memory (TTL)
-- ============================================
CREATE OR REPLACE FUNCTION cleanup_old_memory()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
    expiry_hours INTEGER := 24; -- Por defecto, 24 horas (configurable)
BEGIN
    -- Contar registros a eliminar (log)
    SELECT COUNT(*) INTO deleted_count
    FROM langchain_memory
    WHERE created_at < NOW() - (expiry_hours * INTERVAL '1 hour');

    -- Eliminar registros antiguos
    DELETE FROM langchain_memory
    WHERE created_at < NOW() - (expiry_hours * INTERVAL '1 hour');

    -- Log del cleanup
    RAISE NOTICE 'Cleanup de langchain_memory: % registros eliminados (TTL: % horas)', deleted_count, expiry_hours;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Comentarios de documentación
-- ============================================
COMMENT ON TABLE user_profiles IS 'Perfil de usuario con memoria a largo plazo (semántica). Almacena preferencias, historial y hechos extraídos.';
COMMENT ON COLUMN user_profiles.phone_number IS 'Número de teléfono normalizado en formato E.164 (ej: +34612345678)';
COMMENT ON COLUMN user_profiles.preferences IS 'Preferencias del usuario en JSON (ej: {"service": "limpieza", "time_preference": "morning"})';
COMMENT ON COLUMN user_profiles.last_appointment IS 'Fecha y hora de la última cita agendada';
COMMENT ON COLUMN user_profiles.last_appointment_service IS 'Servicio de la última cita (ej: "extracción", "limpieza")';
COMMENT ON COLUMN user_profiles.notes IS 'Notas médicas o de preferencias (alergias, miedos, etc.)';
COMMENT ON COLUMN user_profiles.extracted_facts IS 'Hechos extraídos automáticamente de conversaciones (estructura libre)';
COMMENT ON COLUMN user_profiles.total_conversations IS 'Contador total de conversaciones mantenidas (para estadísticas)';
COMMENT ON COLUMN user_profiles.first_seen IS 'Timestamp de la primera vez que el usuario interactuó';
COMMENT ON COLUMN user_profiles.last_seen IS 'Timestamp de la última interacción (actualizado automáticamente)';
