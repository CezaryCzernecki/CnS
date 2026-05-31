"""
Trenuje BaselineModel na danych z mv_training_features.

Użycie:
    poetry run python -m cns.ml.train_baseline

Zmienne środowiskowe:
    DATABASE_URL  – URL połączenia PostgreSQL
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from math import sqrt
from pathlib import Path


def _evaluate(model, val_df) -> tuple[float, float, float]:
    """Zwraca (MAE, RMSE, coverage%) na zbiorze walidacyjnym."""
    import pandas as pd

    errors_abs: list[float] = []
    errors_sq:  list[float] = []
    exact = 0
    total = 0

    for _, row in val_df[val_df["delay_departure_min"].notna()].iterrows():
        pred = model.predict(
            str(row["station_id"]),
            int(row["hour_of_day"]),
            str(row["day_type"]) if pd.notna(row.get("day_type")) else "WORKING",
        )
        if pred.median_delay is None:
            continue
        err = abs(float(pred.median_delay) - float(row["delay_departure_min"]))
        errors_abs.append(err)
        errors_sq.append(err ** 2)
        if not pred.fallback:
            exact += 1
        total += 1

    if not errors_abs:
        return 0.0, 0.0, 0.0

    mae = sum(errors_abs) / len(errors_abs)
    rmse = sqrt(sum(errors_sq) / len(errors_sq))
    coverage = exact / total * 100 if total else 0.0
    return mae, rmse, coverage


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: Ustaw zmienną DATABASE_URL", file=sys.stderr)
        sys.exit(1)

    try:
        import pandas as pd
        import psycopg
    except ImportError as e:
        print(f"ERROR: {e}. Zainstaluj: poetry install -E ml", file=sys.stderr)
        sys.exit(1)

    from cns.ml.baseline_model import BaselineModel

    today = date.today()
    cutoff = today - timedelta(days=90)
    split_date = today - timedelta(days=18)

    print(f"Pobieranie danych z mv_training_features (od {cutoff})...")

    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT station_id, hour_of_day, day_type,
                           delay_departure_min, operating_date
                    FROM mv_training_features
                    WHERE operating_date >= %s
                    ORDER BY operating_date
                    """,
                    (cutoff,),
                )
                rows = cur.fetchall()
                cols = [d.name for d in cur.description]
    except Exception as e:
        print(f"ERROR: Nie udało się pobrać danych: {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print(
            "ERROR: Brak danych w mv_training_features.\n"
            "       Uruchom: REFRESH MATERIALIZED VIEW mv_training_features",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.DataFrame(rows, columns=cols)
    df["operating_date"] = pd.to_datetime(df["operating_date"]).dt.date

    train = df[df["operating_date"] < split_date].copy()
    val   = df[df["operating_date"] >= split_date].copy()

    print(f"Train: {len(train):>8,} wierszy  ({cutoff} – {split_date})")
    print(f"Val:   {len(val):>8,} wierszy  ({split_date} – {today})")

    model = BaselineModel()
    model.fit(train, trained_date=today.isoformat())

    mae, rmse, coverage = _evaluate(model, val)
    print(f"\nMAE:      {mae:.2f} min")
    print(f"RMSE:     {rmse:.2f} min")
    print(f"Coverage: {coverage:.1f}%  (% predykcji z dokładnego bucketu L1)")

    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)
    path = models_dir / f"baseline_v{today.strftime('%Y%m%d')}.pkl"
    model.save(path)
    print(f"\nZapisano model → {path}")


if __name__ == "__main__":
    main()
