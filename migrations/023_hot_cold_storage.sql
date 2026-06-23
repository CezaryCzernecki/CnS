-- Migracja hot/cold storage — nowe tabele dla station_stops
--
-- Tworzy trzy nowe tabele:
--   train_runs            — 1 wiersz per unikatowy kurs (zastępuje train_operations jako FK)
--   station_stops_hot     — ostatnie 3 dni, UPSERT, pełne kolumny
--   station_stops_archive — starsze dane, tylko faktyczne pomiary, partycjonowane
--
-- Stare tabele (station_stops, train_operations) NIE są tu usuwane.
-- DROP nastąpi po zakończeniu migracji historycznej (osobny krok).
--
-- Poprawki względem MIGRATION_HOT_COLD.md:
--   - numer pliku 023 (022 zajęte przez cancelled_runs_mv)
--   - station_id NOT NULL w archive — unikamy NULL w PK; NULL-owe przystanki
--     są pomijane przy archiwizacji (WHERE h.station_id IS NOT NULL)
--   - UNIQUE NULLS NOT DISTINCT w station_stops_hot na wypadek gdyby station_id
--     był NULL (choć Python filtruje to przed insertem)


-- ── train_runs ────────────────────────────────────────────────────────────────
-- 1 wiersz per unikatowy kurs pociągu (schedule_id + order_id + date).
-- Zastępuje per-snapshot train_operations jako klucz FK dla obu warstw.

CREATE TABLE IF NOT EXISTS train_runs (
    id             SERIAL       PRIMARY KEY,
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
-- Pełne kolumny (w tym planned) — potrzebne do wyświetlania na stronie i MV.

CREATE TABLE IF NOT EXISTS station_stops_hot (
    id                  BIGSERIAL    PRIMARY KEY,
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
    -- NULLS NOT DISTINCT: bezpieczne gdy station_id wyjątkowo NULL
    UNIQUE NULLS NOT DISTINCT (train_run_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_ssh_station_id
    ON station_stops_hot (station_id);
CREATE INDEX IF NOT EXISTS idx_ssh_last_seen
    ON station_stops_hot (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_ssh_train_run_id
    ON station_stops_hot (train_run_id);


-- ── station_stops_archive ────────────────────────────────────────────────────
-- Starsze niż 3 dni. Tylko faktyczne pomiary (actual IS NOT NULL lub cancelled).
-- station_id NOT NULL — archiwizujemy tylko przystanki z poznaną stacją.
-- Brak planned_arrival/departure — są w schedule_stops.
-- SMALLINT dla opóźnień (zakres ±32767 min — wystarczy z dużym zapasem).
-- Partycjonowana po miesiącach — DROP PARTITION zamiast DELETE dla starych danych.

CREATE TABLE IF NOT EXISTS station_stops_archive (
    train_run_id        INTEGER      NOT NULL,
    station_id          INTEGER      NOT NULL,
    operating_date      DATE         NOT NULL,
    actual_arrival      TIMESTAMPTZ,
    actual_departure    TIMESTAMPTZ,
    delay_arrival_min   SMALLINT,
    delay_departure_min SMALLINT,
    is_cancelled        BOOLEAN      NOT NULL DEFAULT FALSE,
    PRIMARY KEY (operating_date, train_run_id, station_id)
) PARTITION BY RANGE (operating_date);

-- Partycje miesięczne — dane historyczne od 2026-05, przez cały rok 2026/2027
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

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_01
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_02
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_03
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_04
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_05
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_06
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_07
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_08
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_09
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_10
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_11
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');

CREATE TABLE IF NOT EXISTS station_stops_archive_2027_12
    PARTITION OF station_stops_archive
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');

-- BRIN zamiast B-tree — dane w archive są sequentially ordered po operating_date,
-- BRIN = <1% rozmiaru B-tree i wystarczy do pruning partycji przy filtrach datowych.
CREATE INDEX IF NOT EXISTS idx_ssa_brin_date
    ON station_stops_archive USING BRIN (operating_date);
