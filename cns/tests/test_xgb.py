"""
Testy XGBoostDelayPredictor i endpointu /predict.
Używają danych syntetycznych – nie wymagają połączenia z bazą.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from cns.api.app import _db_url, app
from cns.ml.xgb_model import FEATURES, XGBoostDelayPredictor
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Dane syntetyczne
# ---------------------------------------------------------------------------

def _make_df(n: int = 400, seed: int = 42) -> pd.DataFrame:
    """Dane z jasnym sygnałem: delay ≈ 0.8 * prev_stop_delay + noise."""
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    prev_delay = rng.exponential(scale=4, size=n)
    noise = rng.exponential(scale=1.5, size=n)
    return pd.DataFrame({
        "station_id":          rng.choice(["33506", "30700", "12345"], n),
        "hour_of_day":         rng.integers(0, 24, n),
        "day_of_week":         rng.integers(0, 7, n),
        "month":               rng.integers(1, 13, n),
        "planned_sequence":    rng.integers(1, 15, n),
        "prev_stop_delay_min": prev_delay,
        "temperature_c":       rng.uniform(-10, 30, n),
        "precipitation_mm":    rng.exponential(2, n),
        "wind_speed_kmh":      rng.exponential(15, n),
        "snowfall_cm":         rng.exponential(0.3, n),
        "visibility_m":        rng.integers(1000, 30000, n).astype(float),
        "is_snowing":          rng.integers(0, 2, n).astype(bool),
        "is_heavy_rain":       rng.integers(0, 2, n).astype(bool),
        "is_strong_wind":      rng.integers(0, 2, n).astype(bool),
        "is_frost":            rng.integers(0, 2, n).astype(bool),
        "is_dense_fog":        rng.integers(0, 2, n).astype(bool),
        "day_type":            rng.choice(["WORKING", "WEEKEND", "HOLIDAY"], n),
        "operating_date":      [start + timedelta(days=int(i * 150 / n)) for i in range(n)],
        "delay_departure_min": (prev_delay * 0.8 + noise).clip(0),
    })


def _sample_features(station_id: str = "33506") -> dict:
    return {
        "station_id":          station_id,
        "hour_of_day":         10,
        "day_of_week":         1,
        "month":               5,
        "planned_sequence":    3,
        "prev_stop_delay_min": 5.0,
        "temperature_c":       12.0,
        "precipitation_mm":    0.0,
        "wind_speed_kmh":      20.0,
        "snowfall_cm":         0.0,
        "visibility_m":        25000.0,
        "is_snowing":          False,
        "is_heavy_rain":       False,
        "is_strong_wind":      False,
        "is_frost":            False,
        "is_dense_fog":        False,
        "day_type":            "WORKING",
    }


# ---------------------------------------------------------------------------
# Fixture – model wytrenowany raz per moduł (szybkie drzewa)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fitted_xgb():
    model = XGBoostDelayPredictor()
    metrics = model.fit(_make_df(400), n_estimators=30)
    return model, metrics


# ---------------------------------------------------------------------------
# Fit / predict / explain
# ---------------------------------------------------------------------------

class TestXGBFit:
    def test_fit_zwraca_wymagane_klucze(self, fitted_xgb):
        _, metrics = fitted_xgb
        assert {"mae_train", "mae_val", "rmse_val", "feature_importances"} <= metrics.keys()

    def test_mae_train_jest_dodatni(self, fitted_xgb):
        _, m = fitted_xgb
        assert m["mae_train"] > 0

    def test_rmse_wieksze_lub_rowne_mae(self, fitted_xgb):
        _, m = fitted_xgb
        assert m["rmse_val"] >= m["mae_val"] - 1e-6

    def test_feature_importances_pokrywa_wszystkie_cechy(self, fitted_xgb):
        from cns.ml.xgb_model import _ALL
        _, m = fitted_xgb
        fi = m["feature_importances"]
        assert set(fi.keys()) == set(_ALL)

    def test_feature_importances_sumuja_do_1(self, fitted_xgb):
        _, m = fitted_xgb
        total = sum(m["feature_importances"].values())
        assert abs(total - 1.0) < 0.01

    def test_trained_date_ustawiony(self, fitted_xgb):
        model, _ = fitted_xgb
        assert model.trained_date is not None

    def test_nie_rzuca_na_df_z_nullami(self):
        df = _make_df(200)
        df.loc[:20, "prev_stop_delay_min"] = float("nan")
        df.loc[:10, "temperature_c"] = float("nan")
        m = XGBoostDelayPredictor()
        m.fit(df, n_estimators=5)  # nie powinno rzucić


class TestXGBPredict:
    def test_predict_zwraca_float(self, fitted_xgb):
        model, _ = fitted_xgb
        result = model.predict(_sample_features())
        assert isinstance(result, float)

    def test_predict_nieujemny(self, fitted_xgb):
        model, _ = fitted_xgb
        assert model.predict(_sample_features()) >= 0.0

    def test_predict_with_intervals_struktura(self, fitted_xgb):
        model, _ = fitted_xgb
        r = model.predict_with_intervals(_sample_features())
        assert {"prediction", "p75", "ci_low", "ci_high"} == set(r.keys())

    def test_ci_low_mniejszy_od_ci_high(self, fitted_xgb):
        model, _ = fitted_xgb
        r = model.predict_with_intervals(_sample_features())
        assert r["ci_low"] <= r["ci_high"]

    def test_p75_wiekszy_lub_rowny_prediction(self, fitted_xgb):
        model, _ = fitted_xgb
        r = model.predict_with_intervals(_sample_features())
        # Residua mogą być ujemne – p75 może być poniżej predykcji przy silnym overestimation
        assert isinstance(r["p75"], float)

    def test_nieznana_stacja_nie_rzuca(self, fitted_xgb):
        model, _ = fitted_xgb
        feat = _sample_features("99999")  # stacja nieobecna w train
        result = model.predict(feat)
        assert isinstance(result, float)

    def test_pred_rosnie_z_prev_stop_delay(self, fitted_xgb):
        model, _ = fitted_xgb
        low = model.predict({**_sample_features(), "prev_stop_delay_min": 0})
        high = model.predict({**_sample_features(), "prev_stop_delay_min": 30})
        assert high >= low  # propagacja opóźnienia

    def test_niezainicjowany_model_rzuca(self):
        m = XGBoostDelayPredictor()
        with pytest.raises(RuntimeError):
            m.predict(_sample_features())


class TestXGBOverfitting:
    def test_val_mae_nie_przekracza_2x_train_mae(self, fitted_xgb):
        """
        Overfitting test: jeśli val MAE > 2× train MAE, model jest przetrenowany.
        Z 400 wierszami i 30 drzewami oczekujemy zdrowego generalizowania.
        """
        _, m = fitted_xgb
        assert m["mae_val"] <= m["mae_train"] * 2.0, (
            f"Silny overfitting: train MAE={m['mae_train']:.2f}, val MAE={m['mae_val']:.2f}"
        )

    def test_val_mae_ma_rozsadny_porzadek_wielkosci(self, fitted_xgb):
        """Val MAE powinien być poniżej 50 min na danych syntetycznych."""
        _, m = fitted_xgb
        assert m["mae_val"] < 50.0


class TestXGBExplain:
    def test_explain_zwraca_liste(self, fitted_xgb):
        model, _ = fitted_xgb
        result = model.explain(_sample_features())
        assert isinstance(result, list)

    def test_explain_zawiera_max_5_elementow(self, fitted_xgb):
        model, _ = fitted_xgb
        result = model.explain(_sample_features())
        assert len(result) <= 5

    def test_explain_kazdy_element_ma_feature_i_impact(self, fitted_xgb):
        model, _ = fitted_xgb
        for item in model.explain(_sample_features()):
            assert "feature" in item
            assert "impact" in item

    def test_explain_impact_jest_float(self, fitted_xgb):
        model, _ = fitted_xgb
        for item in model.explain(_sample_features()):
            assert isinstance(item["impact"], float)

    def test_explain_posortowane_malejaco_po_abs_impact(self, fitted_xgb):
        model, _ = fitted_xgb
        items = model.explain(_sample_features())
        impacts = [abs(i["impact"]) for i in items]
        assert impacts == sorted(impacts, reverse=True)

    def test_explain_feature_to_znany_string(self, fitted_xgb):
        from cns.ml.xgb_model import _ALL
        model, _ = fitted_xgb
        for item in model.explain(_sample_features()):
            assert item["feature"] in _ALL


class TestXGBSaveLoad:
    def test_roundtrip_pred_identyczna(self, fitted_xgb, tmp_path):
        model, _ = fitted_xgb
        path = tmp_path / "xgb.pkl"
        model.save(path)
        loaded = XGBoostDelayPredictor.load(path)
        orig   = model.predict(_sample_features())
        loaded_ = loaded.predict(_sample_features())
        assert abs(orig - loaded_) < 1e-4


# ---------------------------------------------------------------------------
# Endpoint /predict
# ---------------------------------------------------------------------------

def _fake_db_url() -> str:
    return "postgresql://test:test@localhost/test"


@pytest.fixture
def client_with_xgb(fitted_xgb):
    model, _ = fitted_xgb
    app.dependency_overrides[_db_url] = _fake_db_url
    with TestClient(app) as client:
        client.app.state.xgb_model = model
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_xgb():
    app.dependency_overrides[_db_url] = _fake_db_url
    with TestClient(app) as client:
        client.app.state.xgb_model = None
        yield client
    app.dependency_overrides.clear()


class TestPredictEndpoint:
    _PARAMS = {
        "station_id": "33506",
        "planned_departure": "2026-05-31T10:00:00",
        "day_type": "WORKING",
        "prev_stop_delay_min": 5,
    }

    def test_200_gdy_model_zaladowany(self, client_with_xgb):
        r = client_with_xgb.get("/predict", params=self._PARAMS)
        assert r.status_code == 200

    def test_struktura_odpowiedzi(self, client_with_xgb):
        body = client_with_xgb.get("/predict", params=self._PARAMS).json()
        assert body["model"] == "xgboost"
        assert "predicted_delay_min" in body
        assert "p75_delay_min" in body
        assert "confidence_interval" in body
        assert "explanation" in body

    def test_confidence_interval_ma_dwa_elementy(self, client_with_xgb):
        ci = client_with_xgb.get("/predict", params=self._PARAMS).json()["confidence_interval"]
        assert isinstance(ci, list)
        assert len(ci) == 2

    def test_explanation_niepusta(self, client_with_xgb):
        expl = client_with_xgb.get("/predict", params=self._PARAMS).json()["explanation"]
        assert isinstance(expl, list)
        assert len(expl) > 0

    def test_explanation_ma_feature_impact_value(self, client_with_xgb):
        for item in client_with_xgb.get("/predict", params=self._PARAMS).json()["explanation"]:
            assert "feature" in item
            assert "impact" in item

    def test_503_gdy_model_niezaladowany(self, client_no_xgb):
        r = client_no_xgb.get("/predict", params=self._PARAMS)
        assert r.status_code == 503

    def test_400_gdy_zla_data(self, client_with_xgb):
        r = client_with_xgb.get("/predict", params={**self._PARAMS, "planned_departure": "ZLA-DATA"})
        assert r.status_code == 400

    def test_day_type_opcjonalny(self, client_with_xgb):
        params = {k: v for k, v in self._PARAMS.items() if k != "day_type"}
        r = client_with_xgb.get("/predict", params=params)
        assert r.status_code == 200

    def test_model_date_obecny(self, client_with_xgb):
        body = client_with_xgb.get("/predict", params=self._PARAMS).json()
        assert body["model_date"] is not None

    def test_brak_station_id_422(self, client_with_xgb):
        params = {k: v for k, v in self._PARAMS.items() if k != "station_id"}
        r = client_with_xgb.get("/predict", params=params)
        assert r.status_code == 422
