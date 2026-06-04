-- Czyszczenie artefaktów opóźnień powstałych przy progu 1439 min.
--
-- 1438/1439 min to przesunięcia dobowe (1440 min) z zaokrągleniem ±1/2 min.
-- Zerujemy je w bazie; nowy próg kolektora to 1200 min (plik records.py).

UPDATE station_stops
SET delay_departure_min = NULL
WHERE delay_departure_min > 1200;

UPDATE station_stops
SET delay_arrival_min = NULL
WHERE delay_arrival_min > 1200;
