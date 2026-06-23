#!/usr/bin/env bash
# scripts/migrate_historical.sh
# Migracja historyczna: station_stops + train_operations → station_stops_archive
#
# Strategia: TYLKO archiwizacja (bez DELETE w pętli).
# DROP TABLE na końcu (KROK 6) — jedyne podejście które fizycznie zwalnia miejsce na dysku.
#
# Dlaczego nie DELETE+VACUUM w pętli:
#   - DELETE+VACUUM nie zwraca miejsca do OS — tylko oznacza strony jako wolne wewnątrz pliku.
#   - Fizyczne miejsce wraca do OS tylko przez DROP TABLE lub VACUUM FULL (wymaga 2× miejsca).
#   - Każdy INSERT do archive (+5 MB) bez realnego zwolnienia → dysk idzie w dół.
#
# Bezpieczne do restartu: INSERT ON CONFLICT DO NOTHING.
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
echo "MIGRACJA HISTORYCZNA (archive-only): $START_DATE → $END_DATE"
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
$PSQL -c "SELECT MIN(operating_date) AS od, MAX(operating_date) AS do, COUNT(DISTINCT operating_date) AS dni_w_train_operations FROM train_operations;"
echo ""
$PSQL -c "SELECT MIN(operating_date) AS od, MAX(operating_date) AS do, COUNT(DISTINCT operating_date) AS dni_w_archive FROM station_stops_archive;"
echo "Wolne miejsce: $(df -h / | tail -1 | awk '{print $4}')"

# ── FAZA 1: Populacja train_runs dla pozostałej historii ─────────────────────
# Idempotentny: ON CONFLICT DO NOTHING — można restartować.
echo ""
echo "=== FAZA 1: Upsert train_runs ==="
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

# ── FAZA 2: Archiwizacja dzień po dniu (TYLKO INSERT, bez DELETE) ─────────────
echo ""
echo "=== FAZA 2: Archiwizacja dzień po dniu ==="
echo "    (bez DELETE — dysk nie zmniejszy się do KROK 6)"

current="$START_DATE"
day_num=0
total_archived=0

while [[ "$current" < "$END_EXCLUSIVE" ]]; do
    day_num=$((day_num + 1))
    next=$(date -d "$current + 1 day" +%Y-%m-%d)

    # INSERT do station_stops_archive
    # DISTINCT ON (tr.id, ss.station_id) → 1 wiersz per kurs×stacja.
    # ON CONFLICT DO NOTHING → bezpieczny restart, pomija już zarchiwizowane dni.
    inserted=$($PSQL -t <<SQL
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
    )
    # wyciągnij liczbę z "INSERT 0 N"
    n=$(echo "$inserted" | grep -oP 'INSERT 0 \K\d+' || echo "0")
    total_archived=$((total_archived + n))
    echo "  Dzień $day_num ($current): +${n} wierszy w archive (łącznie: $total_archived)"

    current="$next"
done

# ── FAZA 3: Podsumowanie ──────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "ARCHIWIZACJA ZAKOŃCZONA: $(date)"
echo "Łącznie zarchiwizowane wiersze: $total_archived"
echo "========================================================"

echo ""
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
$PSQL -c "SELECT COUNT(DISTINCT operating_date) AS dni_w_archive FROM station_stops_archive;"
echo "Wolne miejsce: $(df -h / | tail -1 | awk '{print $4}')"

echo ""
echo "================================================================"
echo "NASTĘPNY KROK (KROK 6) — uruchom ręcznie po potwierdzeniu:"
echo ""
echo "  docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach << 'SQL'"
echo "  DROP TABLE station_stops CASCADE;"
echo "  DROP TABLE train_operations CASCADE;"
echo "  SQL"
echo ""
echo "  DROP TABLE natychmiastowo zwalnia ~60 GB na dysku."
echo "================================================================"
