"""
cyrk_na_szynach – FastAPI

Uruchomienie:
    poetry run cns api-serve
    poetry run cns api-serve --host 0.0.0.0 --port 8080

Wymagania:
    poetry install -E api

Endpointy:
    GET /                         – health check
    GET /delays/stations/top      – top N stacji z największymi opóźnieniami (7 dni)
    GET /delays/active            – aktualnie opóźnione pociągi (status P)
    GET /stats                    – statystyki bazy
    GET /predict/baseline         – predykcja opóźnienia (model historycznych median)
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan – ładowanie modelu ML przy starcie
# ---------------------------------------------------------------------------

def _find_latest_model() -> Optional[Path]:
    """Zwraca najnowszy baseline_v*.pkl z katalogu models/."""
    model_path_env = os.environ.get("BASELINE_MODEL_PATH")
    if model_path_env:
        p = Path(model_path_env)
        return p if p.exists() else None
    models_dir = Path("models")
    if not models_dir.exists():
        return None
    candidates = sorted(models_dir.glob("baseline_v*.pkl"), reverse=True)
    return candidates[0] if candidates else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.baseline_model = None
    model_path = _find_latest_model()
    if model_path:
        try:
            from cns.ml.baseline_model import BaselineModel
            app.state.baseline_model = BaselineModel.load(model_path)
            logger.info("Załadowano model baseline: %s", model_path)
        except Exception as e:
            logger.warning("Nie udało się załadować modelu baseline: %s", e)
    yield


app = FastAPI(
    title="cyrk_na_szynach API",
    version="1.0.0",
    description="API do analizy opóźnień pociągów PKP PLK",
    lifespan=lifespan,
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


class BaselinePredictionResponse(BaseModel):
    station_id: str
    station_name: Optional[str] = None
    predicted_delay_min: Optional[float]
    p75_delay_min: Optional[float]
    p90_delay_min: Optional[float]
    sample_count: int
    model: str = "baseline"
    model_date: Optional[str] = None
    fallback: bool


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


@app.get("/predict/baseline", response_model=BaselinePredictionResponse)
def predict_baseline(
    request: Request,
    station_id: str = Query(..., description="ID stacji PKP (np. 33506)"),
    planned_departure: str = Query(
        ..., description="Planowany odjazd ISO 8601 (np. 2026-05-31T10:00:00)"
    ),
    day_type: Optional[str] = Query(
        None,
        description="Typ dnia: WORKING/WEEKEND/HOLIDAY/LONG_WEEKEND/… (auto-detect jeśli pominięty)",
    ),
    db_url: str = Depends(_db_url),
):
    """Predykcja opóźnienia przez model historycznych median (baseline)."""
    model = getattr(request.app.state, "baseline_model", None)
    if model is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Model baseline nie jest załadowany. "
                "Uruchom: poetry run python -m cns.ml.train_baseline"
            ),
        )

    try:
        dt = datetime.fromisoformat(planned_departure)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Nieprawidłowy format daty: '{planned_departure}'. Użyj ISO 8601.",
        )

    if day_type is None:
        from cns.collector.calendar_service import CalendarService
        day_type = CalendarService().get_day_type(dt.date()).value

    # Pobierz nazwę stacji (opcjonalne – błąd DB nie blokuje odpowiedzi)
    station_name: Optional[str] = None
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM stations WHERE station_id = %s",
                    (int(station_id),),
                )
                row = cur.fetchone()
                if row:
                    station_name = row[0]
    except Exception:
        pass

    pred = model.predict(station_id, dt.hour, day_type)

    return BaselinePredictionResponse(
        station_id=station_id,
        station_name=station_name,
        predicted_delay_min=pred.median_delay,
        p75_delay_min=pred.p75_delay,
        p90_delay_min=pred.p90_delay,
        sample_count=pred.sample_count,
        model="baseline",
        model_date=getattr(model, "trained_date", None),
        fallback=pred.fallback,
    )
