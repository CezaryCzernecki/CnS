-- Migracja 026: indeks station_stops_archive(train_run_id)
-- Bez tego indeksu LATERAL cancelled check w rankingach skanuje wszystkie partycje
-- archiwum (brak filtra operating_date w zapytaniu LATERAL).
-- Indeks na tabeli-rodzicu automatycznie propaguje się do wszystkich partycji.

CREATE INDEX IF NOT EXISTS station_stops_archive_train_run_id_idx
    ON station_stops_archive (train_run_id);
