-- Widok zmaterializowany mv_train_run_delays
--
-- Pre-agreguje maksymalne opóźnienie per unikalny kurs pociągu
-- (schedule_id, order_id, operating_date) ze wszystkich snapshotów.
--
-- latest_train_op_id: MAX(train_op_id) spośród snapshotów z opóźnieniami.
-- BIGSERIAL jest sekwencyjny → wyższe ID = nowszy insert → dobry proxy
-- dla najnowszego snapshotu przy lookupie stacji.
--
-- Poprzednia wersja używała DISTINCT ON z ORDER BY (schedule_id, order_id,
-- operating_date, collected_at DESC) po ~9M wierszy bez pokrywającego indeksu,
-- co przy domyślnym work_mem=4MB powodowało wielokrotne spille na dysk.
-- Obecna wersja to pojedynczy GROUP BY agregujący station_stops raz.

SET work_mem = '256MB';

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_train_run_delays AS
SELECT
    to_.schedule_id,
    to_.order_id,
    to_.operating_date,
    MAX(ss.delay_departure_min) AS max_delay_min,
    MAX(ss.train_op_id)         AS latest_train_op_id
FROM station_stops ss
JOIN train_operations to_ ON ss.train_op_id = to_.id
WHERE ss.delay_departure_min > 0
GROUP BY to_.schedule_id, to_.order_id, to_.operating_date;

RESET work_mem;

-- Wymagany przez REFRESH CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS mv_train_run_delays_pk
    ON mv_train_run_delays (schedule_id, order_id, operating_date);

-- Ranking wszech czasów: ORDER BY max_delay_min DESC LIMIT N
CREATE INDEX IF NOT EXISTS mv_train_run_delays_delay_idx
    ON mv_train_run_delays (max_delay_min DESC);

-- Rankingi dzienne i miesięczne: WHERE operating_date = / BETWEEN
CREATE INDEX IF NOT EXISTS mv_train_run_delays_date_idx
    ON mv_train_run_delays (operating_date, max_delay_min DESC);
