-- Diagnoza brakujących numerów pociągów w aktualnym snapshocie.
-- Uruchomienie:
--   docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
--     < scripts/diagnose_missing_train_numbers.sql

\echo '=== 1. AKTUALNY SNAPSHOT ==='
SELECT id, fetched_at, total_trains, total_stops
FROM operations_snapshots
ORDER BY fetched_at DESC
LIMIT 1;

\echo ''
\echo '=== 2. POKRYCIE SCHEDULES (pociągi status P/X z ostatniego snapshotu) ==='
WITH latest AS (
    SELECT id FROM operations_snapshots ORDER BY fetched_at DESC LIMIT 1
)
SELECT
    COUNT(*)                                                     AS total_trains,
    COUNT(sc.id)                                                 AS matched_schedules,
    COUNT(*) - COUNT(sc.id)                                      AS missing_schedules,
    ROUND(100.0 * COUNT(sc.id) / NULLIF(COUNT(*), 0), 1)        AS match_pct,
    COUNT(sc.national_number)                                    AS has_train_number,
    COUNT(*) - COUNT(sc.national_number)                         AS missing_train_number
FROM train_operations to_
JOIN latest ON to_.snapshot_id = latest.id
LEFT JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                       AND sc.order_id       = to_.order_id
                       AND sc.operating_date = to_.operating_date
WHERE to_.train_status IN ('P', 'X');

\echo ''
\echo '=== 3. PRZYKŁADY BEZ NUMERU POCIĄGU (pierwsze 15) ==='
WITH latest AS (
    SELECT id FROM operations_snapshots ORDER BY fetched_at DESC LIMIT 1
)
SELECT
    to_.schedule_id,
    to_.order_id,
    to_.operating_date,
    to_.train_status,
    CASE WHEN sc.id IS NULL THEN 'BRAK w schedules'
         WHEN sc.national_number IS NULL THEN 'national_number = NULL'
    END                         AS problem,
    sc.carrier_code,
    sc.commercial_category
FROM train_operations to_
JOIN latest ON to_.snapshot_id = latest.id
LEFT JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                       AND sc.order_id       = to_.order_id
                       AND sc.operating_date = to_.operating_date
WHERE to_.train_status IN ('P', 'X')
  AND sc.national_number IS NULL
ORDER BY to_.operating_date DESC, to_.schedule_id
LIMIT 15;

\echo ''
\echo '=== 4. RÓŻNE DATY OPERACYJNE W SNAPSHOCIE ==='
WITH latest AS (
    SELECT id FROM operations_snapshots ORDER BY fetched_at DESC LIMIT 1
)
SELECT
    to_.operating_date,
    COUNT(*)            AS trains,
    COUNT(sc.id)        AS matched
FROM train_operations to_
JOIN latest ON to_.snapshot_id = latest.id
LEFT JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                       AND sc.order_id       = to_.order_id
                       AND sc.operating_date = to_.operating_date
WHERE to_.train_status IN ('P', 'X')
GROUP BY to_.operating_date
ORDER BY to_.operating_date DESC;

\echo ''
\echo '=== 5. ROZKŁADY W BAZIE (ostatnie 3 dni) ==='
SELECT
    operating_date,
    COUNT(*)                    AS schedules,
    COUNT(national_number)      AS with_number,
    COUNT(*) - COUNT(national_number) AS without_number
FROM schedules
WHERE operating_date >= CURRENT_DATE - 2
GROUP BY operating_date
ORDER BY operating_date DESC;
