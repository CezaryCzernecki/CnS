BEGIN;

ALTER TABLE disruptions
    ADD COLUMN IF NOT EXISTS disruption_type_code VARCHAR(50),
    ADD COLUMN IF NOT EXISTS start_station_id INTEGER REFERENCES stations(station_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS end_station_id INTEGER REFERENCES stations(station_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS has_bus_replacement BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_disruptions_bus_replacement
    ON disruptions (has_bus_replacement)
    WHERE has_bus_replacement;

COMMIT;
