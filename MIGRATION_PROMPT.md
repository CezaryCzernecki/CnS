# Prompt do wklejenia w nowej sesji Claude

---

Pracuję nad projektem Python/PostgreSQL/Docker o nazwie **cyrk_na_szynach** — system
kolekcjonowania danych RT o opóźnieniach pociągów PKP PLK.

**Workflow tej sesji:**
- Kod piszemy tutaj, na lokalnym WSL (`/home/cezary/cns/CnS`)
- Zmiany wdrażamy przez `git push` → `git pull` na serwerze
- Komendy do wykonania NA SERWERZE zawsze oznaczaj blokiem `### SERWER` — skopiuję je ręcznie
- Komendy lokalne (testy, budowanie) wykonujesz sam

**Zanim zaczniesz, przeczytaj KONIECZNIE:**
1. `MIGRATION_HOT_COLD.md` — pełna strategia, schematy tabel, kod Python, SQL
2. `CLAUDE.md` — kontekst projektu, wzorce kodowania, krytyczne ustalenia
3. `cns/storage/postgres.py` — główny plik do modyfikacji
4. `cns/collector/collector.py` — drugi plik do modyfikacji
5. Pliki migracji w `migrations/` — żeby zrozumieć aktualny schemat i zależności

**Kontekst krytyczny:**
- Dysk serwera: 75 GB, wolne: ~5.3 GB
- Nie można powiększyć dysku
- Strona nieaktywna — można maksymalnie obciążyć maszynę, priorytet: SZYBKOŚĆ
- Dane historyczne (330M wierszy, 58 GB) muszą zostać zachowane dla ML
- Psycopg3, Poetry, PostgreSQL 16 w Dockerze, kontener: `cyrk-na-szynach-db`

---

## Cel: migracja hot/cold storage

Szczegóły w `MIGRATION_HOT_COLD.md`. Skrót:

| Tabela | Teraz | Po migracji |
|--------|-------|-------------|
| station_stops | 58 GB, 330M wierszy | usunięta |
| train_operations | 2.2 GB | usunięta |
| station_stops_hot | brak | ~50 MB, ostatnie 3 dni, UPSERT |
| station_stops_archive | brak | ~140 MB, reszta, tylko faktyczne dane |
| train_runs | brak | ~7 MB, 1 wiersz per kurs |

---

## KROK 0 — Audyt zależności (zacznij tutaj)

Przeczytaj wszystkie wymienione pliki. Następnie sprawdź lokalnie:

```bash
grep -rn "station_stops\|train_operations" migrations/ --include="*.sql" | grep -v "^Binary"
grep -rn "station_stops\|train_operations" cns/ --include="*.py"
```

Przedstaw mi:
1. Listę widoków i MV które referują `station_stops` lub `train_operations` (z numerami migracji)
2. Listę endpointów API które będą wymagały aktualizacji po zmianie schematu
3. Typ kolumny `order_id` w `train_operations` (INTEGER czy BIGINT?) — sprawdź w pliku migracji
4. Wszelkie niezgodności między kodem w `MIGRATION_HOT_COLD.md` a aktualnym stanem repo

Poczekaj na moje potwierdzenie przed przejściem do KROKU 1.

---

## KROK 1 — Nowe tabele SQL

Utwórz `migrations/022_hot_cold_storage.sql` na podstawie sekcji 3 dokumentu.
Uwzględnij poprawki wykryte w KROKU 0 (np. właściwy typ order_id).

Pokaż mi plik i poczekaj na potwierdzenie. Nie wykonuj jeszcze na serwerze.

---

## KROK 2 — Zmiany w Pythonie

Zmodyfikuj oba pliki zgodnie z sekcjami 5.1–5.3 dokumentu:

- `cns/storage/postgres.py`:
  - Nowa `save_snapshot()` — UPSERT na `train_runs` + `station_stops_hot`
  - Nowa `archive_hot_data()` — przepisuje dane >3 dni do archive, czyści hot
- `cns/collector/collector.py`:
  - `_last_archive` i `_archive_interval = 24 * 3600` w `__init__`
  - Wywołanie `archive_hot_data()` w `_tick()`

Po zmianach uruchom testy:
```bash
poetry run pytest -v
```

Pokaż mi diff i wynik testów. Poczekaj na potwierdzenie.

---

## KROK 3 — Widoki i MV (aktualizacja przed deployem)

Na podstawie listy z KROKU 0 zaktualizuj widoki i MV tak żeby po DROP TABLE nic nie padło.
Utwórz nową migrację `migrations/023_update_views.sql` z:

- Widokiem `v_station_stops` łączącym hot + archive (sekcja 5.5 dokumentu)
- Przepisanymi `v_active_delays`, `mv_training_features`, `mv_train_run_delays`,
  `mv_cancelled_runs` — żeby korzystały z nowych tabel zamiast `station_stops`

Pokaż mi SQL i poczekaj na potwierdzenie.

---

## KROK 4 — Deploy kodu

Gdy wszystkie zmiany zatwierdzone:

```bash
git add migrations/022_hot_cold_storage.sql migrations/023_update_views.sql \
        cns/storage/postgres.py cns/collector/collector.py
git commit -m "feat: hot/cold storage migration — station_stops_hot + archive"
git push
```

Następnie podaj mi blok do wykonania NA SERWERZE:

### SERWER
```bash
cd ~/app
git pull

# Zastosuj migracje
docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  < migrations/022_hot_cold_storage.sql

docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  < migrations/023_update_views.sql

# Zbuduj i wdróż nowe kontenery
docker compose build collector fastapi
docker compose up -d collector fastapi

# Weryfikacja — po ~20 min sprawdź czy dane trafiają do hot
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  -c "SELECT COUNT(*) FROM station_stops_hot;"
```

Poczekaj na moje potwierdzenie że dane pojawiają się w `station_stops_hot`.

---

## KROK 5 — Migracja historyczna (SZYBKA, serwis nieaktywny)

Przygotuj skrypt `scripts/migrate_historical.sh` zoptymalizowany pod szybkość:

**Strategia:** najpierw archiwizuj WSZYSTKIE dane naraz (jeden duży INSERT ~96 MB),
potem DELETE partiami po 3 dni + VACUUM po każdej partii.

```bash
#!/usr/bin/env bash
# scripts/migrate_historical.sh
# Uruchamiać NA SERWERZE. Migruje całą historię station_stops → archive.
set -euo pipefail

CONTAINER="cyrk-na-szynach-db"
PSQL="docker exec -i $CONTAINER psql -U cyrk_na_szynach -d cyrk_na_szynach"

# Ustaw agresywne parametry VACUUM dla tej sesji
PSQL_FAST="docker exec -i $CONTAINER psql -U cyrk_na_szynach -d cyrk_na_szynach \
  -c \"SET maintenance_work_mem='512MB'\" -c"

echo "=== FAZA 1: Upsert train_runs dla całej historii ==="
$PSQL <<'SQL'
SET work_mem = '256MB';
INSERT INTO train_runs (schedule_id, order_id, operating_date)
SELECT DISTINCT schedule_id::integer, order_id::bigint, operating_date
FROM train_operations
ON CONFLICT (schedule_id, order_id, operating_date) DO NOTHING;
SQL
echo "train_runs: OK"

echo "=== FAZA 2: Archiwizuj całą historię (jeden INSERT) ==="
echo "To może trwać 5-15 minut..."
$PSQL <<'SQL'
SET work_mem = '512MB';
INSERT INTO station_stops_archive
    (train_run_id, station_id, operating_date,
     actual_arrival, actual_departure,
     delay_arrival_min, delay_departure_min, is_cancelled)
SELECT DISTINCT ON (tr.id, ss.station_id)
    tr.id,
    ss.station_id,
    tr.operating_date,
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
WHERE (ss.actual_arrival IS NOT NULL
       OR ss.actual_departure IS NOT NULL
       OR ss.is_cancelled = TRUE)
ORDER BY tr.id, ss.station_id, to_.collected_at DESC
ON CONFLICT (operating_date, train_run_id, station_id) DO NOTHING;
SQL
echo "Archive INSERT: OK"
echo "Wierszy w archive:"
$PSQL -c "SELECT COUNT(*) FROM station_stops_archive;"

echo "=== FAZA 3: DELETE partiami po 3 dni + VACUUM ==="
# Pętla od najstarszego dnia do 3 dni temu
START_DATE="2026-05-30"
END_DATE=$(date -d "$(date +%Y-%m-%d) - 3 days" +%Y-%m-%d)

current="$START_DATE"
while [[ "$current" < "$END_DATE" ]]; do
    chunk_end=$(date -d "$current + 3 days" +%Y-%m-%d)
    if [[ "$chunk_end" > "$END_DATE" ]]; then chunk_end="$END_DATE"; fi

    echo "--- DELETE $current → $chunk_end ---"
    $PSQL <<SQL
DELETE FROM station_stops ss
USING train_operations to_
WHERE ss.train_op_id = to_.id
  AND to_.operating_date >= '$current'
  AND to_.operating_date < '$chunk_end';

DELETE FROM train_operations
WHERE operating_date >= '$current'
  AND operating_date < '$chunk_end';
SQL

    echo "VACUUM..."
    $PSQL -c "SET maintenance_work_mem='512MB'; VACUUM (ANALYZE, PARALLEL 3) station_stops;"
    $PSQL -c "VACUUM (ANALYZE) train_operations;"

    echo "Wolne miejsce:"
    df -h / | tail -1
    echo "Pozostało w station_stops:"
    $PSQL -c "SELECT COUNT(*) FROM station_stops;"

    current="$chunk_end"
done

echo "=== MIGRACJA HISTORYCZNA ZAKOŃCZONA ==="
df -h /
```

Podaj mi ten skrypt i poczekaj na moje potwierdzenie.

### SERWER (po potwierdzeniu)
```bash
chmod +x ~/app/scripts/migrate_historical.sh
bash ~/app/scripts/migrate_historical.sh 2>&1 | tee ~/app/logs/migration_$(date +%Y%m%d_%H%M).log
```

Wklej mi końcowy wynik (lub `tail -50` loga jeśli jest długi).

---

## KROK 6 — DROP starych tabel

Sprawdź że wszystko OK:

### SERWER
```bash
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach -c "
SELECT
  (SELECT MIN(operating_date) FROM station_stops_archive) AS archive_od,
  (SELECT MAX(operating_date) FROM station_stops_archive) AS archive_do,
  (SELECT COUNT(*) FROM station_stops_archive) AS archive_wierszy,
  (SELECT COUNT(*) FROM station_stops_hot) AS hot_wierszy,
  (SELECT COUNT(*) FROM station_stops) AS stara_pozostalo;"
```

Pokaż mi wynik. Jeśli `stara_pozostalo` = 0 lub zawiera tylko ostatnie 3 dni,
podaj mi dokładne komendy DROP i czekaj na moje słowo **"tak, usuwaj"**.

Po moim potwierdzeniu:

### SERWER
```bash
# Backup przed DROP
bash ~/app/scripts/backup_db.sh

# DROP
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach -c "
DROP TABLE station_stops CASCADE;
DROP TABLE train_operations CASCADE;"

# VACUUM FULL żeby zwrócić miejsce systemowi
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach -c "
VACUUM FULL VERBOSE station_stops_archive;
VACUUM FULL VERBOSE train_runs;"
```

---

## KROK 7 — Weryfikacja końcowa

### SERWER
```bash
# Rozmiary tabel
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach -c "
SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
FROM pg_class WHERE relkind IN ('r','m')
  AND relname NOT LIKE 'pg_%'
ORDER BY pg_total_relation_size(oid) DESC LIMIT 15;"

# Wolne miejsce
df -h /

# API działa?
curl -s http://localhost/api/delays/active | head -c 200
curl -s http://localhost/api/stats | python3 -m json.tool
```

Pokaż mi wyniki — sesja zakończona gdy rozmiary i API są OK.

---

## Zasady pracy w tej sesji

- Kod piszesz lokalnie na WSL, ja potwierdzam, potem `git push`
- Komendy do wykonania NA SERWERZE zawsze w bloku `### SERWER`
- Przed każdą destrukcyjną operacją (DELETE masowy, DROP) czekasz na moje jawne potwierdzenie
- Testy lokalne po każdej zmianie Pythonu
- Jeśli testy padają — napraw zanim przejdziesz dalej
- Strona nieaktywna → możesz proponować agresywne ustawienia (work_mem, parallel workers)

Zacznij od KROKU 0.
