-- ============================================
-- Arcadium Automation - Migración 002
-- Fecha: 2025-04-03
-- Descripción: Agregar campos de Google Calendar a appointments
-- ============================================

-- Agregar columnas para Google Calendar sync
ALTER TABLE appointments
ADD COLUMN IF NOT EXISTS google_event_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS sync_status VARCHAR(50) DEFAULT 'synced' NOT NULL;

-- Comentarios
COMMENT ON COLUMN appointments.google_event_id IS 'ID del evento en Google Calendar';
COMMENT ON COLUMN appointments.sync_status IS 'Estado de sincronización: synced, pending, error';

-- Índice para búsquedas por google_event_id
CREATE INDEX IF NOT EXISTS idx_appointments_google_event_id ON appointments(google_event_id);

-- Índice para sync_status (para limpieza de pending)
CREATE INDEX IF NOT EXISTS idx_appointments_sync_status ON appointments(sync_status);
