-- ============================================================
-- TorAlert – migracja 002: TimescaleDB (opcjonalna)
-- Bezpiecznie pomijana gdy rozszerzenie niedostępne (postgres:16-alpine).
-- Używaj timescale/timescaledb:latest-pg16 aby w pełni aktywować.
-- ============================================================

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    PERFORM create_hypertable(
        'station_stops', 'planned_departure',
        chunk_time_interval => INTERVAL '1 day',
        if_not_exists => TRUE, migrate_data => TRUE
    );
    PERFORM create_hypertable(
        'operations_snapshots', 'fetched_at',
        chunk_time_interval => INTERVAL '7 days',
        if_not_exists => TRUE, migrate_data => TRUE
    );
    PERFORM add_retention_policy('station_stops',        INTERVAL '90 days',  if_not_exists => TRUE);
    PERFORM add_retention_policy('operations_snapshots', INTERVAL '365 days', if_not_exists => TRUE);
    ALTER TABLE station_stops SET (
        timescaledb.compress,
        timescaledb.compress_orderby  = 'planned_departure DESC',
        timescaledb.compress_segmentby = 'station_id'
    );
    PERFORM add_compression_policy('station_stops', INTERVAL '7 days', if_not_exists => TRUE);
  ELSE
    RAISE NOTICE 'TimescaleDB niedostępne — pomijam migrację 002';
  END IF;
END;
$$;
