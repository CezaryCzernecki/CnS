"""
Trenuje XGBoostDelayPredictor na danych z mv_training_features.

Użycie:
    poetry run python -m cns.ml.train_xgb

Zmienne środowiskowe:
    DATABASE_URL  – URL PostgreSQL

Walidacja modelu (gate jakości):
    val_MAE <= baseline_MAE * 0.85  (wymagana ≥15% poprawa vs. baseline)
    Jeśli warunek nie spełniony – model NIE jest zapisywany do models/
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from math import sqrt
from pathlib import Path

_QUERY_COLS = [
    "station_id", "hour_of_day", "day_of_week", "month", "planned_sequence",
    "prev_stop_delay_min",
    "temperature_c", "precipitation_mm", "wind_speed_kmh",
    "snowfall_cm", "visibility_m",
    "is_snowing", "is_heavy_rain", "is_strong_wind", "is_frost", "is_dense_fog",
    "day_type", "operating_date", "delay_departure_min",
]


def _baseline_mae(train_df, val_df) -> float:
    from cns.ml.baseline_model import BaselineModel
    import pandas as pd

    bl = BaselineModel()
    bl.fit(train_df)
    errors = []
    for _, row in val_df[val_df["delay_departure_min"].notna()].iterrows():
        dt = str(row.get("day_type") or "WORKING")
        pred = bl.predict(str(row["station_id"]), int(row["hour_of_day"]), dt)
        if pred.median_delay is not None:
            errors.append(abs(pred.median_delay - float(row["delay_departure_min"])))
    return sum(errors) / len(errors) if errors else float("inf")


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: Ustaw DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    try:
        import pandas as pd
        import psycopg
    except ImportError as e:
        print(f"ERROR: {e}. Zainstaluj: poetry install -E ml", file=sys.stderr)
        sys.exit(1)

    from cns.ml.xgb_model import XGBoostDelayPredictor

    today = date.today()
    cutoff = today - timedelta(days=180)

    print(f"Pobieranie danych z mv_training_features (od {cutoff})...")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(_QUERY_COLS)} FROM mv_training_features "
                    f"WHERE operating_date >= %s ORDER BY operating_date",
                    (cutoff,),
                )
                rows = cur.fetchall()
                cols = [d.name for d in cur.description]
    except Exception as e:
        print(f"ERROR: Nie udało się pobrać danych: {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print(
            "ERROR: Brak danych. Uruchom REFRESH MATERIALIZED VIEW mv_training_features.",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.DataFrame(rows, columns=cols)
    df["operating_date"] = pd.to_datetime(df["operating_date"]).dt.date

    df_clean = df[df["delay_departure_min"].notna()].sort_values("operating_date")
    n = len(df_clean)
    split = int(n * 0.8)
    train_df = df_clean.iloc[:split].copy()
    val_df   = df_clean.iloc[split:].copy()

    print(f"Train: {len(train_df):>8,} wierszy")
    print(f"Val:   {len(val_df):>8,} wierszy")

    # ---- Baseline MAE dla porównania (na tym samym splicie) ----
    print("\nObliczam MAE baseline...")
    bl_mae = _baseline_mae(train_df, val_df)
    print(f"Baseline MAE: {bl_mae:.2f} min")

    # ---- Trenuj XGBoost ----
    print("\nTrenuję XGBoostDelayPredictor (500 drzew, early stopping)...")
    xgb = XGBoostDelayPredictor()
    metrics = xgb.fit(df_clean)

    print(f"XGB MAE train: {metrics['mae_train']:.2f} min")
    print(f"XGB MAE val:   {metrics['mae_val']:.2f} min")
    print(f"XGB RMSE val:  {metrics['rmse_val']:.2f} min")

    improvement = (bl_mae - metrics["mae_val"]) / bl_mae * 100 if bl_mae > 0 else 0
    print(f"Poprawa vs baseline: {improvement:+.1f}%")

    # ---- Feature importance ----
    fi = sorted(
        metrics["feature_importances"].items(),
        key=lambda x: x[1], reverse=True,
    )
    print("\nFeature importance (top-10):")
    for rank, (name, val) in enumerate(fi[:10], 1):
        print(f"  {rank:2d}. {name:<30s} {val:.4f}")

    # ---- Gate jakości ----
    threshold = bl_mae * 0.85
    if metrics["mae_val"] > threshold:
        print(
            f"\n⚠️  OSTRZEŻENIE: val MAE {metrics['mae_val']:.2f} > próg {threshold:.2f} "
            f"(baseline * 0.85). Model NIE zapisany.",
            file=sys.stderr,
        )
        sys.exit(2)

    # ---- Zapisz ----
    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)
    path = models_dir / f"xgb_v{today.strftime('%Y%m%d')}.pkl"
    xgb.save(path)
    print(f"\nZapisano model → {path}")


if __name__ == "__main__":
    main()
