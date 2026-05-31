-- Faza 1.1 – tabela obserwacji i prognoz pogodowych
-- Pobierane co 1h dla ~30 głównych węzłów PKP przez WeatherClient (Open-Meteo)

CREATE TABLE IF NOT EXISTS weather_observations (
    id              BIGSERIAL PRIMARY KEY,
    station_id      VARCHAR(20),
    observed_at     TIMESTAMPTZ NOT NULL,
    is_forecast     BOOLEAN NOT NULL DEFAULT FALSE,
    temperature_c   NUMERIC(5,2),
    precipitation_mm NUMERIC(6,2),
    wind_speed_kmh  NUMERIC(6,2),
    snowfall_cm     NUMERIC(6,2),
    visibility_m    INTEGER,
    cloud_cover_pct SMALLINT,
    weather_code    SMALLINT,
    collected_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (station_id, observed_at, is_forecast)
);

CREATE INDEX IF NOT EXISTS weather_observations_station_time_idx
    ON weather_observations (station_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS weather_observations_forecast_idx
    ON weather_observations (observed_at)
    WHERE is_forecast = TRUE;
