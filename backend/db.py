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

-- 午休稀采样阈值：全库只存一行（id = 1）。
-- window_start/window_end：周期窗用本地 HH:MM（配合 tz_offset_minutes），
--   或填 ISO 绝对时刻表示一次性窗（用于测试“把窗盖住此刻/挪到夜间”）。
CREATE TABLE IF NOT EXISTS break_config (
    id integer PRIMARY KEY DEFAULT 1,
    window_start text NOT NULL DEFAULT '12:00',
    window_end text NOT NULL DEFAULT '14:00',
    tz_offset_minutes integer NOT NULL DEFAULT 480,
    recent_minutes integer NOT NULL DEFAULT 60,
    min_completed integer NOT NULL DEFAULT 3,
    lamp_on boolean NOT NULL DEFAULT false,
    updated_by text NOT NULL DEFAULT 'seed',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT break_config_singleton CHECK (id = 1)
);

-- 亮灯履历：只在灭 -> 亮的边沿写一条，灯亮期间重复判定不重复记。
CREATE TABLE IF NOT EXISTS break_lamp_history (
    id serial PRIMARY KEY,
    lit_at timestamptz NOT NULL,
    window_start text NOT NULL,
    window_end text NOT NULL,
    recent_minutes integer NOT NULL,
    min_completed integer NOT NULL,
    completed_count integer NOT NULL,
    reason text NOT NULL
);
"""
