BEGIN;

CREATE TABLE IF NOT EXISTS stations (
    station_id   INTEGER      PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    short_name   VARCHAR(100),
    latitude     FLOAT,
    longitude    FLOAT,
    synced_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS carriers (
    code         VARCHAR(20)  PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    synced_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commercial_categories (
    symbol       VARCHAR(20)  PRIMARY KEY,
    name         VARCHAR(200) NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id                      BIGSERIAL    PRIMARY KEY,
    schedule_id             INTEGER      NOT NULL,
    order_id                BIGINT       NOT NULL,
    carrier_code            VARCHAR(20)  REFERENCES carriers(code) ON DELETE SET NULL,
    national_number         VARCHAR(20),
    commercial_category     VARCHAR(20)  REFERENCES commercial_categories(symbol) ON DELETE SET NULL,
    operating_date          DATE         NOT NULL,
    fetched_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (schedule_id, order_id, operating_date)
);

CREATE INDEX IF NOT EXISTS idx_schedules_lookup
    ON schedules (schedule_id, order_id, operating_date);
CREATE INDEX IF NOT EXISTS idx_schedules_carrier
    ON schedules (carrier_code, operating_date);

CREATE TABLE IF NOT EXISTS schedule_stops (
    id              BIGSERIAL    PRIMARY KEY,
    schedule_id     BIGINT       NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    station_id      INTEGER      NOT NULL REFERENCES stations(station_id) ON DELETE RESTRICT,
    order_number    INTEGER      NOT NULL,
    arrival_time    TIME,
    departure_time  TIME,
    platform        VARCHAR(20),
    UNIQUE (schedule_id, order_number)
);

CREATE INDEX IF NOT EXISTS idx_schedule_stops_station
    ON schedule_stops (station_id);

CREATE TABLE IF NOT EXISTS operations_snapshots (
    id              BIGSERIAL    PRIMARY KEY,
    data_version    VARCHAR(100),
    fetched_at      TIMESTAMPTZ  NOT NULL,
    total_trains    INTEGER      NOT NULL DEFAULT 0,
    total_stops     INTEGER      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at
    ON operations_snapshots (fetched_at DESC);

CREATE TABLE IF NOT EXISTS train_operations (
    id              BIGSERIAL    PRIMARY KEY,
    snapshot_id     BIGINT       NOT NULL REFERENCES operations_snapshots(id) ON DELETE CASCADE,
    schedule_id     INTEGER      NOT NULL,
    order_id        BIGINT       NOT NULL,
    operating_date  DATE,
    train_status    CHAR(1)      NOT NULL CHECK (train_status IN ('S','P','C','X','Q')),
    collected_at    TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_train_ops_snapshot
    ON train_operations (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_train_ops_lookup
    ON train_operations (schedule_id, order_id, operating_date);
CREATE INDEX IF NOT EXISTS idx_train_ops_status
    ON train_operations (train_status, collected_at DESC);

CREATE TABLE IF NOT EXISTS station_stops (
    id                      BIGSERIAL   PRIMARY KEY,
    train_op_id             BIGINT      NOT NULL REFERENCES train_operations(id) ON DELETE CASCADE,
    station_id              INTEGER     REFERENCES stations(station_id) ON DELETE SET NULL,
    planned_sequence        INTEGER     NOT NULL,
    actual_sequence         INTEGER     NOT NULL,
    planned_arrival         TIMESTAMPTZ,
    actual_arrival          TIMESTAMPTZ,
    planned_departure       TIMESTAMPTZ,
    actual_departure        TIMESTAMPTZ,
    delay_arrival_min       INTEGER,
    delay_departure_min     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_station_stops_station_time
    ON station_stops (station_id, planned_departure DESC);
CREATE INDEX IF NOT EXISTS idx_station_stops_delay
    ON station_stops (delay_departure_min, planned_departure DESC)
    WHERE delay_departure_min IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_station_stops_train_op
    ON station_stops (train_op_id);

CREATE TABLE IF NOT EXISTS disruptions (
    id              BIGSERIAL   PRIMARY KEY,
    disruption_id   INTEGER     NOT NULL,
    message         TEXT,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    collected_date  DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (disruption_id, collected_date)
);

CREATE INDEX IF NOT EXISTS idx_disruptions_collected
    ON disruptions (collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_disruptions_api_id
    ON disruptions (disruption_id);

CREATE TABLE IF NOT EXISTS disruption_affected_routes (
    id              BIGSERIAL   PRIMARY KEY,
    disruption_id   BIGINT      NOT NULL REFERENCES disruptions(id) ON DELETE CASCADE,
    schedule_id     INTEGER     NOT NULL,
    order_id        BIGINT      NOT NULL,
    operating_date  DATE,
    station_id      INTEGER     REFERENCES stations(station_id) ON DELETE SET NULL,
    sequence_number INTEGER
);

CREATE INDEX IF NOT EXISTS idx_disruption_routes_disruption
    ON disruption_affected_routes (disruption_id);
CREATE INDEX IF NOT EXISTS idx_disruption_routes_station
    ON disruption_affected_routes (station_id);

CREATE OR REPLACE VIEW v_active_delays AS
SELECT
    ss.station_id,
    st.name                     AS station_name,
    to_.schedule_id,
    to_.order_id,
    to_.operating_date,
    ss.planned_departure,
    ss.actual_departure,
    ss.delay_departure_min,
    ss.delay_arrival_min,
    snap.fetched_at             AS snapshot_time
FROM station_stops ss
JOIN train_operations to_       ON ss.train_op_id = to_.id
JOIN operations_snapshots snap  ON to_.snapshot_id = snap.id
LEFT JOIN stations st           ON ss.station_id = st.station_id
WHERE to_.train_status = 'P'
  AND ss.delay_departure_min IS NOT NULL
ORDER BY snap.fetched_at DESC, ss.delay_departure_min DESC;

CREATE OR REPLACE VIEW v_station_delay_stats AS
SELECT
    ss.station_id,
    st.name                             AS station_name,
    COUNT(*)                            AS total_stops,
    COUNT(ss.delay_departure_min)       AS stops_with_data,
    SUM(CASE WHEN ss.delay_departure_min > 0 THEN 1 ELSE 0 END) AS delayed_count,
    ROUND(AVG(ss.delay_departure_min) FILTER (WHERE ss.delay_departure_min > 0), 1) AS avg_delay_min,
    MAX(ss.delay_departure_min)         AS max_delay_min,
    ROUND(
        100.0 * SUM(CASE WHEN ss.delay_departure_min > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(ss.delay_departure_min), 0), 1
    )                                   AS delay_rate_pct
FROM station_stops ss
JOIN train_operations to_   ON ss.train_op_id = to_.id
LEFT JOIN stations st       ON ss.station_id = st.station_id
WHERE to_.collected_at >= NOW() - INTERVAL '7 days'
  AND ss.delay_departure_min IS NOT NULL
GROUP BY ss.station_id, st.name
HAVING COUNT(*) >= 10
ORDER BY avg_delay_min DESC NULLS LAST;

COMMIT;
