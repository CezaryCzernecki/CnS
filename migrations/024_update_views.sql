-- Aktualizacja widoków i MV do architektury hot/cold storage.
--
-- Uruchom PO:  023_hot_cold_storage.sql
-- Uruchom PRZED: zatrzymaniem starych tabel (DROP TABLE).
--
-- Co się zmienia:
--   v_station_stops     — nowy widok: UNION ALL hot + archive
--   v_active_delays     — przepisany: station_stops_hot + train_runs zamiast
--                         station_stops + train_operations
--   mv_training_features — przepisany: station_stops_hot (ostatnie 3 dni);
--                          usunięto sequence_delta (brak actual_sequence w hot)
--   mv_train_run_delays  — przepisany: v_station_stops;
--                          latest_train_op_id → train_run_id
--   mv_cancelled_runs    — przepisany: train_runs + v_station_stops


-- ── 1. v_station_stops ───────────────────────────────────────────────────────
-- Widok łączący hot + archive — ujednolicony dostęp do danych historycznych.
-- Kolumny: podzbiór wspólny obu warstw (brak planned_*, planned_sequence z hot).

CREATE OR REPLACE VIEW v_station_stops AS
    SELECT
        train_run_id,
        station_id,
        operating_date,
        actual_arrival,
        actual_departure,
        delay_arrival_min,
        delay_departure_min,
        is_cancelled
    FROM station_stops_archive
    UNION ALL
    SELECT
        h.train_run_id,
        h.station_id,
        tr.operating_date,
        h.actual_arrival,
        h.actual_departure,
        h.delay_arrival_min,
        h.delay_departure_min,
        h.is_cancelled
    FROM station_stops_hot h
    JOIN train_runs tr ON h.train_run_id = tr.id;


-- ── 2. v_active_delays ───────────────────────────────────────────────────────
-- Przepisany na station_stops_hot + train_runs.
-- train_status = NULL (hot nie przechowuje per-snapshot statusu);
-- ActiveDelay.train_status: Optional[str] = None — kompatybilne z API.
-- "Aktywny" = kurs widziany w ciągu ostatnich 20 min od najnowszego last_seen_at.

DROP VIEW IF EXISTS v_active_delays;

CREATE VIEW v_active_delays AS
WITH latest_seen AS (
    SELECT MAX(last_seen_at) AS latest_at
    FROM station_stops_hot
),
active_runs AS (
    SELECT
        h.train_run_id,
        MAX(h.last_seen_at)      AS snapshot_time,
        BOOL_AND(h.is_cancelled) AS all_cancelled
    FROM station_stops_hot h, latest_seen ls
    WHERE h.last_seen_at >= ls.latest_at - INTERVAL '20 minutes'
    GROUP BY h.train_run_id
),
first_station AS (
    SELECT DISTINCT ON (h.train_run_id)
        h.train_run_id,
        st.name             AS station_name,
        h.planned_departure AS departure_time
    FROM station_stops_hot h
    JOIN active_runs ar ON ar.train_run_id = h.train_run_id
    LEFT JOIN stations st ON st.station_id = h.station_id
    ORDER BY h.train_run_id, h.planned_sequence ASC
),
last_station AS (
    SELECT DISTINCT ON (h.train_run_id)
        h.train_run_id,
        st.name           AS station_name,
        h.planned_arrival AS arrival_time
    FROM station_stops_hot h
    JOIN active_runs ar ON ar.train_run_id = h.train_run_id
    LEFT JOIN stations st ON st.station_id = h.station_id
    ORDER BY h.train_run_id, h.planned_sequence DESC
),
last_visited AS (
    SELECT DISTINCT ON (h.train_run_id)
        h.train_run_id,
        st.name          AS station_name,
        h.actual_arrival AS actual_arrival_time
    FROM station_stops_hot h
    JOIN active_runs ar ON ar.train_run_id = h.train_run_id
    LEFT JOIN stations st ON st.station_id = h.station_id
    WHERE h.actual_arrival IS NOT NULL
      AND h.actual_arrival <= (SELECT latest_at FROM latest_seen) + INTERVAL '2 hours'
    ORDER BY h.train_run_id, h.actual_arrival DESC
),
train_delay AS (
    SELECT
        h.train_run_id,
        MAX(h.delay_departure_min) AS delay_departure_min,
        MAX(h.delay_arrival_min)   AS delay_arrival_min
    FROM station_stops_hot h
    JOIN active_runs ar ON ar.train_run_id = h.train_run_id
    GROUP BY h.train_run_id
)
SELECT
    tr.schedule_id,
    tr.order_id,
    tr.operating_date,
    NULL::TEXT             AS train_status,
    ar.snapshot_time,
    sc.national_number     AS train_number,
    sc.train_name,
    c.name                 AS carrier_name,
    fs.station_name        AS first_station,
    fs.departure_time      AS first_station_departure,
    ls.station_name        AS last_station,
    ls.arrival_time        AS last_station_arrival,
    lv.station_name        AS last_visited_station,
    lv.actual_arrival_time AS last_visited_arrival,
    td.delay_departure_min,
    td.delay_arrival_min
FROM active_runs ar
JOIN train_runs tr         ON tr.id = ar.train_run_id
LEFT JOIN schedules sc     ON sc.schedule_id    = tr.schedule_id
                          AND sc.order_id        = tr.order_id
                          AND sc.operating_date  = tr.operating_date
LEFT JOIN carriers c       ON c.code = sc.carrier_code
LEFT JOIN first_station fs ON fs.train_run_id = ar.train_run_id
LEFT JOIN last_station ls  ON ls.train_run_id = ar.train_run_id
LEFT JOIN last_visited lv  ON lv.train_run_id = ar.train_run_id
LEFT JOIN train_delay td   ON td.train_run_id = ar.train_run_id
WHERE tr.operating_date >= CURRENT_DATE
  AND NOT COALESCE(ar.all_cancelled, FALSE)
ORDER BY td.delay_departure_min DESC NULLS LAST;


-- ── 3. mv_training_features ──────────────────────────────────────────────────
-- Przepisany na station_stops_hot (okno: ostatnie 3 dni, aktualizowane przez hot).
-- Zmiany względem 005/014:
--   - źródło: station_stops_hot zamiast station_stops × train_operations × snapshots
--   - UNIQUE KEY: station_stops_hot.id (BIGSERIAL, zawsze unikalny)
--   - snapshot_time: last_seen_at zamiast operations_snapshots.fetched_at
--   - usunięto sequence_delta (brak actual_sequence w station_stops_hot)

DROP MATERIALIZED VIEW IF EXISTS mv_training_features CASCADE;

CREATE MATERIALIZED VIEW mv_training_features AS
SELECT
    h.id,
    h.station_id,
    st.name                                           AS station_name,
    h.delay_departure_min,
    h.delay_arrival_min,
    h.planned_departure::date                         AS operating_date,
    EXTRACT(HOUR  FROM h.planned_departure)::SMALLINT AS hour_of_day,
    EXTRACT(DOW   FROM h.planned_departure)::SMALLINT AS day_of_week,
    EXTRACT(MONTH FROM h.planned_departure)::SMALLINT AS month,
    ce.day_type,
    ce_b.day_type                                     AS day_type_zone_b,
    LAG(h.delay_departure_min) OVER (
        PARTITION BY h.train_run_id
        ORDER BY h.planned_sequence
    )                                                 AS prev_stop_delay_min,
    h.planned_sequence,
    wo.temperature_c,
    wo.precipitation_mm,
    wo.wind_speed_kmh,
    wo.snowfall_cm,
    wo.visibility_m,
    wo.cloud_cover_pct,
    wo.weather_code,
    (wo.snowfall_cm > 1)::BOOLEAN         AS is_snowing,
    (wo.precipitation_mm > 5)::BOOLEAN    AS is_heavy_rain,
    (wo.wind_speed_kmh > 70)::BOOLEAN     AS is_strong_wind,
    (wo.temperature_c < -10)::BOOLEAN     AS is_frost,
    (wo.visibility_m < 200)::BOOLEAN      AS is_dense_fog,
    h.last_seen_at                                    AS snapshot_time
FROM station_stops_hot h
LEFT JOIN stations st ON h.station_id = st.station_id
LEFT JOIN LATERAL (
    SELECT temperature_c, precipitation_mm, wind_speed_kmh,
           snowfall_cm, visibility_m, cloud_cover_pct, weather_code
    FROM weather_observations wo2
    WHERE wo2.station_id  = h.station_id::TEXT
      AND wo2.observed_at <= h.planned_departure
    ORDER BY wo2.is_forecast ASC, wo2.observed_at DESC
    LIMIT 1
) wo ON TRUE
LEFT JOIN calendar_events ce
    ON ce.event_date = h.planned_departure::date
   AND ce.zone IS NULL
LEFT JOIN calendar_events ce_b
    ON ce_b.event_date = h.planned_departure::date
   AND ce_b.zone = 'B'
WHERE h.delay_departure_min IS NOT NULL
WITH NO DATA;

CREATE UNIQUE INDEX mv_training_features_id_idx
    ON mv_training_features (id);

CREATE INDEX mv_training_features_station_date_idx
    ON mv_training_features (station_id, operating_date);

CREATE INDEX mv_training_features_date_idx
    ON mv_training_features (operating_date);


-- ── 4. mv_train_run_delays ───────────────────────────────────────────────────
-- Przepisany na v_station_stops (hot + archive) zamiast station_stops.
-- ZMIANA KOLUMNY: latest_train_op_id → train_run_id
-- API (app.py) zaktualizowane odpowiednio.

DROP MATERIALIZED VIEW IF EXISTS mv_train_run_delays CASCADE;

SET work_mem = '256MB';

CREATE MATERIALIZED VIEW mv_train_run_delays AS
SELECT
    tr.schedule_id,
    tr.order_id,
    tr.operating_date,
    MAX(vss.delay_departure_min) AS max_delay_min,
    tr.id                        AS train_run_id
FROM v_station_stops vss
JOIN train_runs tr ON vss.train_run_id = tr.id
WHERE vss.delay_departure_min > 0
GROUP BY tr.id, tr.schedule_id, tr.order_id, tr.operating_date
WITH NO DATA;

RESET work_mem;

-- UNIQUE INDEX wymagany przez REFRESH CONCURRENTLY
CREATE UNIQUE INDEX mv_train_run_delays_pk
    ON mv_train_run_delays (schedule_id, order_id, operating_date);

CREATE INDEX mv_train_run_delays_delay_idx
    ON mv_train_run_delays (max_delay_min DESC);

CREATE INDEX mv_train_run_delays_date_idx
    ON mv_train_run_delays (operating_date, max_delay_min DESC);


-- ── 5. mv_cancelled_runs ─────────────────────────────────────────────────────
-- Przepisany na train_runs + v_station_stops.
-- Eliminuje zależność od train_operations i station_stops.

DROP MATERIALIZED VIEW IF EXISTS mv_cancelled_runs CASCADE;

SET work_mem = '256MB';

CREATE MATERIALIZED VIEW mv_cancelled_runs AS
WITH cancelled_runs AS (
    SELECT tr.operating_date, sc.carrier_code
    FROM train_runs tr
    JOIN schedules sc ON sc.schedule_id    = tr.schedule_id
                     AND sc.order_id       = tr.order_id
                     AND sc.operating_date = tr.operating_date
    JOIN v_station_stops vss ON vss.train_run_id = tr.id
    GROUP BY tr.id, tr.operating_date, sc.carrier_code
    HAVING BOOL_AND(vss.is_cancelled) = TRUE
)
SELECT
    operating_date,
    carrier_code,
    COUNT(*) AS cancelled_count
FROM cancelled_runs
GROUP BY operating_date, carrier_code
WITH NO DATA;

RESET work_mem;

CREATE UNIQUE INDEX mv_cancelled_runs_pk
    ON mv_cancelled_runs (operating_date, carrier_code) NULLS NOT DISTINCT;

CREATE INDEX mv_cancelled_runs_date_idx
    ON mv_cancelled_runs (operating_date);


-- ── 6. Pierwsze REFRESH (non-concurrent — hot jest puste, szybkie) ────────────
-- Po starcie kolektora kolejne odświeżenia używają REFRESH CONCURRENTLY.

REFRESH MATERIALIZED VIEW mv_training_features;
REFRESH MATERIALIZED VIEW mv_train_run_delays;
REFRESH MATERIALIZED VIEW mv_cancelled_runs;
