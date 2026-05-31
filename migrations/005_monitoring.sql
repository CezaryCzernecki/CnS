-- Faza 5.1 – monitoring kolektora danych
-- Wypełniana co 5 min przez HealthChecker w DataCollector.

CREATE TABLE IF NOT EXISTS collector_health (
    id                      SERIAL      PRIMARY KEY,
    check_time              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_snapshot_at        TIMESTAMPTZ,
    minutes_since_snapshot  INTEGER,
    snapshots_last_24h      INTEGER     NOT NULL DEFAULT 0,
    expected_snapshots_24h  INTEGER     NOT NULL DEFAULT 96,
    gaps                    JSONB,      -- [{from_time, to_time, gap_minutes}]
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('OK','WARNING','CRITICAL'))
);

CREATE INDEX IF NOT EXISTS collector_health_check_time_idx
    ON collector_health (check_time DESC);
