-- ============================================================================
-- validate_rankings.sql — diagnostyka poprawności rankingów
--
-- Uruchomienie:
--   docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
--     < scripts/validate_rankings.sql
--
-- lub interaktywnie:
--   docker exec -it cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach
-- ============================================================================

\echo '=== 1. Pokrycie MV: ile rekordów ma national_number vs brak ==='
SELECT
    COUNT(*)                                                          AS total_mv,
    COUNT(sc.id)                                                      AS has_schedule,
    COUNT(CASE WHEN sc.national_number IS NOT NULL THEN 1 END)        AS has_national_number,
    COUNT(CASE WHEN sc.national_number IS     NULL THEN 1 END)        AS missing_national_number,
    ROUND(
        100.0 * COUNT(CASE WHEN sc.national_number IS NOT NULL THEN 1 END) / COUNT(*), 1
    )                                                                  AS coverage_pct
FROM mv_train_run_delays dr
LEFT JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                      AND sc.order_id       = dr.order_id
                      AND sc.operating_date = dr.operating_date;


\echo '=== 2. Top 30 all-time PRZED filtrowaniem (schedule + cancelled) ==='
SELECT
    dr.max_delay_min,
    dr.operating_date,
    sc.national_number,
    sc.train_name,
    CASE WHEN sc.national_number IS NULL THEN 'BRAK national_number' ELSE 'OK' END AS status,
    BOOL_AND(ss.is_cancelled) AS all_cancelled
FROM mv_train_run_delays dr
LEFT JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                      AND sc.order_id       = dr.order_id
                      AND sc.operating_date = dr.operating_date
LEFT JOIN station_stops ss ON ss.train_op_id = dr.latest_train_op_id
GROUP BY dr.max_delay_min, dr.operating_date, sc.national_number, sc.train_name
ORDER BY dr.max_delay_min DESC
LIMIT 30;


\echo '=== 3. Liczba kursów na pociąg w bieżącym miesiącu (top 20) ==='
-- Wyjaśnienie: > 1 kurs/dzień = pociąg kursuje wielokrotnie (różne order_id)
--              > liczba dni w miesiącu = wiele schedule_id dla tego samego numeru
SELECT
    sc.national_number,
    sc.train_name,
    COUNT(DISTINCT dr.operating_date)                AS distinct_days,
    COUNT(*)                                          AS total_trips,
    ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT dr.operating_date), 0), 2)
                                                      AS trips_per_day,
    COUNT(DISTINCT (dr.schedule_id, dr.order_id))     AS distinct_schedule_order_pairs
FROM mv_train_run_delays dr
JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                 AND sc.order_id       = dr.order_id
                 AND sc.operating_date = dr.operating_date
WHERE dr.operating_date >= date_trunc('month', CURRENT_DATE)
  AND dr.operating_date <  date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
  AND sc.national_number IS NOT NULL
GROUP BY sc.national_number, sc.train_name
ORDER BY total_trips DESC
LIMIT 20;


\echo '=== 4. Odwołane kursy: przez MV (stara metoda) vs train_operations (prawidłowa) ==='
WITH date_bounds AS (
    SELECT date_trunc('month', CURRENT_DATE)::date               AS month_start,
           (date_trunc('month', CURRENT_DATE) + INTERVAL '1 month')::date AS month_end
),
via_mv AS (
    SELECT c.name AS carrier_name, COUNT(*) AS cancelled_via_mv
    FROM date_bounds db, mv_train_run_delays dr
    JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                     AND sc.order_id       = dr.order_id
                     AND sc.operating_date = dr.operating_date
    JOIN station_stops ss ON ss.train_op_id = dr.latest_train_op_id
    LEFT JOIN carriers c ON c.code = sc.carrier_code
    WHERE dr.operating_date >= db.month_start
      AND dr.operating_date <  db.month_end
    GROUP BY c.name, dr.schedule_id, dr.order_id, dr.operating_date
    HAVING BOOL_AND(ss.is_cancelled) = TRUE
),
via_to AS (
    SELECT c.name AS carrier_name, COUNT(*) AS cancelled_direct
    FROM date_bounds db, train_operations to_
    JOIN schedules sc ON sc.schedule_id   = to_.schedule_id
                     AND sc.order_id       = to_.order_id
                     AND sc.operating_date = to_.operating_date
    JOIN station_stops ss ON ss.train_op_id = to_.id
    LEFT JOIN carriers c ON c.code = sc.carrier_code
    WHERE to_.operating_date >= db.month_start
      AND to_.operating_date <  db.month_end
    GROUP BY c.name, to_.schedule_id, to_.order_id, to_.operating_date
    HAVING BOOL_AND(ss.is_cancelled) = TRUE
),
mv_agg AS (SELECT carrier_name, SUM(cancelled_via_mv) AS cancelled_via_mv FROM via_mv GROUP BY carrier_name),
to_agg AS (SELECT carrier_name, SUM(cancelled_direct) AS cancelled_direct FROM via_to GROUP BY carrier_name)
SELECT
    COALESCE(mv.carrier_name, dir.carrier_name)    AS carrier_name,
    COALESCE(mv.cancelled_via_mv, 0)               AS mv_method,
    COALESCE(dir.cancelled_direct, 0)              AS direct_method,
    COALESCE(dir.cancelled_direct, 0)
        - COALESCE(mv.cancelled_via_mv, 0)         AS missing_from_mv
FROM mv_agg mv
FULL OUTER JOIN to_agg dir ON dir.carrier_name = mv.carrier_name
ORDER BY COALESCE(dir.cancelled_direct, 0) DESC;


\echo '=== 5. Weryfikacja top-10 all-time: ile odpadło przez filtrowanie ==='
WITH sampled AS (
    SELECT schedule_id, order_id, operating_date, max_delay_min, latest_train_op_id
    FROM mv_train_run_delays
    ORDER BY max_delay_min DESC
    LIMIT 100  -- weź top 100 żeby sprawdzić ile przeżyje filtrowanie do top 10
),
with_filters AS (
    SELECT
        dr.max_delay_min,
        sc.national_number,
        BOOL_AND(ss.is_cancelled) AS all_cancelled,
        CASE
            WHEN sc.national_number IS NULL THEN 'brak national_number'
            WHEN BOOL_AND(ss.is_cancelled)  THEN 'odwołany'
            ELSE 'ok'
        END AS filter_reason
    FROM sampled dr
    LEFT JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                          AND sc.order_id       = dr.order_id
                          AND sc.operating_date = dr.operating_date
    LEFT JOIN station_stops ss ON ss.train_op_id = dr.latest_train_op_id
    GROUP BY dr.max_delay_min, sc.national_number
)
SELECT filter_reason, COUNT(*) AS count
FROM with_filters
GROUP BY filter_reason
ORDER BY count DESC;


\echo '=== 6. Sanity check: czy trip_count > liczba_dni wskazuje na wielokrotne kursy? ==='
-- Dzień z największą liczbą kursów dla pociągu z najwyższym trip_count w miesiącu
WITH top_train AS (
    SELECT sc.national_number, SUM(1) AS trips
    FROM mv_train_run_delays dr
    JOIN schedules sc ON sc.schedule_id = dr.schedule_id
                     AND sc.order_id = dr.order_id
                     AND sc.operating_date = dr.operating_date
    WHERE dr.operating_date >= date_trunc('month', CURRENT_DATE)
      AND dr.operating_date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
      AND sc.national_number IS NOT NULL
    GROUP BY sc.national_number
    ORDER BY trips DESC
    LIMIT 1
)
SELECT
    sc.national_number,
    dr.operating_date,
    dr.schedule_id,
    dr.order_id,
    dr.max_delay_min
FROM mv_train_run_delays dr
JOIN schedules sc ON sc.schedule_id = dr.schedule_id
                 AND sc.order_id = dr.order_id
                 AND sc.operating_date = dr.operating_date
JOIN top_train t ON t.national_number = sc.national_number
WHERE dr.operating_date >= date_trunc('month', CURRENT_DATE)
  AND dr.operating_date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
ORDER BY dr.operating_date, dr.order_id;
