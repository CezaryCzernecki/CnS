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
    GET /predict                  – predykcja XGBoost (główny model produkcyjny)
    GET /predict/baseline         – predykcja model historycznych median (benchmark)
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan – ładowanie modeli ML przy starcie
# ---------------------------------------------------------------------------

def _find_model(pattern: str) -> Optional[Path]:
    env_key = "BASELINE_MODEL_PATH" if "baseline" in pattern else "XGB_MODEL_PATH"
    env_path = os.environ.get(env_key)
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None
    models_dir = Path("models")
    if not models_dir.exists():
        return None
    candidates = sorted(models_dir.glob(pattern), reverse=True)
    return candidates[0] if candidates else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.baseline_model = None
    app.state.xgb_model = None

    baseline_path = _find_model("baseline_v*.pkl")
    if baseline_path:
        try:
            from cns.ml.baseline_model import BaselineModel
            app.state.baseline_model = BaselineModel.load(baseline_path)
            logger.info("Załadowano model baseline: %s", baseline_path)
        except Exception as e:
            logger.warning("Błąd ładowania baseline: %s", e)

    xgb_path = _find_model("xgb_v*.pkl")
    if xgb_path:
        try:
            from cns.ml.xgb_model import XGBoostDelayPredictor
            app.state.xgb_model = XGBoostDelayPredictor.load(xgb_path)
            logger.info("Załadowano model XGBoost: %s", xgb_path)
        except Exception as e:
            logger.warning("Błąd ładowania XGBoost: %s", e)

    yield


app = FastAPI(
    title="cyrk_na_szynach API",
    version="1.0.0",
    description="API do analizy opóźnień pociągów PKP PLK",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="DATABASE_URL nie jest skonfigurowany")
    return url


def _fetch_weather(db_url: str, station_id: str) -> dict:
    """Pobiera najnowszą obserwację pogodową z bazy. Błąd → pusty słownik."""
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT temperature_c, precipitation_mm, wind_speed_kmh,
                           snowfall_cm, visibility_m
                    FROM weather_observations
                    WHERE station_id = %s AND is_forecast = FALSE
                    ORDER BY observed_at DESC LIMIT 1
                    """,
                    (station_id,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "temperature_c": row[0],
                        "precipitation_mm": row[1],
                        "wind_speed_kmh": row[2],
                        "snowfall_cm": row[3],
                        "visibility_m": row[4],
                    }
    except Exception:
        pass
    return {}


def _fetch_station_name(db_url: str, station_id: str) -> Optional[str]:
    try:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM stations WHERE station_id = %s",
                    (int(station_id),),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None


def _build_features(
    station_id: str,
    dt: datetime,
    day_type: str,
    prev_stop_delay_min: float,
    planned_sequence: int,
    weather: dict,
) -> dict:
    """Buduje słownik cech dla modelu ML."""
    t = weather.get("temperature_c") or 0.0
    p = weather.get("precipitation_mm") or 0.0
    w = weather.get("wind_speed_kmh") or 0.0
    s = weather.get("snowfall_cm") or 0.0
    v = weather.get("visibility_m") or 10000

    return {
        "station_id":          station_id,
        "hour_of_day":         dt.hour,
        "day_of_week":         (dt.weekday() + 1) % 7,  # PostgreSQL DOW: 0=Sun
        "month":               dt.month,
        "planned_sequence":    planned_sequence,
        "prev_stop_delay_min": prev_stop_delay_min,
        "temperature_c":       t,
        "precipitation_mm":    p,
        "wind_speed_kmh":      w,
        "snowfall_cm":         s,
        "visibility_m":        v,
        "is_snowing":          s > 1,
        "is_heavy_rain":       p > 5,
        "is_strong_wind":      w > 70,
        "is_frost":            t < -10,
        "is_dense_fog":        v < 200,
        "day_type":            day_type,
    }


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
    schedule_id: int
    order_id: int
    operating_date: Optional[str] = None
    train_status: Optional[str] = None
    snapshot_time: Optional[str] = None
    train_number: Optional[str] = None
    train_name: Optional[str] = None
    carrier_name: Optional[str] = None
    first_station: Optional[str] = None
    first_station_departure: Optional[str] = None
    last_station: Optional[str] = None
    last_station_arrival: Optional[str] = None
    last_visited_station: Optional[str] = None
    last_visited_arrival: Optional[str] = None
    delay_departure_min: Optional[int] = None
    delay_arrival_min: Optional[int] = None


class StationMapPoint(BaseModel):
    station_id: Optional[int] = None
    station_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    avg_delay_min: Optional[float] = None
    delay_rate_pct: Optional[float] = None
    total_stops: int = 0


class ExplanationItem(BaseModel):
    feature: str
    impact: float
    value: Optional[Any] = None


class XGBPredictionResponse(BaseModel):
    station_id: str
    station_name: Optional[str] = None
    predicted_delay_min: float
    p75_delay_min: Optional[float] = None
    confidence_interval: Optional[list[float]] = None
    model: str = "xgboost"
    model_date: Optional[str] = None
    explanation: Optional[list[ExplanationItem]] = None


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


class AllTimeRankingEntry(BaseModel):
    train_number: Optional[str] = None
    train_name: Optional[str] = None
    carrier_name: Optional[str] = None
    operating_date: Optional[str] = None
    max_delay_min: Optional[int] = None


class DailyRankingEntry(BaseModel):
    train_number: Optional[str] = None
    train_name: Optional[str] = None
    carrier_name: Optional[str] = None
    max_delay_min: Optional[int] = None


class MonthlyTrainRankingEntry(BaseModel):
    train_number: Optional[str] = None
    train_name: Optional[str] = None
    carrier_name: Optional[str] = None
    trip_count: int = 0
    total_delay_min: Optional[int] = None
    avg_delay_min: Optional[float] = None


class MonthlyCarrierRankingEntry(BaseModel):
    carrier_name: Optional[str] = None
    trip_count: int = 0
    total_delay_min: Optional[int] = None
    avg_delay_min: Optional[float] = None


# ---------------------------------------------------------------------------
# Endpointy
# ---------------------------------------------------------------------------

@app.get("/")
def health():
    return {"status": "ok", "service": "cyrk_na_szynach", "version": "1.0.0"}


@app.get("/health/collector")
def health_collector(db_url: str = Depends(_db_url)):
    """Stan kolektora danych: ostatni snapshot, pokrycie 24h, wykryte luki."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT check_time, last_snapshot_at, minutes_since_snapshot,
                           snapshots_last_24h, expected_snapshots_24h, gaps, status
                    FROM collector_health
                    ORDER BY check_time DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    if row is None:
        raise HTTPException(
            status_code=503,
            detail="Brak danych health check. Kolektor nie uruchomiony lub tabela pusta.",
        )

    check_time, last_snap, minutes_since, snaps_24h, expected_24h, gaps, status = row
    coverage = round(snaps_24h / expected_24h * 100, 1) if expected_24h else 0.0

    return {
        "status": status,
        "last_snapshot_at": str(last_snap) if last_snap else None,
        "minutes_since_last_snapshot": minutes_since,
        "snapshots_last_24h": snaps_24h,
        "expected_24h": expected_24h,
        "coverage_pct": coverage,
        "gaps_last_24h": gaps or [],
        "checked_at": str(check_time),
    }


@app.get("/delays/stations/top", response_model=list[StationDelayStat])
def top_delayed_stations(
    limit: int = Query(default=10, ge=1, le=500, description="Liczba stacji"),
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
            station_id=r[0], station_name=r[1],
            total_stops=r[2], stops_with_data=r[3], delayed_count=r[4],
            avg_delay_min=float(r[5]) if r[5] is not None else None,
            max_delay_min=r[6],
            delay_rate_pct=float(r[7]) if r[7] is not None else None,
        )
        for r in rows
    ]


@app.get("/delays/active", response_model=list[ActiveDelay])
def active_delays(
    limit: int = Query(default=500, ge=1, le=10000, description="Liczba wyników"),
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
                    SELECT schedule_id, order_id, operating_date, train_status,
                           snapshot_time, train_number, train_name, carrier_name,
                           first_station, first_station_departure,
                           last_station, last_station_arrival,
                           last_visited_station, last_visited_arrival,
                           delay_departure_min, delay_arrival_min
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
            schedule_id=r[0], order_id=r[1],
            operating_date=str(r[2]) if r[2] is not None else None,
            train_status=r[3],
            snapshot_time=str(r[4]) if r[4] is not None else None,
            train_number=r[5], train_name=r[6], carrier_name=r[7],
            first_station=r[8],
            first_station_departure=str(r[9]) if r[9] is not None else None,
            last_station=r[10],
            last_station_arrival=str(r[11]) if r[11] is not None else None,
            last_visited_station=r[12],
            last_visited_arrival=str(r[13]) if r[13] is not None else None,
            delay_departure_min=r[14], delay_arrival_min=r[15],
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


@app.get("/delays/stations/map", response_model=list[StationMapPoint])
def stations_map(
    limit: int = Query(default=60, ge=1, le=200, description="Liczba stacji"),
    db_url: str = Depends(_db_url),
):
    """Stacje z opóźnieniami i koordynatami – do wizualizacji na mapie."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        v.station_id,
                        v.station_name,
                        s.latitude,
                        s.longitude,
                        v.avg_delay_min,
                        v.delay_rate_pct,
                        v.total_stops
                    FROM v_station_delay_stats v
                    LEFT JOIN stations s ON v.station_id = s.station_id
                    WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL
                    ORDER BY v.avg_delay_min DESC NULLS LAST
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        StationMapPoint(
            station_id=r[0],
            station_name=r[1],
            latitude=float(r[2]) if r[2] is not None else None,
            longitude=float(r[3]) if r[3] is not None else None,
            avg_delay_min=float(r[4]) if r[4] is not None else None,
            delay_rate_pct=float(r[5]) if r[5] is not None else None,
            total_stops=r[6] or 0,
        )
        for r in rows
    ]


@app.get("/predict", response_model=XGBPredictionResponse)
def predict_xgb(
    request: Request,
    station_id: str = Query(..., description="ID stacji PKP (np. 33506)"),
    planned_departure: str = Query(
        ..., description="Planowany odjazd ISO 8601 (np. 2026-05-31T10:00:00)"
    ),
    day_type: Optional[str] = Query(None, description="Typ dnia (auto-detect jeśli pominięty)"),
    prev_stop_delay_min: float = Query(0.0, description="Opóźnienie poprzedniego przystanku [min]"),
    planned_sequence: int = Query(1, ge=1, description="Numer przystanku na trasie"),
    db_url: str = Depends(_db_url),
):
    """Predykcja opóźnienia przez XGBoost z wyjaśnieniem SHAP."""
    model = getattr(request.app.state, "xgb_model", None)
    if model is None:
        # Spróbuj baseline jako fallback gdy XGB nie gotowy
        baseline = getattr(request.app.state, "baseline_model", None)
        if baseline is None:
            raise HTTPException(
                status_code=503,
                detail="Model w trakcie ładowania. Uruchom: python -m cns.ml.train_xgb",
            )
        # Baseline jako tymczasowy fallback – zwróć w formacie XGBPredictionResponse
        try:
            dt_fb = datetime.fromisoformat(planned_departure)
        except ValueError:
            raise HTTPException(400, f"Nieprawidłowy format daty: '{planned_departure}'.")
        if day_type is None:
            from cns.collector.calendar_service import CalendarService
            day_type = CalendarService().get_day_type(dt_fb.date()).value
        pred_fb = baseline.predict(station_id, dt_fb.hour, day_type)
        return XGBPredictionResponse(
            station_id=station_id,
            station_name=None,
            predicted_delay_min=pred_fb.median_delay or 0.0,
            p75_delay_min=pred_fb.p75_delay,
            confidence_interval=None,
            model="baseline_fallback",
            model_date=getattr(baseline, "trained_date", None),
            explanation=None,
        )

    try:
        dt = datetime.fromisoformat(planned_departure)
    except ValueError:
        raise HTTPException(400, f"Nieprawidłowy format daty: '{planned_departure}'. Użyj ISO 8601.")

    if day_type is None:
        from cns.collector.calendar_service import CalendarService
        day_type = CalendarService().get_day_type(dt.date()).value

    weather = _fetch_weather(db_url, station_id)
    station_name = _fetch_station_name(db_url, station_id)
    features = _build_features(
        station_id, dt, day_type, prev_stop_delay_min, planned_sequence, weather
    )

    result = model.predict_with_intervals(features)
    explanation = model.explain(features)

    return XGBPredictionResponse(
        station_id=station_id,
        station_name=station_name,
        predicted_delay_min=result["prediction"],
        p75_delay_min=result["p75"],
        confidence_interval=[result["ci_low"], result["ci_high"]],
        model="xgboost",
        model_date=getattr(model, "trained_date", None),
        explanation=[ExplanationItem(**e) for e in explanation],
    )


@app.get("/predict/baseline", response_model=BaselinePredictionResponse)
def predict_baseline(
    request: Request,
    station_id: str = Query(..., description="ID stacji PKP (np. 33506)"),
    planned_departure: str = Query(
        ..., description="Planowany odjazd ISO 8601 (np. 2026-05-31T10:00:00)"
    ),
    day_type: Optional[str] = Query(None, description="Typ dnia (auto-detect jeśli pominięty)"),
    db_url: str = Depends(_db_url),
):
    """Predykcja opóźnienia przez model historycznych median (benchmark)."""
    model = getattr(request.app.state, "baseline_model", None)
    if model is None:
        raise HTTPException(
            status_code=503,
            detail="Model w trakcie ładowania. Uruchom: python -m cns.ml.train_baseline",
        )

    try:
        dt = datetime.fromisoformat(planned_departure)
    except ValueError:
        raise HTTPException(400, f"Nieprawidłowy format daty: '{planned_departure}'. Użyj ISO 8601.")

    if day_type is None:
        from cns.collector.calendar_service import CalendarService
        day_type = CalendarService().get_day_type(dt.date()).value

    station_name = _fetch_station_name(db_url, station_id)
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


@app.get("/rankings/all-time", response_model=list[AllTimeRankingEntry])
def rankings_all_time(
    limit: int = Query(default=10, ge=1, le=100, description="Top N wyników"),
    db_url: str = Depends(_db_url),
):
    """Ranking pociągów z najwyższymi opóźnieniami od początku notowań."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH top_stops AS (
                        -- Skan po indeksie idx_station_stops_delay zamiast full table scan.
                        -- 50k rekordow z 65M+ to ulamek tabeli — konczy sie w <1s.
                        SELECT train_op_id, delay_departure_min
                        FROM station_stops
                        WHERE delay_departure_min > 0
                        ORDER BY delay_departure_min DESC
                        LIMIT 50000
                    ),
                    deduped_runs AS (
                        -- Deduplikacja: ten sam kurs może mieć wiele train_op_id
                        -- (jeden na snapshot). Zachowujemy najwyższe opóźnienie per kurs.
                        SELECT DISTINCT ON (to_.schedule_id, to_.order_id, to_.operating_date)
                            to_.schedule_id,
                            to_.order_id,
                            to_.operating_date,
                            ts.delay_departure_min AS max_delay_min
                        FROM top_stops ts
                        JOIN train_operations to_ ON ts.train_op_id = to_.id
                        ORDER BY to_.schedule_id, to_.order_id, to_.operating_date,
                                 ts.delay_departure_min DESC
                    )
                    SELECT
                        sc.national_number  AS train_number,
                        sc.train_name,
                        c.name              AS carrier_name,
                        dr.operating_date,
                        dr.max_delay_min
                    FROM deduped_runs dr
                    JOIN schedules sc ON sc.schedule_id    = dr.schedule_id
                                    AND sc.order_id        = dr.order_id
                                    AND sc.operating_date  = dr.operating_date
                                    AND sc.national_number IS NOT NULL
                    LEFT JOIN carriers c ON c.code = sc.carrier_code
                    ORDER BY dr.max_delay_min DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        AllTimeRankingEntry(
            train_number=r[0], train_name=r[1], carrier_name=r[2],
            operating_date=str(r[3]) if r[3] is not None else None,
            max_delay_min=r[4],
        )
        for r in rows
    ]


@app.get("/rankings/daily", response_model=list[DailyRankingEntry])
def rankings_daily(
    date: str = Query(
        ..., description="Data w formacie YYYY-MM-DD (np. 2026-06-04)"
    ),
    limit: int = Query(default=10, ge=1, le=100, description="Top N wyników"),
    db_url: str = Depends(_db_url),
):
    """Ranking pociągów z najwyższymi opóźnieniami w danym dniu."""
    from datetime import date as date_type

    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        query_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, f"Nieprawidłowy format daty: '{date}'. Użyj YYYY-MM-DD.")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        sc.national_number      AS train_number,
                        sc.train_name,
                        c.name                  AS carrier_name,
                        MAX(ss.delay_departure_min) AS max_delay_min
                    FROM station_stops ss
                    JOIN train_operations to_ ON ss.train_op_id = to_.id
                    JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                                    AND sc.order_id        = to_.order_id
                                    AND sc.operating_date  = to_.operating_date
                                    AND sc.national_number IS NOT NULL
                    LEFT JOIN carriers c ON c.code = sc.carrier_code
                    WHERE ss.delay_departure_min IS NOT NULL
                      AND ss.delay_departure_min > 0
                      AND to_.operating_date = %s
                    GROUP BY to_.schedule_id, to_.order_id,
                             sc.national_number, sc.train_name, c.name
                    ORDER BY max_delay_min DESC
                    LIMIT %s
                    """,
                    (query_date, limit),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        DailyRankingEntry(
            train_number=r[0], train_name=r[1], carrier_name=r[2],
            max_delay_min=r[3],
        )
        for r in rows
    ]


@app.get("/rankings/monthly/trains", response_model=list[MonthlyTrainRankingEntry])
def rankings_monthly_trains(
    year: int = Query(..., ge=2024, le=2030, description="Rok (np. 2026)"),
    month: int = Query(..., ge=1, le=12, description="Miesiąc (1–12)"),
    limit: int = Query(default=10, ge=1, le=100, description="Top N wyników"),
    db_url: str = Depends(_db_url),
):
    """Ranking pociągów z największą łączną liczbą minut opóźnień w danym miesiącu."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH train_run_max AS (
                        SELECT
                            to_.schedule_id, to_.order_id, to_.operating_date,
                            sc.national_number  AS train_number,
                            sc.train_name,
                            c.name              AS carrier_name,
                            MAX(ss.delay_departure_min) AS max_delay_run
                        FROM station_stops ss
                        JOIN train_operations to_ ON ss.train_op_id = to_.id
                        LEFT JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                                             AND sc.order_id        = to_.order_id
                                             AND sc.operating_date  = to_.operating_date
                        LEFT JOIN carriers c ON c.code = sc.carrier_code
                        WHERE ss.delay_departure_min IS NOT NULL
                          AND ss.delay_departure_min > 0
                          AND sc.national_number IS NOT NULL
                          AND EXTRACT(YEAR  FROM to_.operating_date) = %s
                          AND EXTRACT(MONTH FROM to_.operating_date) = %s
                        GROUP BY to_.schedule_id, to_.order_id, to_.operating_date,
                                 sc.national_number, sc.train_name, c.name
                    )
                    SELECT
                        train_number,
                        train_name,
                        carrier_name,
                        COUNT(*)                    AS trip_count,
                        SUM(max_delay_run)          AS total_delay_min,
                        ROUND(AVG(max_delay_run), 1) AS avg_delay_min
                    FROM train_run_max
                    GROUP BY train_number, train_name, carrier_name
                    ORDER BY total_delay_min DESC
                    LIMIT %s
                    """,
                    (year, month, limit),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        MonthlyTrainRankingEntry(
            train_number=r[0], train_name=r[1], carrier_name=r[2],
            trip_count=r[3] or 0,
            total_delay_min=r[4],
            avg_delay_min=float(r[5]) if r[5] is not None else None,
        )
        for r in rows
    ]


@app.get("/rankings/monthly/carriers", response_model=list[MonthlyCarrierRankingEntry])
def rankings_monthly_carriers(
    year: int = Query(..., ge=2024, le=2030, description="Rok (np. 2026)"),
    month: int = Query(..., ge=1, le=12, description="Miesiąc (1–12)"),
    db_url: str = Depends(_db_url),
):
    """Ranking przewoźników z największą łączną liczbą minut opóźnień w danym miesiącu."""
    try:
        import psycopg
    except ImportError:
        raise HTTPException(status_code=503, detail="Zainstaluj: poetry install -E api")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH train_run_max AS (
                        SELECT
                            to_.schedule_id, to_.order_id, to_.operating_date,
                            c.name AS carrier_name,
                            MAX(ss.delay_departure_min) AS max_delay_run
                        FROM station_stops ss
                        JOIN train_operations to_ ON ss.train_op_id = to_.id
                        LEFT JOIN schedules sc ON sc.schedule_id    = to_.schedule_id
                                             AND sc.order_id        = to_.order_id
                                             AND sc.operating_date  = to_.operating_date
                        LEFT JOIN carriers c ON c.code = sc.carrier_code
                        WHERE ss.delay_departure_min IS NOT NULL
                          AND ss.delay_departure_min > 0
                          AND c.name IS NOT NULL
                          AND EXTRACT(YEAR  FROM to_.operating_date) = %s
                          AND EXTRACT(MONTH FROM to_.operating_date) = %s
                        GROUP BY to_.schedule_id, to_.order_id, to_.operating_date, c.name
                    )
                    SELECT
                        carrier_name,
                        COUNT(*)                    AS trip_count,
                        SUM(max_delay_run)          AS total_delay_min,
                        ROUND(AVG(max_delay_run), 1) AS avg_delay_min
                    FROM train_run_max
                    GROUP BY carrier_name
                    ORDER BY total_delay_min DESC
                    """,
                    (year, month),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy danych: {e}")

    return [
        MonthlyCarrierRankingEntry(
            carrier_name=r[0],
            trip_count=r[1] or 0,
            total_delay_min=r[2],
            avg_delay_min=float(r[3]) if r[3] is not None else None,
        )
        for r in rows
    ]
