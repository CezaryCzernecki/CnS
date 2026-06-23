# Migracja hot/cold storage — station_stops

Dokument dla nowej sesji Claude. Zawiera pełny kontekst, schematy, kod i kolejność kroków.
Wygenerowany: 2026-06-23. Dysk na serwerze: 75 GB / 5.3 GB wolnych.

---

## 1. Kontekst i problem

System zbiera dane RT o opóźnieniach pociągów PKP PLK co 15 minut (`/operations` API).
Każdy snapshot wstawia ~150k nowych wierszy do `station_stops` (10k pociągów × ~15 przystanków).
Ten sam przystanek tego samego pociągu zapisywany jest 8–10 razy w kolejnych snapshotach
— tylko ostatni odczyt ma wartość (faktyczne opóźnienie po przejechaniu stacji).

**Stan na 2026-06-23:**

| Tabela | Wierszy | Rozmiar |
|--------|---------|---------|
| station_stops | 330 M | 58 GB |
| train_operations | — | 2.2 GB |
| disruption_affected_routes | — | 440 MB |
| schedule_stops | — | 357 MB |

Przyrost: **~2.5 GB/dzień**. Bez zmian dysk zapełni się ponownie za ~2 dni.

**Czego NIE robimy:** nie zmniejszamy częstotliwości kolekcji, nie usuwamy danych historycznych
(potrzebne do trenowania modelu ML). Nie powiększamy dysku — brak takiej możliwości.

---

## 2. Architektura docelowa

```
[PKP API co 15 min]
        │
   [Kolektor]
        │ UPSERT (1 wiersz per kurs × przystanek)
        ▼
[station_stops_hot]     ← ostatnie 3 dni, pełne kolumny, ~420k wierszy
        │
        │ codziennie ~03:00 (archival job)
        ▼
[station_stops_archive] ← starsze niż 3 dni, tylko faktyczne dane,
                          ~6 MB/dzień, partycjonowane po miesiącach
```

### Pomocnicza tabela train_runs

Zastępuje `train_operations` jako nośnik tożsamości kursu pociągu.
Jeden wiersz per unikatowy kurs `(schedule_id, order_id, operating_date)`.

---

## 3. Schematy nowych tabel (SQL)

Plik: `migrations/022_hot_cold_storage.sql`

```sql
-- ── train_runs ────────────────────────────────────────────────────────────────
-- 1 wiersz per unikatowy kurs pociągu (schedule_id + order_id + date).
-- Zastępuje per-snapshot train_operations jako klucz FK dla obu warstw.

CREATE TABLE IF NOT EXISTS train_runs (
    id             SERIAL PRIMARY KEY,
    schedule_id    INTEGER      NOT NULL,
    order_id       BIGINT       NOT NULL,
    operating_date DATE         NOT NULL,
    carrier_code   VARCHAR(20),
    train_name     VARCHAR(200),
    UNIQUE (schedule_id, order_id, operating_date)
);

CREATE INDEX IF NOT EXISTS idx_train_runs_date
    ON train_runs (operating_date);


-- ── station_stops_hot ────────────────────────────────────────────────────────
-- Ostatnie 3 dni. UPSERT: jeden wiersz per (kurs × przystanek), aktualizowany.
-- Pełne kolumny (w tym planned) — potrzebne do wyświetlania na stronie.

CREATE TABLE IF NOT EXISTS station_stops_hot (
    id                  BIGSERIAL PRIMARY KEY,
    train_run_id        INTEGER      NOT NULL REFERENCES train_runs(id),
    station_id          INTEGER      REFERENCES stations(station_id) ON DELETE SET NULL,
    planned_sequence    SMALLINT,
    planned_arrival     TIMESTAMPTZ,
    actual_arrival      TIMESTAMPTZ,
    planned_departure   TIMESTAMPTZ,
    actual_departure    TIMESTAMPTZ,
    delay_arrival_min   SMALLINT,
    delay_departure_min SMALLINT,
    is_confirmed        BOOLEAN      NOT NULL DEFAULT FALSE,
    is_cancelled        BOOLEAN      NOT NULL DEFAULT FALSE,
    last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (train_run_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_ssh_station_id
    ON station_stops_hot (station_id);
CREATE INDEX IF NOT EXISTS idx_ssh_last_seen
    ON station_stops_hot (last_seen_at);


-- ── station_stops_archive ────────────────────────────────────────────────────
-- Starsze niż 3 dni. Tylko faktyczne pomiary (actual IS NOT NULL lub cancelled).
-- Brak planned_arrival/departure — są w schedule_stops.
-- SMALLINT dla opóźnień (zakres ±32767 min — wystarczy z dużym zapasem).
-- Partycjonowana po miesiącach — DROP PARTITION zamiast DELETE dla starych danych.

CREATE TABLE IF NOT EXISTS station_stops_archive (
    train_run_id        INTEGER  NOT NULL,
    station_id          INTEGER,
    operating_date      DATE     NOT NULL,   -- denorm dla partycjonowania
    actual_arrival      TIMESTAMPTZ,
    actual_departure    TIMESTAMPTZ,
    delay_arrival_min   SMALLINT,
    delay_departure_min SMALLINT,
    is_cancelled        BOOLEAN  NOT NULL DEFAULT FALSE,
    PRIMARY KEY (operating_date, train_run_id, station_id)
) PARTITION BY RANGE (operating_date);

-- Partycje — twórz z góry, dodawaj nową każdy miesiąc
CREATE TABLE IF NOT EXISTS station_stops_archive_2026_05
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_06
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_07
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_08
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_09
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_10
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_11
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2026_12
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- BRIN zamiast B-tree — dane w archive są sequentially ordered, BRIN = <1% rozmiaru B-tree
CREATE INDEX IF NOT EXISTS idx_ssa_brin_date
    ON station_stops_archive USING BRIN (operating_date);
```

---

## 4. Strategia migracji (chunked — tylko 5.3 GB wolnych)

### Szacunki per dzień

| | Stara tabela | Archive |
|--|-------------|---------|
| Wierszy/dzień | ~14 M | ~140 k |
| Rozmiar/dzień | ~2.5 GB | ~6 MB |
| Po migracji 1 dnia | -2.5 GB (DELETE + VACUUM) | +6 MB |
| **Netto zwolnione** | **~2.5 GB** | |

### Kolejność kroków (wykonaj w tej kolejności)

#### KROK 0 — Utwórz nowe tabele

```bash
docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  < migrations/022_hot_cold_storage.sql
```

Nie dotyka starych tabel, tylko tworzy nowe. Bezpieczne, zajmuje <1 MB.

#### KROK 1 — Zmień kolektor (zatrzymaj zapis do starych tabel)

Zaktualizuj `cns/storage/postgres.py` — szczegóły w sekcji 5.
Zrób docker rebuild i deploy:

```bash
docker compose build collector fastapi
docker compose up -d collector fastapi
```

Od tego momentu nowe dane trafiają do `train_runs` + `station_stops_hot`.
Stare tabele przestają rosnąć.

#### KROK 2 — Migruj dane historyczne (dzień po dniu)

Wykonaj skrypt `scripts/migrate_chunk.sh` dla każdego dnia od najstarszego.
Skrypt opisany w sekcji 6.

Sprawdź ile masz wolnego miejsca po każdym dniu:

```bash
docker exec cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  -c "SELECT COUNT(*) FROM station_stops;"   # powinno maleć ~14M/dzień
df -h /
```

#### KROK 3 — Po zakończeniu migracji usuń stare tabele

Tylko gdy `station_stops` zawiera wyłącznie dane z ostatnich 3 dni
(które są już w `station_stops_hot`):

```sql
-- Upewnij się że nic nie zostało
SELECT MIN(planned_arrival::date), MAX(planned_arrival::date),
       COUNT(*) FROM station_stops;

-- Dopiero wtedy usuń
DROP TABLE station_stops CASCADE;
DROP TABLE train_operations CASCADE;
-- operations_snapshots zostaw — używane przez health monitoring
```

#### KROK 4 — Skonfiguruj nightly archival job

Dodaj wywołanie `archive_hot_data()` w kolektorze (sekcja 5)
lub ustaw cron na serwerze (sekcja 6).

---

## 5. Zmiany w Pythonie

### 5.1 Nowa metoda save_snapshot() w cns/storage/postgres.py

Zamień istniejącą metodę `save_snapshot()` na poniższą:

```python
def save_snapshot(self, snapshot: OperationsSnapshot) -> None:
    import time
    t0 = time.monotonic()

    # Zachowaj snapshot dla health monitoringu (bez zmian)
    snapshot_sql = """
        INSERT INTO operations_snapshots
            (data_version, fetched_at, total_trains, total_stops)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """

    # UPSERT train_runs — 1 wiersz per unikatowy kurs
    train_run_sql = """
        INSERT INTO train_runs (schedule_id, order_id, operating_date)
        VALUES (%s, %s, %s)
        ON CONFLICT (schedule_id, order_id, operating_date) DO NOTHING
        RETURNING id
    """
    train_run_select_sql = """
        SELECT id FROM train_runs
        WHERE schedule_id = %s AND order_id = %s AND operating_date = %s
    """

    # UPSERT station_stops_hot — aktualizuj actual times i delays
    hot_upsert_sql = """
        INSERT INTO station_stops_hot
            (train_run_id, station_id, planned_sequence,
             planned_arrival, actual_arrival,
             planned_departure, actual_departure,
             delay_arrival_min, delay_departure_min,
             is_confirmed, is_cancelled, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (train_run_id, station_id) DO UPDATE SET
            actual_arrival      = EXCLUDED.actual_arrival,
            actual_departure    = EXCLUDED.actual_departure,
            delay_arrival_min   = EXCLUDED.delay_arrival_min,
            delay_departure_min = EXCLUDED.delay_departure_min,
            is_confirmed        = EXCLUDED.is_confirmed,
            is_cancelled        = EXCLUDED.is_cancelled,
            last_seen_at        = NOW()
    """

    valid_trains = []
    for train in snapshot.trains:
        try:
            valid_trains.append((train, int(train.schedule_id), int(train.order_id)))
        except (ValueError, TypeError):
            continue

    with _conn(self.database_url) as conn:
        with conn.cursor() as cur:
            # 1. Snapshot dla health monitoringu
            cur.execute(snapshot_sql, (
                snapshot.data_version_guid,
                snapshot.fetched_at,
                snapshot.total_trains,
                snapshot.total_stops,
            ))

            # 2. Upsert train_runs + collect IDs
            train_run_ids: dict[tuple, int] = {}
            for train, sched_id, ord_id in valid_trains:
                op_date = train.operating_date or None
                key = (sched_id, ord_id, op_date)
                cur.execute(train_run_sql, key)
                row = cur.fetchone()
                if row is None:
                    cur.execute(train_run_select_sql, key)
                    row = cur.fetchone()
                if row:
                    train_run_ids[key] = row[0]

            # 3. Batch upsert station_stops_hot
            stop_rows = []
            for train, sched_id, ord_id in valid_trains:
                op_date = train.operating_date or None
                run_id = train_run_ids.get((sched_id, ord_id, op_date))
                if run_id is None:
                    continue
                for stop in train.stops:
                    try:
                        stop_rows.append((
                            run_id,
                            int(stop.station_id) if stop.station_id else None,
                            stop.planned_sequence,
                            stop.planned_arrival,
                            stop.actual_arrival,
                            stop.planned_departure,
                            stop.actual_departure,
                            stop.delay_arrival_minutes,
                            stop.delay_departure_minutes,
                            stop.is_confirmed,
                            stop.is_cancelled,
                        ))
                    except Exception:
                        continue

            if stop_rows:
                # Filtruj po validnych station_id (tak jak w starej metodzie)
                cur.execute("SELECT station_id FROM stations")
                valid_ids = {row[0] for row in cur.fetchall()}
                stop_rows = [r for r in stop_rows if r[1] in valid_ids]
                if stop_rows:
                    cur.executemany(hot_upsert_sql, stop_rows)

    elapsed = time.monotonic() - t0
    logger.info(
        "Snapshot hot: %d kursów, %d przystanków w %.1fs",
        len(train_run_ids), len(stop_rows), elapsed,
    )
```

### 5.2 Nowa metoda archive_hot_data() w postgres.py

Dodaj metodę do klasy `PostgresStorage`:

```python
def archive_hot_data(self, retention_days: int = 3) -> int:
    """Przepisz dane starsze niż retention_days z hot do archive, usuń z hot.

    Wywołuj raz dziennie (np. przez _tick co 24h lub osobny cron).
    Zwraca liczbę zarchiwizowanych wierszy.
    """
    archive_sql = """
        INSERT INTO station_stops_archive
            (train_run_id, station_id, operating_date,
             actual_arrival, actual_departure,
             delay_arrival_min, delay_departure_min, is_cancelled)
        SELECT
            h.train_run_id,
            h.station_id,
            tr.operating_date,
            h.actual_arrival,
            h.actual_departure,
            h.delay_arrival_min::SMALLINT,
            h.delay_departure_min::SMALLINT,
            h.is_cancelled
        FROM station_stops_hot h
        JOIN train_runs tr ON h.train_run_id = tr.id
        WHERE tr.operating_date < CURRENT_DATE - %s::integer
          AND (h.actual_arrival IS NOT NULL
               OR h.actual_departure IS NOT NULL
               OR h.is_cancelled = TRUE)
        ON CONFLICT (operating_date, train_run_id, station_id) DO NOTHING
    """
    delete_sql = """
        DELETE FROM station_stops_hot h
        USING train_runs tr
        WHERE h.train_run_id = tr.id
          AND tr.operating_date < CURRENT_DATE - %s::integer
    """
    with _conn(self.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(archive_sql, (retention_days,))
            archived = cur.rowcount
            cur.execute(delete_sql, (retention_days,))
            deleted = cur.rowcount
    logger.info("Archiwizacja: %d → archive, %d usunięto z hot", archived, deleted)
    return archived
```

### 5.3 Podpięcie archive_hot_data() w kolektorze

W `cns/collector/collector.py`, w `__init__`:

```python
self._last_archive: Optional[float] = None
self._archive_interval = 24 * 3600  # co 24h
```

W metodzie `_tick()`, na końcu:

```python
if hasattr(self.storage, "archive_hot_data") and (
    self._last_archive is None or (now - self._last_archive) >= self._archive_interval
):
    try:
        self.storage.archive_hot_data(retention_days=3)
    except Exception as e:
        logger.error("Błąd archiwizacji: %s", e)
    self._last_archive = now
```

### 5.4 Aktualizacja API (cns/api/app.py)

Endpointy korzystające z `station_stops` muszą czytać z obu tabel.
Najprościej przez widok SQL (sekcja 5.5) — wtedy API nie wymaga zmian.

### 5.5 Widok łączący hot + archive (opcjonalny, ułatwia API)

```sql
-- Dodaj do migracji 022 lub osobnej 023
CREATE OR REPLACE VIEW v_station_stops AS
    SELECT
        train_run_id, station_id, operating_date,
        actual_arrival, actual_departure,
        delay_arrival_min, delay_departure_min,
        is_cancelled
    FROM station_stops_archive
    UNION ALL
    SELECT
        h.train_run_id, h.station_id, tr.operating_date,
        h.actual_arrival, h.actual_departure,
        h.delay_arrival_min, h.delay_departure_min,
        h.is_cancelled
    FROM station_stops_hot h
    JOIN train_runs tr ON h.train_run_id = tr.id;
```

---

## 6. Skrypt migracji historycznej (chunked)

Plik: `scripts/migrate_chunk.sh`

Migruje ONE dzień z `station_stops` do `station_stops_archive`.
Uruchamiaj raz dla każdego dnia od 2026-05-30 do 3 dni temu.

```bash
#!/usr/bin/env bash
# Użycie: bash scripts/migrate_chunk.sh 2026-05-30
set -euo pipefail

DATE="${1:?Podaj datę: bash migrate_chunk.sh YYYY-MM-DD}"
CONTAINER="cyrk-na-szynach-db"
PSQL="docker exec -i $CONTAINER psql -U cyrk_na_szynach -d cyrk_na_szynach"

echo "=== Migracja dnia $DATE ==="
echo "Wolne miejsce przed:"
df -h / | tail -1

# 1. Upsert train_runs dla tego dnia
$PSQL <<SQL
INSERT INTO train_runs (schedule_id, order_id, operating_date)
SELECT DISTINCT
    to_.schedule_id::integer,
    to_.order_id::bigint,
    to_.operating_date
FROM train_operations to_
WHERE to_.operating_date = '$DATE'
ON CONFLICT (schedule_id, order_id, operating_date) DO NOTHING;
SQL
echo "  [1/5] train_runs: OK"

# 2. Archiwizuj station_stops (DISTINCT ON = ostatni odczyt per kurs×przystanek)
$PSQL <<SQL
INSERT INTO station_stops_archive
    (train_run_id, station_id, operating_date,
     actual_arrival, actual_departure,
     delay_arrival_min, delay_departure_min, is_cancelled)
SELECT DISTINCT ON (tr.id, ss.station_id)
    tr.id,
    ss.station_id,
    '$DATE'::date,
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
WHERE to_.operating_date = '$DATE'
  AND (ss.actual_arrival IS NOT NULL
       OR ss.actual_departure IS NOT NULL
       OR ss.is_cancelled = TRUE)
ORDER BY tr.id, ss.station_id, to_.collected_at DESC
ON CONFLICT (operating_date, train_run_id, station_id) DO NOTHING;
SQL
echo "  [2/5] archive insert: OK"

# 3. Usuń station_stops dla tego dnia
$PSQL <<SQL
DELETE FROM station_stops ss
USING train_operations to_
WHERE ss.train_op_id = to_.id
  AND to_.operating_date = '$DATE';
SQL
echo "  [3/5] station_stops DELETE: OK"

# 4. Usuń train_operations dla tego dnia
$PSQL <<SQL
DELETE FROM train_operations WHERE operating_date = '$DATE';
SQL
echo "  [4/5] train_operations DELETE: OK"

# 5. VACUUM żeby fizycznie zwolnić miejsce
$PSQL -c "VACUUM (ANALYZE) station_stops;" &
$PSQL -c "VACUUM (ANALYZE) train_operations;" &
wait
echo "  [5/5] VACUUM: OK"

echo "Wolne miejsce po:"
df -h / | tail -1
echo "=== Dzień $DATE zakończony ==="
```

### Pętla migracji wszystkich dni

```bash
# Dni do migracji: 2026-05-30 do 2026-06-20 (3 dni temu od 2026-06-23)
for day in $(seq 0 23 | xargs -I{} date -d "2026-05-30 + {} days" +%Y-%m-%d); do
    bash scripts/migrate_chunk.sh "$day"
    echo "Czekam 30s żeby VACUUM zakończył..." && sleep 30
done
```

Alternatywnie jeden po jednym ręcznie:

```bash
bash scripts/migrate_chunk.sh 2026-05-30
bash scripts/migrate_chunk.sh 2026-05-31
bash scripts/migrate_chunk.sh 2026-06-01
# itd.
```

---

## 7. Weryfikacja po każdym kroku

### Sprawdź rozmiary tabel

```sql
SELECT
    relname,
    pg_size_pretty(pg_total_relation_size(oid)) AS size
FROM pg_class
WHERE relname IN (
    'station_stops', 'train_operations',
    'station_stops_hot', 'station_stops_archive',
    'train_runs'
)
ORDER BY pg_total_relation_size(oid) DESC;
```

### Sprawdź kompletność archiwum

```sql
-- Porównaj liczbę unikatowych kursów per dzień przed i po migracji
SELECT
    operating_date,
    COUNT(DISTINCT train_run_id) AS kursy_w_archive
FROM station_stops_archive
GROUP BY operating_date
ORDER BY operating_date;
```

### Sprawdź integralność hot (brak old data)

```sql
SELECT
    MIN(tr.operating_date) AS najstarszy,
    MAX(tr.operating_date) AS najnowszy,
    COUNT(*) AS wierszy
FROM station_stops_hot h
JOIN train_runs tr ON h.train_run_id = tr.id;
```

---

## 8. Szacowane końcowe rozmiary

| Tabela | Teraz | Po migracji |
|--------|-------|------------|
| station_stops | 58 GB | **usunięta** |
| train_operations | 2.2 GB | **usunięta** |
| station_stops_hot | — | ~50 MB |
| station_stops_archive (23 dni) | — | ~140 MB |
| **Łącznie** | **~62 GB** | **~300 MB** |

**Roczny przyrost po migracji:** ~2.2 GB (archive) + ~50 MB (hot rolling) = **~2.3 GB/rok**
zamiast obecnych ~900 GB/rok.

---

## 9. Uwagi krytyczne

1. **Kolejność DELETE**: najpierw `station_stops`, potem `train_operations` (FK constraint).

2. **VACUUM po każdym dniu** — PostgreSQL nie zwalnia miejsca od razu po DELETE.
   Bez VACUUM dysk nie wróci. Każdy VACUUM może trwać 2–5 minut.

3. **Kolektor działa podczas migracji** — po KROKU 1 pisze do hot, nie do starych tabel.
   Migracja historyczna nie koliduje z bieżącym działaniem.

4. **train_runs.order_id jako BIGINT** — `order_id` w `train_operations` to faktycznie bigint
   (duże wartości). Upewnij się że kolumna ma typ `BIGINT` a nie `INTEGER`.

5. **Partycja na bieżący miesiąc musi istnieć** — przed każdym 1. dniem miesiąca dodaj:
   ```sql
   CREATE TABLE station_stops_archive_YYYY_MM
       PARTITION OF station_stops_archive
       FOR VALUES FROM ('YYYY-MM-01') TO ('YYYY-MM+1-01');
   ```
   Możesz to zautomatyzować w archival job (sprawdź czy partycja istnieje przed INSERT).

6. **Widoki `v_active_delays` i inne** — korzystają z `station_stops`.
   Po DROP TABLE muszą być przepisane na `v_station_stops` (sekcja 5.5) lub na `station_stops_hot`.
   Zrób to przed KROKIEM 3.

7. **`mv_training_features`** — widok zmaterializowany prawdopodobnie joinuje `station_stops`.
   Sprawdź i zaktualizuj SQL widoku przed DROP.

8. **`mv_train_run_delays` i `mv_cancelled_runs`** — sprawdź czy referują `station_stops`
   lub `train_operations`. Zaktualizuj przed DROP.

---

## 10. Pliki do modyfikacji (lista)

| Plik | Co zmienić |
|------|-----------|
| `migrations/022_hot_cold_storage.sql` | nowy plik — treść z sekcji 3 |
| `cns/storage/postgres.py` | `save_snapshot()` → sekcja 5.1; nowa `archive_hot_data()` → sekcja 5.2 |
| `cns/collector/collector.py` | `__init__` + `_tick()` → sekcja 5.3 |
| `scripts/migrate_chunk.sh` | nowy plik — treść z sekcji 6 |
| migracje widoków | sprawdź i zaktualizuj przed DROP TABLE |

---

## 11. Rollback

Jeśli cokolwiek pójdzie nie tak przed KROKIEM 3 (DROP TABLE):
- Stare tabele `station_stops` i `train_operations` nadal istnieją
- Wystarczy przywrócić starą wersję `save_snapshot()` i zdeployować
- Nowe tabele można usunąć: `DROP TABLE station_stops_hot, station_stops_archive, train_runs CASCADE`

Po KROKU 3 rollback jest niemożliwy bez backupu.
**Zrób backup przed KROKIEM 3:**
```bash
bash scripts/backup_db.sh
```
