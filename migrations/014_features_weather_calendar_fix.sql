-- Faza 2.1 – naprawa mv_training_features: dwa niezależne bugi
--
-- Bug #1 – pogoda nigdy nie trafiała do feature store:
--   Kolektor zapisywał wyłącznie get_forecast_48h() → is_forecast=TRUE.
--   LATERAL JOIN filtrował AND wo2.is_forecast = FALSE.
--   Efekt: wszystkie kolumny pogodowe w widoku = NULL.
--
--   Naprawka LATERAL: usunięcie filtra is_forecast=FALSE,
--   sortowanie ORDER BY is_forecast ASC (preferuje obserwacje), observed_at DESC.
--   Kolektor zmieniony na wywołanie get_current() (is_forecast=False) obok get_forecast_48h().
--
-- Bug #2 – niespójność trening/predykcja dla day_type:
--   calendar_events przechowuje tylko HOLIDAY / WINTER_BREAK / SUMMER_BREAK.
--   W feature store day_type=NULL dla WEEKEND i WORKING.
--   Endpoint /predict używa CalendarService.get_day_type() → zawsze konkretny string.
--   Model trenowany na NULL, inference dostaje non-NULL → niespójność train/predict.
--
--   Naprawka: COALESCE(ce.day_type, CASE EXTRACT(DOW) → WEEKEND/WORKING).
--   LONG_WEEKEND / HOLIDAY_EVE / HOLIDAY_RETURN nadal = NULL
--   (brak w calendar_events; drobna strata informacji, akceptowalna).

DROP MATERIALIZED VIEW IF EXISTS mv_training_features;

CREATE MATERIALIZED VIEW mv_training_features AS
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

  -- kontekst kalendarzowy
  -- NULL gdy nie ma wpisu w calendar_events → fallback do WEEKEND/WORKING z DOW
  COALESCE(
      ce.day_type,
      CASE WHEN EXTRACT(DOW FROM ss.planned_departure) IN (0, 6) THEN 'WEEKEND'
           ELSE 'WORKING'
      END
  )                                                AS day_type,

  COALESCE(
      ce_b.day_type,
      CASE WHEN EXTRACT(DOW FROM ss.planned_departure) IN (0, 6) THEN 'WEEKEND'
           ELSE 'WORKING'
      END
  )                                                AS day_type_zone_b,

  -- opóźnienie propagacyjne: poprzedni przystanek tego samego pociągu
  LAG(ss.delay_departure_min) OVER (
    PARTITION BY ss.train_op_id
    ORDER BY ss.planned_sequence
  )                                                AS prev_stop_delay_min,

  -- pozycja na trasie
  ss.planned_sequence,
  ss.actual_sequence - ss.planned_sequence         AS sequence_delta,

  -- pogoda: najbliższa obserwacja lub prognoza <= planowany odjazd
  -- ORDER BY is_forecast ASC → preferuje obserwacje (False=0) nad prognozami (True=1)
  -- Fallback na prognozę gdy brak obserwacji (np. dane historyczne sprzed naprawki)
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

-- LATERAL: najnowsza obserwacja lub prognoza <= czas odjazdu
-- is_forecast ASC: False(0) < True(1) → obserwacje preferowane nad prognozami
-- observed_at DESC: najnowszy czas gdy is_forecast jest równy
LEFT JOIN LATERAL (
  SELECT
    temperature_c, precipitation_mm, wind_speed_kmh,
    snowfall_cm, visibility_m, cloud_cover_pct, weather_code
  FROM weather_observations wo2
  WHERE wo2.station_id   = ss.station_id::TEXT
    AND wo2.observed_at  <= ss.planned_departure
  ORDER BY wo2.is_forecast ASC, wo2.observed_at DESC
  LIMIT 1
) wo ON TRUE

-- kalendarz ogólnopolski (zone IS NULL = święta, wakacje ogólnopolskie)
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
CREATE UNIQUE INDEX mv_training_features_id_idx
    ON mv_training_features (id);

CREATE INDEX mv_training_features_station_date_idx
    ON mv_training_features (station_id, operating_date);

CREATE INDEX mv_training_features_date_idx
    ON mv_training_features (operating_date);
