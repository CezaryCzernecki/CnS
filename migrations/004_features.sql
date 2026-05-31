-- Faza 2.1 – widok zmaterializowany dla feature engineering modelu ML
--
-- Uwagi:
--   station_stops.station_id   INTEGER  → rzutowanie ::TEXT przy joinie z weather
--   weather_observations.station_id VARCHAR(20)
--   Widok tworzony WITH NO DATA (bezpieczne na zaludnionej bazie).
--   Indeks UNIQUE wymagany przez REFRESH CONCURRENTLY.
--   Odświeżanie: aplikacja wywołuje REFRESH MATERIALIZED VIEW CONCURRENTLY
--   po każdym save_snapshot() w tle (threading.Thread, daemon=True).

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_training_features AS
SELECT
  ss.id,
  ss.station_id,
  st.name                                          AS station_name,

  -- target variables
  ss.delay_departure_min,
  ss.delay_arrival_min,

  -- czas
  ss.planned_departure::date                       AS operating_date,
  EXTRACT(HOUR  FROM ss.planned_departure)::SMALLINT AS hour_of_day,
  EXTRACT(DOW   FROM ss.planned_departure)::SMALLINT AS day_of_week,
  EXTRACT(MONTH FROM ss.planned_departure)::SMALLINT AS month,

  -- kontekst kalendarzowy (strefa null = ogólnopolski, strefa B = default PKP)
  ce.day_type,
  ce_b.day_type                                    AS day_type_zone_b,

  -- opóźnienie propagacyjne: poprzedni przystanek tego samego pociągu
  LAG(ss.delay_departure_min) OVER (
    PARTITION BY ss.train_op_id
    ORDER BY ss.planned_sequence
  )                                                AS prev_stop_delay_min,

  -- pozycja na trasie
  ss.planned_sequence,
  ss.actual_sequence - ss.planned_sequence         AS sequence_delta,

  -- pogoda: najbliższa obserwacja (nie prognoza) <= planowany odjazd
  wo.temperature_c,
  wo.precipitation_mm,
  wo.wind_speed_kmh,
  wo.snowfall_cm,
  wo.visibility_m,
  wo.cloud_cover_pct,
  wo.weather_code,

  -- flagi pogodowe (progi empiryczne dla warunków ekstremalnych)
  (wo.snowfall_cm > 1)::BOOLEAN         AS is_snowing,
  (wo.precipitation_mm > 5)::BOOLEAN    AS is_heavy_rain,
  (wo.wind_speed_kmh > 70)::BOOLEAN     AS is_strong_wind,
  (wo.temperature_c < -10)::BOOLEAN     AS is_frost,
  (wo.visibility_m < 200)::BOOLEAN      AS is_dense_fog,

  -- metadane pociągu
  to_.train_status,
  snap.fetched_at                                  AS snapshot_time

FROM station_stops ss
JOIN train_operations to_       ON ss.train_op_id   = to_.id
JOIN operations_snapshots snap  ON to_.snapshot_id  = snap.id
LEFT JOIN stations st           ON ss.station_id     = st.station_id

-- LATERAL: najnowsza obserwacja pogodowa <= czas odjazdu, nie prognoza
LEFT JOIN LATERAL (
  SELECT
    temperature_c, precipitation_mm, wind_speed_kmh,
    snowfall_cm, visibility_m, cloud_cover_pct, weather_code
  FROM weather_observations wo2
  WHERE wo2.station_id   = ss.station_id::TEXT
    AND wo2.observed_at  <= ss.planned_departure
    AND wo2.is_forecast  = FALSE
  ORDER BY wo2.observed_at DESC
  LIMIT 1
) wo ON TRUE

-- kalendarz ogólnopolski (zone IS NULL = święta, wakacje)
LEFT JOIN calendar_events ce
  ON  ce.event_date = ss.planned_departure::date
  AND ce.zone IS NULL

-- kalendarz strefa B (większość głównych węzłów PKP)
LEFT JOIN calendar_events ce_b
  ON  ce_b.event_date = ss.planned_departure::date
  AND ce_b.zone = 'B'

WHERE ss.delay_departure_min IS NOT NULL
  AND to_.train_status IN ('C', 'P')

WITH NO DATA;

-- Indeks unikalny – wymagany przez REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS mv_training_features_id_idx
    ON mv_training_features (id);

CREATE INDEX IF NOT EXISTS mv_training_features_station_date_idx
    ON mv_training_features (station_id, operating_date);

CREATE INDEX IF NOT EXISTS mv_training_features_date_idx
    ON mv_training_features (operating_date);
