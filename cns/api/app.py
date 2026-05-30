"""
cyrk_na_szynach – FastAPI

Uruchomienie:
    poetry run cns api-serve
    poetry run cns api-serve --host 0.0.0.0 --port 8080

Wymagania:
    poetry install -E api

Endpointy:
    GET /                        – health check
    GET /delays/stations/top     – top N stacji z największymi opóźnieniami (7 dni)
    GET /delays/active           – aktualnie opóźnione pociągi (status P)
    GET /stats                   – statystyki bazy
"""

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(
    title="cyrk_na_szynach API",
    version="1.0.0",
    description="API do analizy opóźnień pociągów PKP PLK",
)


# ---------------------------------------------------------------------------
# Dependency – URL bazy danych
# ---------------------------------------------------------------------------

def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="DATABASE_URL nie jest skonfigurowany")
    return url


# ---------------------------------------------------------------------------
# Modele odpowiedzi
# ---------------------------------------------------------------------------

class StationDelayStat(BaseModel):
    station_id: Optional[int] = None
    station_name: Optional[str] = None
    total_stops: int
    stops_with_data: int
    delayed_count: int
    avg_delay_min: Optional[float] = None
    max_delay_min: Optional[int] = None
    delay_rate_pct: Optional[float] = None


class ActiveDelay(BaseModel):
    station_id: Optional[int] = None
    station_name: Optional[str] = None
    schedule_id: int
    order_id: int
    operating_date: Optional[str] = None
    planned_departure: Optional[str] = None
    actual_departure: Optional[str] = None
    delay_departure_min: Optional[int] = None
    delay_arrival_min: Optional[int] = None
    snapshot_time: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpointy
# ---------------------------------------------------------------------------

@app.get("/")
def health():
    return {"status": "ok", "service": "cyrk_na_szynach", "version": "1.0.0"}


@app.get("/delays/stations/top", response_model=list[StationDelayStat])
def top_delayed_stations(
    limit: int = Query(default=10, ge=1, le=100, description="Liczba stacji"),
    db_url: str = Depends(_db_url),
):
    """Stacje z największymi średnimi opóźnieniami w ostatnich 7 dniach (min. 10 pomiarów)."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT station_id, station_name, total_stops, stops_with_data,
                           delayed_count, avg_delay_min, max_delay_min, delay_rate_pct
                    FROM v_station_delay_stats
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        StationDelayStat(
            station_id=r[0],
            station_name=r[1],
            total_stops=r[2],
            stops_with_data=r[3],
            delayed_count=r[4],
            avg_delay_min=float(r[5]) if r[5] is not None else None,
            max_delay_min=r[6],
            delay_rate_pct=float(r[7]) if r[7] is not None else None,
        )
        for r in rows
    ]


@app.get("/delays/active", response_model=list[ActiveDelay])
def active_delays(
    limit: int = Query(default=20, ge=1, le=200, description="Liczba wyników"),
    db_url: str = Depends(_db_url),
):
    """Aktualnie opóźnione pociągi (status P) z ostatniego snapshotu."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT station_id, station_name, schedule_id, order_id,
                           operating_date, planned_departure, actual_departure,
                           delay_departure_min, delay_arrival_min, snapshot_time
                    FROM v_active_delays
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        ActiveDelay(
            station_id=r[0],
            station_name=r[1],
            schedule_id=r[2],
            order_id=r[3],
            operating_date=str(r[4]) if r[4] is not None else None,
            planned_departure=str(r[5]) if r[5] is not None else None,
            actual_departure=str(r[6]) if r[6] is not None else None,
            delay_departure_min=r[7],
            delay_arrival_min=r[8],
            snapshot_time=str(r[9]) if r[9] is not None else None,
        )
        for r in rows
    ]


@app.get("/stats")
def stats(db_url: str = Depends(_db_url)):
    """Statystyki bazy danych (liczba rekordów w każdej tabeli)."""
    try:
        from cns.storage.postgres import PostgresStorage
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        storage = PostgresStorage(db_url)
        result = storage.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    if result.get("last_snapshot") is not None:
        result["last_snapshot"] = str(result["last_snapshot"])
    return result
