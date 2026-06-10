-- Widok zmaterializowany mv_train_run_delays
--
-- Pre-agreguje maksymalne opóźnienie per unikalny kurs pociągu
-- (schedule_id, order_id, operating_date) ze wszystkich snapshotów.
-- Przechowuje też latest_train_op_id (najnowszy snapshot kursu) do
-- lookupów pierwszej/ostatniej stacji.
--
-- Odświeżany po każdym save_snapshot() przez PostgresStorage.refresh_rankings().
-- REFRESH CONCURRENTLY wymaga unikalnego indeksu – dodany poniżej.

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_train_run_delays AS
WITH run_delays AS (
    SELECT
        to_.schedule_id,
        to_.order_id,
        to_.operating_date,
        MAX(ss.delay_departure_min) AS max_delay_min
    FROM station_stops ss
    JOIN train_operations to_ ON ss.train_op_id = to_.id
    WHERE ss.delay_departure_min > 0
    GROUP BY to_.schedule_id, to_.order_id, to_.operating_date
),
latest_ops AS (
    SELECT DISTINCT ON (schedule_id, order_id, operating_date)
        id AS train_op_id,
        schedule_id,
        order_id,
        operating_date
    FROM train_operations
    ORDER BY schedule_id, order_id, operating_date, collected_at DESC
)
SELECT
    rd.schedule_id,
    rd.order_id,
    rd.operating_date,
    rd.max_delay_min,
    lo.train_op_id AS latest_train_op_id
FROM run_delays rd
JOIN latest_ops lo
    ON lo.schedule_id    = rd.schedule_id
   AND lo.order_id       = rd.order_id
   AND lo.operating_date = rd.operating_date;

-- Wymagany przez REFRESH CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS mv_train_run_delays_pk
    ON mv_train_run_delays (schedule_id, order_id, operating_date);

-- Ranking wszech czasów: ORDER BY max_delay_min DESC LIMIT N
CREATE INDEX IF NOT EXISTS mv_train_run_delays_delay_idx
    ON mv_train_run_delays (max_delay_min DESC);

-- Rankingi dzienne i miesięczne: WHERE operating_date = / BETWEEN
CREATE INDEX IF NOT EXISTS mv_train_run_delays_date_idx
    ON mv_train_run_delays (operating_date, max_delay_min DESC);
