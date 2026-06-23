#!/usr/bin/env bash
# scripts/migrate_historical.sh
# Migracja historyczna: station_stops + train_operations → station_stops_archive
#
# Podejście: dzień po dniu, każdy cykl zwalnia ~2.5 GB przed kolejnym.
# Bezpieczne do restartu: INSERT ON CONFLICT DO NOTHING, DELETE idempotentny.
#
# URUCHAMIAJ NA SERWERZE:
#   bash ~/app/scripts/migrate_historical.sh 2>&1 | tee ~/app/migrate_$(date +%Y%m%d_%H%M).log

set -euo pipefail

CONTAINER="cyrk-na-szynach-db"
PSQL="docker exec -i $CONTAINER psql -U cyrk_na_szynach -d cyrk_na_szynach"

START_DATE="${START_DATE:-2026-05-30}"
END_DATE="${END_DATE:-$(date -d "3 days ago" +%Y-%m-%d)}"
END_EXCLUSIVE=$(date -d "$END_DATE + 1 day" +%Y-%m-%d)

echo "========================================================"
echo "MIGRACJA HISTORYCZNA: $START_DATE → $END_DATE"
echo "Czas startu: $(date)"
echo "========================================================"

# ── FAZA 0: Stan przed migracją ──────────────────────────────────────────────
echo ""
echo "=== FAZA 0: Stan przed migracją ==="
$PSQL <<'SQL'
SELECT
    relname                                              AS tabela,
    pg_size_pretty(pg_total_relation_size(oid))         AS rozmiar
FROM pg_class
WHERE relname IN (
    'station_stops', 'train_operations', 'train_runs',
    'station_stops_hot', 'station_stops_archive'
)
ORDER BY pg_total_relation_size(oid) DESC;
SQL

echo ""
$PSQL -c "SELECT MIN(operating_date) AS od, MAX(operating_date) AS do, COUNT(DISTINCT operating_date) AS dni_lacznie FROM train_operations;"
echo "Wolne miejsce: $(df -h / | tail -1 | awk '{print $4}')"

# ── FAZA 1: Populacja train_runs dla całej historii ──────────────────────────
# Jeden zbiorczy UPSERT (małe dane ~20 MB) przed pętlą dzień-po-dniu.
echo ""
echo "=== FAZA 1: Upsert train_runs dla całej historii ==="
$PSQL <<'SQL'
SET work_mem = '256MB';
INSERT INTO train_runs (schedule_id, order_id, operating_date)
SELECT DISTINCT
    to_.schedule_id::integer,
    to_.order_id::bigint,
    to_.operating_date
FROM train_operations to_
WHERE to_.operating_date IS NOT NULL
ON CONFLICT (schedule_id, order_id, operating_date) DO NOTHING;
SQL
$PSQL -c "SELECT COUNT(*) AS train_runs_lacznie FROM train_runs;"

# ── FAZA 2: Dzień po dniu — archive → delete station_stops → delete train_ops → vacuum ──
echo ""
echo "=== FAZA 2: Migracja dzień po dniu ==="

current="$START_DATE"
day_num=0

while [[ "$current" < "$END_EXCLUSIVE" ]]; do
    day_num=$((day_num + 1))
    next=$(date -d "$current + 1 day" +%Y-%m-%d)

    echo ""
    echo "--- Dzień $day_num: $current ---"
    echo "  Wolne przed: $(df -h / | tail -1 | awk '{print $4}')"

    # 2a. INSERT do station_stops_archive
    # DISTINCT ON (tr.id, ss.station_id) → jeden wiersz per kurs×stacja.
    # Preferujemy wiersz z actual_departure (ostatnie dane z najnowszego snapshotu).
    # Filtr: tylko wiersze z faktycznymi pomiarami lub odwołaniem.
    $PSQL <<SQL
SET work_mem = '512MB';
INSERT INTO station_stops_archive
    (train_run_id, station_id, operating_date,
     actual_arrival, actual_departure,
     delay_arrival_min, delay_departure_min, is_cancelled)
SELECT DISTINCT ON (tr.id, ss.station_id)
    tr.id,
    ss.station_id,
    '$current'::date AS operating_date,
    ss.actual_arrival,
    ss.actual_departure,
    ss.delay_arrival_min::smallint,
    ss.delay_departure_min::smallint,
    ss.is_cancelled
FROM station_stops ss
JOIN train_operations to_ ON ss.train_op_id = to_.id
JOIN train_runs tr ON (
    tr.schedule_id = to_.schedule_id
    AND tr.order_id = to_.order_id
    AND tr.operating_date = to_.operating_date
)
WHERE to_.operating_date = '$current'::date
  AND ss.station_id IS NOT NULL
  AND (   ss.actual_arrival    IS NOT NULL
       OR ss.actual_departure  IS NOT NULL
       OR ss.is_cancelled       = TRUE)
ORDER BY tr.id, ss.station_id,
         ss.actual_departure DESC NULLS LAST,
         ss.id DESC
ON CONFLICT (operating_date, train_run_id, station_id) DO NOTHING;
SQL
    echo "  [1/4] archive INSERT: OK"

    # 2b. DELETE station_stops dla tego dnia (przez JOIN na train_operations)
    $PSQL <<SQL
DELETE FROM station_stops ss
USING train_operations to_
WHERE ss.train_op_id = to_.id
  AND to_.operating_date = '$current'::date;
SQL
    echo "  [2/4] station_stops DELETE: OK"

    # 2c. DELETE train_operations dla tego dnia (FK ON DELETE CASCADE obsługuje resztę)
    $PSQL -c "DELETE FROM train_operations WHERE operating_date = '$current'::date;"
    echo "  [3/4] train_operations DELETE: OK"

    # 2d. VACUUM — fizyczne zwolnienie miejsca na dysku (kluczowe dla pętli)
    $PSQL \
        -c "SET maintenance_work_mem = '512MB'" \
        -c "VACUUM ANALYZE station_stops" \
        -c "VACUUM ANALYZE train_operations"
    echo "  [4/4] VACUUM: OK"

    echo "  Wolne po:    $(df -h / | tail -1 | awk '{print $4}')"
    remaining=$($PSQL -t -c "SELECT COUNT(DISTINCT operating_date) FROM train_operations;" | tr -d ' \n')
    echo "  Pozostało dni w train_operations: $remaining"

    current="$next"
done

# ── FAZA 3: Wynik końcowy ─────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "MIGRACJA ZAKOŃCZONA: $(date)"
echo "========================================================"

$PSQL <<'SQL'
SELECT
    relname                                              AS tabela,
    pg_size_pretty(pg_total_relation_size(oid))         AS rozmiar
FROM pg_class
WHERE relname IN (
    'station_stops', 'train_operations', 'train_runs',
    'station_stops_hot', 'station_stops_archive'
)
ORDER BY pg_total_relation_size(oid) DESC;
SQL

echo ""
df -h /
echo ""
echo "Następny krok (KROK 6):"
echo "  DROP TABLE station_stops CASCADE;"
echo "  DROP TABLE train_operations CASCADE;"
echo "  VACUUM FULL train_runs;"
