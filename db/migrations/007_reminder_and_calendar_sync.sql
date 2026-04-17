-- Migration 007: Add reminder tracking and calendar sync support
-- Adds reminder_sent_at column for appointment reminders
-- Prepares infrastructure for bidirectional Google Calendar sync

ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_appointments_reminder
    ON appointments (appointment_date, reminder_sent_at, status)
    WHERE status = 'scheduled';
