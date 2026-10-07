import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();

CREATE TABLE IF NOT EXISTS lunch_lamp_config (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    start_minutes integer NOT NULL,
    end_minutes integer NOT NULL,
    min_done integer NOT NULL,
    window_minutes integer NOT NULL,
    updated_by text,
    updated_at timestamptz
);
CREATE TABLE IF NOT EXISTS lunch_lamp_state (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    lit boolean NOT NULL DEFAULT false,
    evaluated_at timestamptz
);
CREATE TABLE IF NOT EXISTS lunch_lamp_events (
    id serial PRIMARY KEY,
    event text NOT NULL,
    done_count integer NOT NULL,
    in_window boolean NOT NULL,
    at timestamptz NOT NULL
);
INSERT INTO lunch_lamp_config (id, start_minutes, end_minutes, min_done, window_minutes)
VALUES (1, 690, 780, 3, 30)
ON CONFLICT (id) DO NOTHING;
INSERT INTO lunch_lamp_state (id, lit) VALUES (1, false)
ON CONFLICT (id) DO NOTHING;
"""
