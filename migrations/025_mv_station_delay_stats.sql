-- Migracja 025: mv_station_delay_stats
-- Zastępuje stary VIEW v_station_delay_stats (usunięty przez DROP TABLE station_stops CASCADE)
-- nowym MATERIALIZED VIEW opartym na v_station_stops (hot + archive).
--
-- Zmiana: VIEW → MATERIALIZED VIEW odświeżany co 15 min przez refresh_rankings().
-- Efekt: eliminuje skan 900k wierszy per request → instant odczyt z ~3k wierszy agregatu.

DROP VIEW IF EXISTS v_station_delay_stats;
DROP MATERIALIZED VIEW IF EXISTS mv_station_delay_stats;

CREATE MATERIALIZED VIEW mv_station_delay_stats AS
SELECT
    vss.station_id,
    st.name                                                                     AS station_name,
    COUNT(*)                                                                    AS total_stops,
    COUNT(vss.delay_departure_min)                                              AS stops_with_data,
    COUNT(*) FILTER (WHERE vss.delay_departure_min > 0)                         AS delayed_count,
    ROUND(
        AVG(vss.delay_departure_min) FILTER (WHERE vss.delay_departure_min > 0),
        1
    )                                                                           AS avg_delay_min,
    MAX(vss.delay_departure_min)                                                AS max_delay_min,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE vss.delay_departure_min > 0)
        / NULLIF(COUNT(vss.delay_departure_min), 0),
        1
    )                                                                           AS delay_rate_pct
FROM v_station_stops vss
LEFT JOIN stations st ON st.station_id = vss.station_id
WHERE vss.operating_date >= CURRENT_DATE - 7
GROUP BY vss.station_id, st.name
HAVING COUNT(vss.delay_departure_min) >= 10
WITH NO DATA;

-- UNIQUE wymagany przez REFRESH CONCURRENTLY
CREATE UNIQUE INDEX mv_station_delay_stats_station_idx
    ON mv_station_delay_stats (station_id);

CREATE INDEX mv_station_delay_stats_delay_idx
    ON mv_station_delay_stats (avg_delay_min DESC NULLS LAST);

REFRESH MATERIALIZED VIEW mv_station_delay_stats;
