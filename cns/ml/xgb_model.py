"""
XGBoostDelayPredictor – predykcja opóźnień pociągów PKP.

Techniki:
- Target encoding cech kategorycznych (station_id, day_type)
  – obliczany wyłącznie na zbiorze treningowym (brak data leakage)
- Podział po dacie 80/20 (chronologiczny, nie losowy)
- Przedziały ufności z percentyli residuów walidacyjnych
- SHAP TreeExplainer – wyjaśnialność predykcji w minutach
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

FEATURES = [
    "hour_of_day", "day_of_week", "month", "planned_sequence",
    "prev_stop_delay_min",
    "temperature_c", "precipitation_mm", "wind_speed_kmh",
    "snowfall_cm", "visibility_m",
    "is_snowing", "is_heavy_rain", "is_strong_wind", "is_frost", "is_dense_fog",
]
CATEGORICAL = ["station_id", "day_type"]
_ALL = FEATURES + CATEGORICAL  # 17 cech łącznie

_TARGET = "delay_departure_min"


class XGBoostDelayPredictor:

    def __init__(self) -> None:
        self._model = None
        self._encoders: dict[str, dict] = {}
        self._global_mean: float = 0.0
        # Percentyle residuów (actual − predicted) z val – do przedziałów ufności
        self._res_p15: float = 0.0
        self._res_p75: float = 0.0
        self._res_p85: float = 0.0
        self.trained_date: Optional[str] = None

    # ------------------------------------------------------------------
    # Budowanie macierzy cech
    # ------------------------------------------------------------------

    def _df_to_X(self, df: "pd.DataFrame") -> "np.ndarray":
        import numpy as np
        import pandas as pd

        cols: dict = {}
        for col in FEATURES:
            if col in df.columns:
                cols[col] = df[col].fillna(0).astype(float)
            else:
                cols[col] = pd.Series(0.0, index=df.index)
        for col in CATEGORICAL:
            enc = self._encoders.get(col, {})
            cols[col] = df[col].map(enc).fillna(self._global_mean).astype(float)
        return pd.DataFrame(cols, index=df.index)[_ALL].values.astype(np.float32)

    def _dict_to_X(self, features: dict) -> "np.ndarray":
        import numpy as np

        row: list[float] = []
        for col in FEATURES:
            v = features.get(col)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = 0.0
            row.append(float(v))
        for col in CATEGORICAL:
            v = str(features.get(col, ""))
            row.append(float(self._encoders.get(col, {}).get(v, self._global_mean)))
        return np.array([row], dtype=np.float32)

    # ------------------------------------------------------------------
    # Trenowanie
    # ------------------------------------------------------------------

    def fit(self, df: "pd.DataFrame", n_estimators: int = 500) -> dict:
        """
        Trenuje model na DataFrame z mv_training_features.
        Podział chronologiczny 80/20 po kolumnie 'operating_date'.

        Zwraca:
            {mae_train, mae_val, rmse_val, feature_importances: dict}
        """
        import numpy as np
        from datetime import date as _date
        from xgboost import XGBRegressor

        self.trained_date = _date.today().isoformat()

        df = df[df[_TARGET].notna()].copy()
        df = df.sort_values("operating_date").reset_index(drop=True)

        split = int(len(df) * 0.8)
        train, val = df.iloc[:split], df.iloc[split:]

        # Target encoding – TYLKO na zbiorze treningowym
        self._global_mean = float(train[_TARGET].mean())
        for col in CATEGORICAL:
            self._encoders[col] = train.groupby(col)[_TARGET].mean().to_dict()

        X_tr = self._df_to_X(train)
        y_tr = train[_TARGET].values.astype(np.float32)
        X_val = self._df_to_X(val)
        y_val = val[_TARGET].values.astype(np.float32)

        model = XGBRegressor(
            n_estimators=n_estimators,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            early_stopping_rounds=min(20, max(5, n_estimators // 25)),
            eval_metric="mae",
            random_state=42,
            tree_method="hist",
        )
        model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
        self._model = model

        tr_preds = model.predict(X_tr)
        v_preds  = model.predict(X_val)

        # Residua walidacyjne (actual − predicted) → CI i p75
        res = y_val - v_preds
        self._res_p15 = float(np.percentile(res, 15))
        self._res_p75 = float(np.percentile(res, 75))
        self._res_p85 = float(np.percentile(res, 85))

        importances = dict(zip(_ALL, model.feature_importances_))

        return {
            "mae_train": float(np.mean(np.abs(tr_preds - y_tr))),
            "mae_val":   float(np.mean(np.abs(v_preds - y_val))),
            "rmse_val":  float(np.sqrt(np.mean((v_preds - y_val) ** 2))),
            "feature_importances": importances,
        }

    # ------------------------------------------------------------------
    # Inferencia
    # ------------------------------------------------------------------

    def predict(self, features: dict) -> float:
        if self._model is None:
            raise RuntimeError("Wywołaj fit() lub load() przed predict().")
        X = self._dict_to_X(features)
        return float(max(0.0, self._model.predict(X)[0]))

    def predict_with_intervals(self, features: dict) -> dict:
        pred = self.predict(features)
        return {
            "prediction": pred,
            "p75":        max(0.0, pred + self._res_p75),
            "ci_low":     max(0.0, pred + self._res_p15),
            "ci_high":    max(0.0, pred + self._res_p85),
        }

    def explain(self, features: dict) -> list[dict]:
        """Zwraca top-5 cech wg SHAP – wpływ na predykcję w minutach."""
        import shap

        X = self._dict_to_X(features)
        explainer = shap.TreeExplainer(self._model)
        sv = explainer.shap_values(X)   # (1, n_features)
        pairs = sorted(zip(_ALL, sv[0]), key=lambda x: abs(x[1]), reverse=True)
        return [
            {"feature": name, "impact": float(val), "value": features.get(name)}
            for name, val in pairs[:5]
        ]

    # ------------------------------------------------------------------
    # Serializacja
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        import joblib
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: Path) -> "XGBoostDelayPredictor":
        import joblib
        return joblib.load(path)
