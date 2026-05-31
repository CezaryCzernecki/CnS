-- Rozszerzenie v_active_delays o czasy dla stacji pierwszej, ostatniej i aktualnej.

DROP VIEW IF EXISTS v_active_delays;

CREATE VIEW v_active_delays AS
WITH latest_snapshot AS (
    SELECT id, fetched_at
    FROM operations_snapshots
    ORDER BY fetched_at DESC
    LIMIT 1
),
first_station AS (
    SELECT DISTINCT ON (ss.train_op_id)
        ss.train_op_id,
        st.name             AS station_name,
        ss.planned_departure AS departure_time
    FROM station_stops ss
    JOIN train_operations to_ ON ss.train_op_id = to_.id
    JOIN latest_snapshot ls   ON to_.snapshot_id = ls.id
    LEFT JOIN stations st     ON ss.station_id = st.station_id
    ORDER BY ss.train_op_id, ss.planned_sequence ASC
),
last_station AS (
    SELECT DISTINCT ON (ss.train_op_id)
        ss.train_op_id,
        st.name            AS station_name,
        ss.planned_arrival AS arrival_time
    FROM station_stops ss
    JOIN train_operations to_ ON ss.train_op_id = to_.id
    JOIN latest_snapshot ls   ON to_.snapshot_id = ls.id
    LEFT JOIN stations st     ON ss.station_id = st.station_id
    ORDER BY ss.train_op_id, ss.planned_sequence DESC
),
last_visited AS (
    SELECT DISTINCT ON (ss.train_op_id)
        ss.train_op_id,
        st.name           AS station_name,
        ss.actual_arrival AS actual_arrival_time
    FROM station_stops ss
    JOIN train_operations to_ ON ss.train_op_id = to_.id
    JOIN latest_snapshot ls   ON to_.snapshot_id = ls.id
    LEFT JOIN stations st     ON ss.station_id = st.station_id
    WHERE ss.actual_arrival IS NOT NULL OR ss.actual_departure IS NOT NULL
    ORDER BY ss.train_op_id, ss.actual_sequence DESC
),
train_delay AS (
    SELECT
        ss.train_op_id,
        MAX(ss.delay_departure_min) AS delay_departure_min,
        MAX(ss.delay_arrival_min)   AS delay_arrival_min
    FROM station_stops ss
    JOIN train_operations to_ ON ss.train_op_id = to_.id
    JOIN latest_snapshot ls   ON to_.snapshot_id = ls.id
    GROUP BY ss.train_op_id
)
SELECT
    to_.schedule_id,
    to_.order_id,
    to_.operating_date,
    to_.train_status,
    snap.fetched_at                 AS snapshot_time,
    sc.national_number              AS train_number,
    c.name                          AS carrier_name,
    fs.station_name                 AS first_station,
    fs.departure_time               AS first_station_departure,
    ls.station_name                 AS last_station,
    ls.arrival_time                 AS last_station_arrival,
    lv.station_name                 AS last_visited_station,
    lv.actual_arrival_time          AS last_visited_arrival,
    td.delay_departure_min,
    td.delay_arrival_min
FROM train_operations to_
JOIN latest_snapshot snap       ON to_.snapshot_id = snap.id
LEFT JOIN schedules sc          ON sc.schedule_id    = to_.schedule_id
                                AND sc.order_id       = to_.order_id
                                AND sc.operating_date = to_.operating_date
LEFT JOIN carriers c            ON c.code = sc.carrier_code
LEFT JOIN first_station fs      ON to_.id = fs.train_op_id
LEFT JOIN last_station ls       ON to_.id = ls.train_op_id
LEFT JOIN last_visited lv       ON to_.id = lv.train_op_id
LEFT JOIN train_delay td        ON to_.id = td.train_op_id
WHERE to_.train_status IN ('P', 'X')
ORDER BY td.delay_departure_min DESC NULLS LAST;
