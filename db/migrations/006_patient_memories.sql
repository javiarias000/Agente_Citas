-- ============================================
-- Migración 006: Tabla patient_memories
-- Fecha: 2026-04-12
-- Descripción: Memoria estructurada y tipada por paciente,
--              inspirada en el sistema de memoria de Claude Code.
--
-- Tipos:
--   user      → perfil permanente (alergias, preferencias, datos personales)
--   feedback  → correcciones y patrones de comportamiento detectados
--   project   → contexto de tratamientos en curso, notas clínicas
--   reference → punteros a sistemas externos (IDs de citas, eventos de calendario)
-- ============================================

CREATE TABLE IF NOT EXISTS patient_memories (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone        VARCHAR(20) NOT NULL,
    type         VARCHAR(20) NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    name         VARCHAR(100) NOT NULL,
    description  TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at   TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    -- Unicidad: un paciente no puede tener dos memorias con el mismo nombre
    UNIQUE (phone, name)
);

CREATE INDEX IF NOT EXISTS idx_patient_memories_phone      ON patient_memories(phone);
CREATE INDEX IF NOT EXISTS idx_patient_memories_phone_type ON patient_memories(phone, type);
CREATE INDEX IF NOT EXISTS idx_patient_memories_updated    ON patient_memories(updated_at);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_patient_memories_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_patient_memories_updated_at ON patient_memories;
CREATE TRIGGER trg_patient_memories_updated_at
    BEFORE UPDATE ON patient_memories
    FOR EACH ROW
    EXECUTE FUNCTION update_patient_memories_updated_at();

COMMENT ON TABLE patient_memories IS 'Memoria estructurada y tipada por paciente. Persistente entre sesiones.';
COMMENT ON COLUMN patient_memories.type IS 'user=perfil, feedback=preferencias detectadas, project=tratamientos, reference=IDs externos';
COMMENT ON COLUMN patient_memories.name IS 'Identificador corto único por paciente (e.g. "alergia_penicilina")';
COMMENT ON COLUMN patient_memories.description IS 'Una línea descriptiva para el índice rápido';
COMMENT ON COLUMN patient_memories.body IS 'Contenido completo de la memoria';
