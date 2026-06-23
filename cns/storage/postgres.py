"""
PostgreSQL storage dla TorAlert.

Wymagania:
    poetry install -E postgres

Zmienne środowiskowe (.env):
    DATABASE_URL=postgresql://user:password@localhost:5432/cns
"""

import logging
from datetime import datetime
from typing import Optional

from cns.collector.parser import detect_bus_replacement
from cns.models.records import Carrier, OperationsSnapshot, Station

logger = logging.getLogger(__name__)


def _conn(database_url: str):
    try:
        import psycopg
        return psycopg.connect(database_url)
    except ImportError:
        raise ImportError("Zainstaluj: poetry install -E postgres")


def _conn_autocommit(database_url: str):
    """Połączenie z autocommit=True – wymagane przez REFRESH MATERIALIZED VIEW CONCURRENTLY."""
    try:
        import psycopg
        return psycopg.connect(database_url, autocommit=True)
    except ImportError:
        raise ImportError("Zainstaluj: poetry install -E postgres")


class PostgresStorage:

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._verify_connection()

    def _verify_connection(self) -> None:
        try:
            with _conn(self.database_url) as c:
                with c.cursor() as cur:
                    cur.execute("SELECT 1")
            logger.info("✅ Połączenie z PostgreSQL OK")
        except Exception as e:
            logger.error("❌ Błąd połączenia z PostgreSQL: %s", e)
            raise

    # -------------------------------------------------------------------------
    # Słowniki
    # -------------------------------------------------------------------------

    def upsert_stations(self, stations: list[Station]) -> None:
        if not stations:
            return
        sql = """
            INSERT INTO stations (station_id, name, short_name, latitude, longitude, synced_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (station_id) DO UPDATE SET
                name=EXCLUDED.name, short_name=EXCLUDED.short_name,
                latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
                synced_at=NOW()
        """
        rows = []
        for s in stations:
            try:
                rows.append((int(s.station_id), s.name, s.short_name, s.latitude, s.longitude))
            except (ValueError, TypeError):
                continue
        if not rows:
            return
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        logger.info("Upsert stacji: %d", len(rows))

    def upsert_carriers(self, carriers: list[Carrier]) -> None:
        if not carriers:
            return
        sql = """
            INSERT INTO carriers (code, name, synced_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, synced_at=NOW()
        """
        rows = [(c.code, c.name) for c in carriers if c.code]
        if not rows:
            return
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        logger.info("Upsert przewoźników: %d", len(rows))

    def upsert_commercial_categories(self, categories: dict) -> None:
        if not categories:
            return
        sql = """
            INSERT INTO commercial_categories (symbol, name)
            VALUES (%s, %s)
            ON CONFLICT (symbol) DO UPDATE SET name=EXCLUDED.name
        """
        rows = [(str(k), str(v)) for k, v in categories.items() if k]
        if not rows:
            return
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        logger.info("Upsert kategorii: %d", len(rows))

    # -------------------------------------------------------------------------
    # Rozkład planowy
    # -------------------------------------------------------------------------

    def save_schedules(self, raw: dict) -> None:
        routes = raw.get("routes") or []
        if not routes:
            logger.warning("Brak tras w danych rozkładu")
            return

        dictionaries = raw.get("dictionaries", {})

        cats = dictionaries.get("commercialCategories", {})
        if cats:
            self.upsert_commercial_categories(cats)

        stations_dict = dictionaries.get("stations", {})
        if stations_dict:
            stations = [
                Station(
                    station_id=str(k),
                    name=v.get("name", "") if isinstance(v, dict) else str(v)
                )
                for k, v in stations_dict.items()
            ]
            self.upsert_stations(stations)

        schedule_sql = """
            INSERT INTO schedules
                (schedule_id, order_id, carrier_code, national_number,
                 train_name, commercial_category, operating_date, fetched_at)
            VALUES (%s, %s, %s, %s, %s,
                    (SELECT symbol FROM commercial_categories WHERE symbol = %s),
                    %s, NOW())
            ON CONFLICT (schedule_id, order_id, operating_date) DO UPDATE SET
                carrier_code=EXCLUDED.carrier_code,
                national_number=EXCLUDED.national_number,
                train_name=EXCLUDED.train_name,
                commercial_category=EXCLUDED.commercial_category,
                fetched_at=NOW()
            RETURNING id
        """
        stop_sql = """
            INSERT INTO schedule_stops
                (schedule_id, station_id, order_number, arrival_time, departure_time, platform)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (schedule_id, order_number) DO NOTHING
        """

        inserted = 0
        skipped = 0

        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                for route in routes:
                    for op_date in (route.get("operatingDates") or []):
                        try:
                            cur.execute("SAVEPOINT sp_route")
                            cur.execute(schedule_sql, (
                                int(route.get("scheduleId") or 0),
                                int(route.get("orderId") or 0),
                                route.get("carrierCode"),
                                route.get("nationalNumber"),
                                route.get("name"),
                                route.get("commercialCategorySymbol"),
                                op_date,
                            ))
                            row = cur.fetchone()
                            if row:
                                schedule_db_id = row[0]
                                stop_rows = []
                                for s in (route.get("stations") or []):
                                    try:
                                        stop_rows.append((
                                            schedule_db_id,
                                            int(s.get("stationId") or 0),
                                            int(s.get("orderNumber") or 0),
                                            s.get("arrivalTime"),
                                            s.get("departureTime"),
                                            s.get("departurePlatform") or s.get("arrivalPlatform"),
                                        ))
                                    except (ValueError, TypeError):
                                        continue
                                if stop_rows:
                                    cur.executemany(stop_sql, stop_rows)
                            cur.execute("RELEASE SAVEPOINT sp_route")
                            inserted += 1
                        except Exception as e:
                            cur.execute("ROLLBACK TO SAVEPOINT sp_route")
                            cur.execute("RELEASE SAVEPOINT sp_route")
                            skipped += 1
                            if skipped <= 3:
                                logger.warning("Pominięto trasę (%s/%s): %s",
                                               route.get("orderId"), op_date, e)

        logger.info("Rozkład: zapisano %d, pominięto %d tras", inserted, skipped)

    # -------------------------------------------------------------------------
    # Dane operacyjne
    # -------------------------------------------------------------------------

    def save_snapshot(self, snapshot: OperationsSnapshot) -> None:
        import time
        t0 = time.monotonic()

        snapshot_sql = """
            INSERT INTO operations_snapshots
                (data_version, fetched_at, total_trains, total_stops)
            VALUES (%s, %s, %s, %s)
        """
        # UPSERT train_runs — 1 wiersz per unikatowy kurs, nie per snapshot
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

                # 2. Upsert train_runs — zbierz ID per kurs
                train_run_ids: dict[tuple, int] = {}
                for train, sched_id, ord_id in valid_trains:
                    op_date = train.operating_date or None
                    key = (sched_id, ord_id, op_date)
                    cur.execute(train_run_sql, key)
                    row = cur.fetchone()
                    if row is None:
                        # ON CONFLICT DO NOTHING — pobierz istniejące ID
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

    def archive_hot_data(self, retention_days: int = 3) -> int:
        """Przepisz dane starsze niż retention_days z hot do archive, usuń z hot.

        Wywołuj raz dziennie. Zwraca liczbę zarchiwizowanych wierszy.
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
            WHERE tr.operating_date < CURRENT_DATE - (%s)
              AND h.station_id IS NOT NULL
              AND (h.actual_arrival IS NOT NULL
                   OR h.actual_departure IS NOT NULL
                   OR h.is_cancelled = TRUE)
            ON CONFLICT (operating_date, train_run_id, station_id) DO NOTHING
        """
        delete_sql = """
            DELETE FROM station_stops_hot h
            USING train_runs tr
            WHERE h.train_run_id = tr.id
              AND tr.operating_date < CURRENT_DATE - (%s)
        """
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(archive_sql, (retention_days,))
                archived = cur.rowcount
                cur.execute(delete_sql, (retention_days,))
                deleted = cur.rowcount
        logger.info("Archiwizacja: %d → archive, %d usunięto z hot", archived, deleted)
        return archived

    # -------------------------------------------------------------------------
    # Utrudnienia
    # -------------------------------------------------------------------------

    def save_disruptions(self, raw: dict) -> None:
        items = raw.get("disruptions") or []
        if not items:
            logger.info("Brak utrudnień do zapisania")
            return

        stations_dict = raw.get("stations", {})
        if stations_dict and isinstance(stations_dict, dict):
            stations = [
                Station(
                    station_id=str(k),
                    name=v.get("name", "") if isinstance(v, dict) else str(v)
                )
                for k, v in stations_dict.items()
            ]
            self.upsert_stations(stations)

        disruption_sql = """
            INSERT INTO disruptions
                (disruption_id, message, disruption_type_code,
                 start_station_id, end_station_id, has_bus_replacement,
                 collected_at, collected_date)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), CURRENT_DATE)
            ON CONFLICT (disruption_id, collected_date) DO UPDATE SET
                message=EXCLUDED.message,
                disruption_type_code=EXCLUDED.disruption_type_code,
                start_station_id=EXCLUDED.start_station_id,
                end_station_id=EXCLUDED.end_station_id,
                has_bus_replacement=EXCLUDED.has_bus_replacement
            RETURNING id
        """
        route_sql = """
            INSERT INTO disruption_affected_routes
                (disruption_id, schedule_id, order_id, operating_date,
                 station_id, sequence_number)
            VALUES (%s, %s, %s, %s, %s, %s)
        """

        inserted = 0
        skipped = 0

        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                for item in items:
                    try:
                        cur.execute("SAVEPOINT sp_dis")
                        msg = item.get("message")
                        type_code = item.get("disruptionTypeCode")
                        start_sid = item.get("startStationId")
                        end_sid = item.get("endStationId")
                        cur.execute(disruption_sql, (
                            int(item.get("disruptionId") or 0),
                            msg,
                            type_code,
                            int(start_sid) if start_sid is not None else None,
                            int(end_sid) if end_sid is not None else None,
                            detect_bus_replacement(msg, type_code),
                        ))
                        row = cur.fetchone()
                        if not row:
                            cur.execute("RELEASE SAVEPOINT sp_dis")
                            continue

                        disruption_db_id = row[0]
                        route_rows = []
                        for r in (item.get("affectedRoutes") or []):
                            try:
                                route_rows.append((
                                    disruption_db_id,
                                    int(r.get("scheduleId") or 0),
                                    int(r.get("orderId") or 0),
                                    r.get("operatingDate"),
                                    int(r.get("stationId")) if r.get("stationId") else None,
                                    r.get("sequenceNumber"),
                                ))
                            except (ValueError, TypeError):
                                continue

                        if route_rows:
                            cur.executemany(route_sql, route_rows)

                        cur.execute("RELEASE SAVEPOINT sp_dis")
                        inserted += 1

                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT sp_dis")
                        cur.execute("RELEASE SAVEPOINT sp_dis")
                        skipped += 1
                        if skipped <= 3:
                            logger.warning("Pominięto utrudnienie: %s", e)

        logger.info("Utrudnienia: zapisano %d, pominięto %d", inserted, skipped)

    # -------------------------------------------------------------------------
    # Pogoda
    # -------------------------------------------------------------------------

    def get_weather_stations(self, limit: int = 30) -> list[tuple]:
        """Zwraca (station_id, latitude, longitude) dla stacji z koordynatami."""
        sql = """
            SELECT station_id, latitude, longitude
            FROM stations
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY name
            LIMIT %s
        """
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                return cur.fetchall()

    def save_weather_observations(self, observations: list[dict]) -> None:
        if not observations:
            return
        sql = """
            INSERT INTO weather_observations
                (station_id, observed_at, is_forecast,
                 temperature_c, precipitation_mm, wind_speed_kmh,
                 snowfall_cm, visibility_m, cloud_cover_pct, weather_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id, observed_at, is_forecast) DO UPDATE SET
                temperature_c=EXCLUDED.temperature_c,
                precipitation_mm=EXCLUDED.precipitation_mm,
                wind_speed_kmh=EXCLUDED.wind_speed_kmh,
                snowfall_cm=EXCLUDED.snowfall_cm,
                visibility_m=EXCLUDED.visibility_m,
                cloud_cover_pct=EXCLUDED.cloud_cover_pct,
                weather_code=EXCLUDED.weather_code
        """
        rows = [
            (
                obs.get("station_id"),
                obs.get("observed_at"),
                obs.get("is_forecast", False),
                obs.get("temperature_c"),
                obs.get("precipitation_mm"),
                obs.get("wind_speed_kmh"),
                obs.get("snowfall_cm"),
                obs.get("visibility_m"),
                obs.get("cloud_cover_pct"),
                obs.get("weather_code"),
            )
            for obs in observations
            if obs.get("station_id") and obs.get("observed_at")
        ]
        if not rows:
            return
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        logger.info("Zapisano %d obserwacji pogodowych", len(rows))

    # -------------------------------------------------------------------------
    # Kalendarz
    # -------------------------------------------------------------------------

    def is_calendar_populated(self) -> bool:
        try:
            with _conn(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM calendar_events")
                    return cur.fetchone()[0] > 0
        except Exception:
            return False

    def save_calendar_events(self, rows: list[dict]) -> None:
        if not rows:
            return
        sql = """
            INSERT INTO calendar_events (event_date, zone, day_type, event_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (event_date, zone) DO UPDATE SET
                day_type=EXCLUDED.day_type,
                event_name=EXCLUDED.event_name
        """
        data = [
            (
                r["event_date"],
                r.get("zone"),
                r["day_type"].value if hasattr(r["day_type"], "value") else r["day_type"],
                r.get("event_name"),
            )
            for r in rows
        ]
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, data)
        logger.info("Zapisano %d wpisów kalendarza", len(data))

    # -------------------------------------------------------------------------
    # Feature Store
    # -------------------------------------------------------------------------

    def refresh_features(self) -> None:
        """REFRESH MATERIALIZED VIEW CONCURRENTLY mv_training_features.

        Wymaga autocommit – REFRESH CONCURRENTLY nie może być wewnątrz transakcji.
        Wywoływana z wątku demona po każdym save_snapshot().
        """
        import time
        t0 = time.monotonic()
        try:
            with _conn_autocommit(self.database_url) as conn:
                conn.execute(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_training_features"
                )
            logger.info("Odświeżono mv_training_features (%.1fs)", time.monotonic() - t0)
        except Exception as e:
            logger.warning("Błąd odświeżania feature store: %s", e)

    def refresh_rankings(self) -> None:
        """REFRESH MATERIALIZED VIEW CONCURRENTLY dla obu widoków rankingowych.

        Wymaga autocommit – REFRESH CONCURRENTLY nie może być wewnątrz transakcji.
        Wywoływana z wątku demona po każdym save_snapshot() (throttle 1×/h).
        """
        import time
        t0 = time.monotonic()
        try:
            with _conn_autocommit(self.database_url) as conn:
                conn.execute(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_train_run_delays"
                )
                conn.execute(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cancelled_runs"
                )
            logger.info(
                "Odświeżono mv_train_run_delays + mv_cancelled_runs (%.1fs)",
                time.monotonic() - t0,
            )
        except Exception as e:
            logger.warning("Błąd odświeżania rankingów: %s", e)

    # -------------------------------------------------------------------------
    # Diagnostyka
    # -------------------------------------------------------------------------

    def save_raw(self, name: str, data: dict) -> None:
        pass

    def get_stats(self) -> dict:
        sql = """
            SELECT
                (SELECT COUNT(*) FROM stations)             AS stations,
                (SELECT COUNT(*) FROM carriers)             AS carriers,
                (SELECT COUNT(*) FROM operations_snapshots) AS snapshots,
                (SELECT COUNT(*) FROM train_runs)           AS train_ops,
                (SELECT COUNT(*) FROM station_stops_hot)
                  + (SELECT COUNT(*) FROM station_stops_archive) AS stops,
                (SELECT COUNT(*) FROM disruptions)          AS disruptions,
                (SELECT MAX(fetched_at) FROM operations_snapshots) AS last_snapshot,
                (SELECT MIN(operating_date) FROM train_runs) AS measurement_start
        """
        with _conn(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return {
                    "stations":          row[0],
                    "carriers":          row[1],
                    "snapshots":         row[2],
                    "train_ops":         row[3],
                    "stops":             row[4],
                    "disruptions":       row[5],
                    "last_snapshot":     row[6],
                    "measurement_start": str(row[7]) if row[7] is not None else None,
                }