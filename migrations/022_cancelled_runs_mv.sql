-- Widok zmaterializowany odwołanych kursów per (operating_date, carrier_code).
--
-- Cel: zastąpienie ciężkiego skanowania train_operations w czasie rzeczywistym
-- w endpointcie /rankings/monthly/carriers. MV przechowuje liczbę odwołanych
-- kursów (BOOL_AND(is_cancelled) = TRUE) per przewoźnik per dzień.
--
-- Odświeżanie: co godzinę razem z mv_train_run_delays (refresh_rankings).
--
-- Uwaga: używamy MAX(to_.id) jako proxy dla "najnowszego snapshotu" danego
-- kursu — identyczna strategia jak w mv_train_run_delays.

SET work_mem = '256MB';

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_cancelled_runs AS
WITH latest_per_run AS (
    SELECT
        schedule_id,
        order_id,
        operating_date,
        MAX(id) AS latest_train_op_id
    FROM train_operations
    GROUP BY schedule_id, order_id, operating_date
),
cancelled_runs AS (
    SELECT lpr.operating_date, sc.carrier_code
    FROM latest_per_run lpr
    JOIN schedules sc      ON sc.schedule_id   = lpr.schedule_id
                          AND sc.order_id       = lpr.order_id
                          AND sc.operating_date = lpr.operating_date
    JOIN station_stops ss  ON ss.train_op_id = lpr.latest_train_op_id
    GROUP BY lpr.operating_date, lpr.schedule_id, lpr.order_id, sc.carrier_code
    HAVING BOOL_AND(ss.is_cancelled) = TRUE
)
SELECT
    operating_date,
    carrier_code,
    COUNT(*) AS cancelled_count
FROM cancelled_runs
GROUP BY operating_date, carrier_code;

RESET work_mem;

-- Indeks unikalny wymagany do REFRESH CONCURRENTLY
-- NULLS NOT DISTINCT: PostgreSQL 15+ (używamy 16)
CREATE UNIQUE INDEX IF NOT EXISTS mv_cancelled_runs_pk
    ON mv_cancelled_runs (operating_date, carrier_code) NULLS NOT DISTINCT;

CREATE INDEX IF NOT EXISTS mv_cancelled_runs_date_idx
    ON mv_cancelled_runs (operating_date);
