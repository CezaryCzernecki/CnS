"""
BaselineModel – predykcja opóźnienia jako historyczna mediana
per (station_id, hour_bucket, day_type).

hour_bucket = hour // 2  →  12 bucketów dziennie (mniej szumu niż 24).

Hierarchia fallback:
  1. (station_id, hour_bucket, day_type)  – dokładne
  2. (station_id, hour_bucket)            – bez day_type
  3. (station_id,)                        – tylko stacja
  4. global                               – ostateczny fallback
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import pandas as pd


# ---------------------------------------------------------------------------
# Typy wewnętrzne
# ---------------------------------------------------------------------------

_Stats = dict  # keys: mean, median, p75, p90, count


def _compute_stats(values: list[float]) -> Optional[_Stats]:
    n = len(values)
    if n == 0:
        return None
    sorted_v = sorted(values)

    def _pct(p: float) -> float:
        return float(sorted_v[min(n - 1, int(p * n))])

    return {
        "mean":   statistics.fmean(values),
        "median": float(statistics.median(values)),
        "p75":    _pct(0.75),
        "p90":    _pct(0.90),
        "count":  n,
    }


def _prediction(stats: _Stats, fallback: bool) -> "BaselinePrediction":
    return BaselinePrediction(
        mean_delay=stats["mean"],
        median_delay=stats["median"],
        p75_delay=stats["p75"],
        p90_delay=stats["p90"],
        sample_count=stats["count"],
        fallback=fallback,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class BaselinePrediction:
    mean_delay:   Optional[float]
    median_delay: Optional[float]
    p75_delay:    Optional[float]
    p90_delay:    Optional[float]
    sample_count: int
    fallback: bool  # True gdy nie znaleziono dokładnego bucketu (station, hour, day_type)


class BaselineModel:
    """
    Model historycznych median jako benchmark (baseline) dla predykcji opóźnień PKP.
    Używa wyłącznie pandas do trenowania; dane wewnętrzne to czyste słowniki Python.
    """

    def __init__(self) -> None:
        self._l1: dict[tuple, _Stats] = {}   # (station_id, hour_bucket, day_type)
        self._l2: dict[tuple, _Stats] = {}   # (station_id, hour_bucket)
        self._l3: dict[str, _Stats] = {}     # station_id
        self._global: Optional[_Stats] = None
        self.trained_date: Optional[str] = None

    def fit(self, df: "pd.DataFrame", trained_date: Optional[str] = None) -> None:
        """
        Trenuje model na DataFrame z kolumnami:
          station_id, hour_of_day, day_type, delay_departure_min
        """
        from datetime import date as _date

        col = "delay_departure_min"
        df = df.copy()
        df = df[df[col].notna()]
        df["_hb"] = (df["hour_of_day"] // 2).astype(int)

        self.trained_date = trained_date or _date.today().isoformat()

        # L4 – global
        self._global = _compute_stats([float(v) for v in df[col]])

        # L3 – per station
        for sid, grp in df.groupby("station_id"):
            s = _compute_stats([float(v) for v in grp[col]])
            if s:
                self._l3[str(sid)] = s

        # L2 – per (station, hour_bucket)
        for (sid, hb), grp in df.groupby(["station_id", "_hb"]):
            s = _compute_stats([float(v) for v in grp[col]])
            if s:
                self._l2[(str(sid), int(hb))] = s

        # L1 – per (station, hour_bucket, day_type)
        df_dt = df[df["day_type"].notna()]
        for (sid, hb, dt), grp in df_dt.groupby(["station_id", "_hb", "day_type"]):
            s = _compute_stats([float(v) for v in grp[col]])
            if s:
                self._l1[(str(sid), int(hb), str(dt))] = s

    def predict(self, station_id: str, hour: int, day_type: str) -> BaselinePrediction:
        hb = hour // 2
        sid = str(station_id)
        dt = str(day_type)

        if (sid, hb, dt) in self._l1:
            return _prediction(self._l1[(sid, hb, dt)], fallback=False)
        if (sid, hb) in self._l2:
            return _prediction(self._l2[(sid, hb)], fallback=True)
        if sid in self._l3:
            return _prediction(self._l3[sid], fallback=True)
        if self._global:
            return _prediction(self._global, fallback=True)
        return BaselinePrediction(
            mean_delay=None, median_delay=None,
            p75_delay=None, p90_delay=None,
            sample_count=0, fallback=True,
        )

    def save(self, path: Path) -> None:
        import joblib
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: Path) -> "BaselineModel":
        import joblib
        return joblib.load(path)
