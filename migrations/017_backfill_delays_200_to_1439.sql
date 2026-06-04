-- Backfill opóźnień 200–1439 min które były filtrowane przez stary próg 200 min.
--
-- station_stops przechowuje surowe timestamps, więc możemy przeliczyć minuty
-- z actual_departure/planned_departure bez dostępu do oryginalnych snapshotów.
--
-- Warunki UPDATE:
--   delay IS NULL          — nie nadpisujemy istniejących wartości
--   oba timestamps != NULL — mamy z czego liczyć
--   wynik w przedziale (200, 1439] — to co było obcięte przez stary próg
--   wynik > 0              — tylko realne opóźnienia (nie wczesniejsze przyjazdy)

UPDATE station_stops
SET delay_departure_min = ROUND(
        EXTRACT(EPOCH FROM (actual_departure - planned_departure)) / 60
    )::int
WHERE delay_departure_min IS NULL
  AND actual_departure IS NOT NULL
  AND planned_departure IS NOT NULL
  AND EXTRACT(EPOCH FROM (actual_departure - planned_departure)) / 60 > 200
  AND EXTRACT(EPOCH FROM (actual_departure - planned_departure)) / 60 <= 1439;

UPDATE station_stops
SET delay_arrival_min = ROUND(
        EXTRACT(EPOCH FROM (actual_arrival - planned_arrival)) / 60
    )::int
WHERE delay_arrival_min IS NULL
  AND actual_arrival IS NOT NULL
  AND planned_arrival IS NOT NULL
  AND EXTRACT(EPOCH FROM (actual_arrival - planned_arrival)) / 60 > 200
  AND EXTRACT(EPOCH FROM (actual_arrival - planned_arrival)) / 60 <= 1439;
