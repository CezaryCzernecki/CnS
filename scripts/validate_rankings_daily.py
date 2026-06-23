#!/usr/bin/env python3
"""
Walidator rankingów — uruchamiany 4× na dobę przez cron.

Uruchomienie ręczne:
    poetry run python scripts/validate_rankings_daily.py

Cron (crontab -e):
    0 6,12,18,0 * * * /home/cezary/cns/CnS/scripts/validate_rankings_daily.sh

    Skrypt .sh ustawia DATABASE_URL na 127.0.0.1:5432 (host→kontener),
    bo cron nie dziedziczy PATH i .env używa hostname "db" widocznego tylko
    wewnątrz sieci Docker. Port jest ekspozowany przez docker-compose.yml.

Wyniki JSON: logs/rankings_validation_YYYY-MM-DD_HHMM.json (starsze niż 14 dni usuwane).
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL nie ustawiony (.env lub env var)")


def run_checks(db_url: str) -> dict:
    import psycopg

    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1)

    result: dict = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "date": str(today),
        "month": f"{today.year}-{today.month:02d}",
        "checks": {},
        "alerts": [],
    }

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:

            # ------------------------------------------------------------------
            # 1. MV pokrycie — ile rekordów ma national_number
            # ------------------------------------------------------------------
            cur.execute("""
                SELECT
                    COUNT(*)                                                         AS total_mv,
                    COUNT(sc.id)                                                     AS has_schedule,
                    COUNT(CASE WHEN sc.national_number IS NOT NULL THEN 1 END)       AS has_national_number,
                    ROUND(
                        100.0 * COUNT(CASE WHEN sc.national_number IS NOT NULL THEN 1 END)
                        / NULLIF(COUNT(*), 0), 1
                    )                                                                AS coverage_pct
                FROM mv_train_run_delays dr
                LEFT JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                                      AND sc.order_id       = dr.order_id
                                      AND sc.operating_date = dr.operating_date
            """)
            row = cur.fetchone()
            mv_coverage = {
                "total_mv_rows": row[0],
                "has_schedule": row[1],
                "has_national_number": row[2],
                "coverage_pct": float(row[3]) if row[3] else 0.0,
            }
            result["checks"]["mv_coverage"] = mv_coverage
            if mv_coverage["coverage_pct"] < 90.0:
                result["alerts"].append(
                    f"MV pokrycie national_number: {mv_coverage['coverage_pct']}% (próg: 90%)"
                )

            # ------------------------------------------------------------------
            # 2. Filtrowanie w all-time top-100: ile odpada przez brak/odwołanie
            # ------------------------------------------------------------------
            cur.execute("""
                WITH top100 AS (
                    SELECT schedule_id, order_id, operating_date,
                           max_delay_min, latest_train_op_id
                    FROM mv_train_run_delays
                    ORDER BY max_delay_min DESC LIMIT 100
                )
                SELECT
                    CASE
                        WHEN sc.national_number IS NULL THEN 'brak_national_number'
                        WHEN BOOL_AND(ss.is_cancelled) THEN 'odwolany'
                        ELSE 'ok'
                    END AS reason,
                    COUNT(*) AS cnt
                FROM top100 dr
                LEFT JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                                      AND sc.order_id       = dr.order_id
                                      AND sc.operating_date = dr.operating_date
                LEFT JOIN station_stops ss ON ss.train_op_id = dr.latest_train_op_id
                GROUP BY 1
            """)
            filter_breakdown = {r[0]: r[1] for r in cur.fetchall()}
            result["checks"]["alltime_top100_filter"] = filter_breakdown
            filtered_out = sum(v for k, v in filter_breakdown.items() if k != "ok")
            if filtered_out > 20:
                result["alerts"].append(
                    f"Top-100 all-time: {filtered_out}/100 rekordów odpada przez filtrowanie"
                )

            # ------------------------------------------------------------------
            # 3. Top 10 pociągów w bieżącym miesiącu — czy trip_count > 31?
            # ------------------------------------------------------------------
            cur.execute("""
                SELECT
                    sc.national_number,
                    sc.train_name,
                    COUNT(DISTINCT dr.operating_date)                  AS trip_days,
                    COUNT(DISTINCT (dr.schedule_id, dr.order_id))      AS schedule_pairs,
                    MIN(dr.operating_date)::text                        AS first_day,
                    MAX(dr.operating_date)::text                        AS last_day
                FROM mv_train_run_delays dr
                JOIN schedules sc ON sc.schedule_id   = dr.schedule_id
                                 AND sc.order_id       = dr.order_id
                                 AND sc.operating_date = dr.operating_date
                WHERE dr.operating_date >= %s
                  AND dr.operating_date <  %s
                  AND sc.national_number IS NOT NULL
                GROUP BY sc.national_number, sc.train_name
                ORDER BY trip_days DESC
                LIMIT 10
            """, (month_start, month_end))
            top_trains = [
                {
                    "national_number": r[0],
                    "train_name": r[1],
                    "trip_days": r[2],
                    "schedule_pairs": r[3],
                    "first_day": r[4],
                    "last_day": r[5],
                }
                for r in cur.fetchall()
            ]
            result["checks"]["monthly_top10_trains"] = top_trains
            multi_sched = [t for t in top_trains if t["schedule_pairs"] > 1]
            if multi_sched:
                result["alerts"].append(
                    f"Pociągi z wieloma schedule_id: "
                    + ", ".join(f"{t['national_number']}({t['schedule_pairs']} wersji)" for t in multi_sched)
                )

            # ------------------------------------------------------------------
            # 4. Odwołane kursy: MV vs mv_cancelled_runs (poprawna metoda)
            # ------------------------------------------------------------------
            cur.execute("""
                WITH via_mv AS (
                    SELECT sc.carrier_code, COUNT(*) AS cnt
                    FROM mv_train_run_delays dr
                    JOIN schedules sc ON sc.schedule_id = dr.schedule_id
                                     AND sc.order_id = dr.order_id
                                     AND sc.operating_date = dr.operating_date
                    JOIN station_stops ss ON ss.train_op_id = dr.latest_train_op_id
                    WHERE dr.operating_date >= %s AND dr.operating_date < %s
                    GROUP BY sc.carrier_code, dr.schedule_id, dr.order_id, dr.operating_date
                    HAVING BOOL_AND(ss.is_cancelled) = TRUE
                ),
                via_mv_agg AS (SELECT carrier_code, SUM(cnt) AS cancelled_mv FROM via_mv GROUP BY carrier_code),
                via_dedicated AS (
                    SELECT carrier_code, SUM(cancelled_count) AS cancelled_dedicated
                    FROM mv_cancelled_runs
                    WHERE operating_date >= %s AND operating_date < %s
                    GROUP BY carrier_code
                ),
                carriers_both AS (
                    SELECT
                        COALESCE(m.carrier_code, d.carrier_code) AS carrier_code,
                        COALESCE(m.cancelled_mv, 0)              AS via_mv,
                        COALESCE(d.cancelled_dedicated, 0)       AS via_dedicated
                    FROM via_mv_agg m
                    FULL OUTER JOIN via_dedicated d ON d.carrier_code = m.carrier_code
                )
                SELECT carrier_code,
                       via_mv,
                       via_dedicated,
                       (via_dedicated - via_mv) AS missed_by_mv
                FROM carriers_both
                ORDER BY via_dedicated DESC
                LIMIT 15
            """, (month_start, month_end, month_start, month_end))
            cancelled_compare = [
                {
                    "carrier_code": r[0],
                    "via_mv_method": r[1],
                    "via_dedicated_mv": r[2],
                    "missed_by_old_method": r[3],
                }
                for r in cur.fetchall()
            ]
            result["checks"]["cancelled_comparison"] = cancelled_compare
            total_missed = sum(r["missed_by_old_method"] for r in cancelled_compare)
            if total_missed > 0:
                result["alerts"].append(
                    f"Stara metoda (MV) pomijała łącznie {total_missed} odwołanych kursów w tym miesiącu"
                )

            # ------------------------------------------------------------------
            # 5. Active delays: ile pociągów bez numeru/nazwy
            # ------------------------------------------------------------------
            cur.execute("""
                SELECT
                    COUNT(*)                                             AS total_active,
                    COUNT(CASE WHEN train_number IS NOT NULL THEN 1 END) AS with_number,
                    COUNT(CASE WHEN train_number IS     NULL THEN 1 END) AS missing_number,
                    ROUND(
                        100.0 * COUNT(CASE WHEN train_number IS NOT NULL THEN 1 END)
                        / NULLIF(COUNT(*), 0), 1
                    )                                                    AS number_coverage_pct
                FROM v_active_delays
            """)
            row = cur.fetchone()
            active_coverage = {
                "total_active": row[0],
                "with_number": row[1],
                "missing_number": row[2],
                "number_coverage_pct": float(row[3]) if row[3] else 0.0,
            }
            result["checks"]["active_delays_coverage"] = active_coverage
            if active_coverage["missing_number"] > 0 and active_coverage["number_coverage_pct"] < 95.0:
                result["alerts"].append(
                    f"Active delays: {active_coverage['missing_number']} pociągów bez numeru "
                    f"({100 - active_coverage['number_coverage_pct']:.1f}%)"
                )

            # ------------------------------------------------------------------
            # 6. Przyczyny brakujących numerów w active delays
            # ------------------------------------------------------------------
            cur.execute("""
                WITH active AS (
                    SELECT to_.schedule_id, to_.order_id, to_.operating_date
                    FROM train_operations to_
                    JOIN operations_snapshots snap ON to_.snapshot_id = snap.id
                    WHERE snap.id = (SELECT id FROM operations_snapshots ORDER BY fetched_at DESC LIMIT 1)
                      AND to_.train_status IN ('P', 'X')
                      AND to_.operating_date >= CURRENT_DATE
                )
                SELECT
                    CASE
                        WHEN sc.id IS NULL                       THEN 'brak_w_schedules'
                        WHEN sc.national_number IS NULL          THEN 'national_number_null'
                        ELSE                                          'ok'
                    END AS reason,
                    COUNT(*) AS cnt
                FROM active a
                LEFT JOIN schedules sc ON sc.schedule_id   = a.schedule_id
                                      AND sc.order_id       = a.order_id
                                      AND sc.operating_date = a.operating_date
                GROUP BY 1
                ORDER BY cnt DESC
            """)
            missing_reasons = {r[0]: r[1] for r in cur.fetchall()}
            result["checks"]["missing_number_reasons"] = missing_reasons

    return result


def _cleanup_old_logs(days: int = 14) -> None:
    cutoff = datetime.now() - timedelta(days=days)
    for f in LOGS_DIR.glob("rankings_validation_*.json"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except OSError:
            pass


def main() -> None:
    try:
        db_url = _db_url()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_checks(db_url)
    except Exception as e:
        print(f"[ERROR] Błąd podczas walidacji: {e}", file=sys.stderr)
        sys.exit(1)

    # Zapis do pliku JSON
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_file = LOGS_DIR / f"rankings_validation_{ts}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    # Podsumowanie na stdout (trafia do crona/logfile)
    alerts = result["alerts"]
    checks = result["checks"]
    print(f"[{result['run_at']}] Walidacja rankingów — {result['month']}")
    print(f"  MV pokrycie:        {checks['mv_coverage']['coverage_pct']}% "
          f"({checks['mv_coverage']['has_national_number']}/{checks['mv_coverage']['total_mv_rows']} rekordów)")
    print(f"  Active delays:      {checks['active_delays_coverage']['number_coverage_pct']}% z numerem "
          f"({checks['active_delays_coverage']['missing_number']} brakuje)")
    filter_ok = checks["alltime_top100_filter"].get("ok", 0)
    print(f"  Top-100 all-time:   {filter_ok}/100 przejdzie filtry")
    multi = [t for t in checks["monthly_top10_trains"] if t["schedule_pairs"] > 1]
    if multi:
        print(f"  Wiele schedule_id:  {', '.join(t['national_number'] for t in multi)}")
    if alerts:
        for a in alerts:
            print(f"  [ALERT] {a}")
    else:
        print("  Brak alertów.")
    print(f"  Zapisano: {out_file.name}")

    _cleanup_old_logs(days=14)


if __name__ == "__main__":
    main()
