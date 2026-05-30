-- ============================================================
-- TorAlert – migracja 002: TimescaleDB (opcjonalna)
-- Uruchom tylko jeśli masz TimescaleDB:
--   docker run timescale/timescaledb:latest-pg16
-- db-init pominie ten plik jeśli rozszerzenie nie jest dostępne.
-- ============================================================

BEGIN;

SELECT create_hypertable(
    'station_stops', 'planned_departure',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE, migrate_data => TRUE
);

SELECT create_hypertable(
    'operations_snapshots', 'fetched_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE, migrate_data => TRUE
);

SELECT add_retention_policy('station_stops',       INTERVAL '90 days',  if_not_exists => TRUE);
SELECT add_retention_policy('operations_snapshots', INTERVAL '365 days', if_not_exists => TRUE);

ALTER TABLE station_stops SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'planned_departure DESC',
    timescaledb.compress_segmentby = 'station_id'
);
SELECT add_compression_policy('station_stops', INTERVAL '7 days', if_not_exists => TRUE);

COMMIT;
