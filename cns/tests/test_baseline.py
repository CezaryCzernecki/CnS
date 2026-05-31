"""
Testy BaselineModel i endpointu /predict/baseline.
Nie wymagają połączenia z bazą danych – używają danych syntetycznych.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cns.api.app import _db_url, app
from cns.ml.baseline_model import BaselineModel, BaselinePrediction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)


def _make_df(
    n: int = 100,
    station_ids: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    stations = station_ids or ["33506"]
    return pd.DataFrame(
        {
            "station_id":         rng.choice(stations, n),
            "hour_of_day":        rng.integers(0, 24, n),
            "day_type":           rng.choice(["WORKING", "WEEKEND", "HOLIDAY"], n),
            "delay_departure_min": rng.exponential(scale=5, size=n).astype(float),
        }
    )


@pytest.fixture
def fitted_model() -> BaselineModel:
    df = _make_df(n=200, station_ids=["33506", "30700"])
    m = BaselineModel()
    m.fit(df, trained_date="2026-05-31")
    return m


# ---------------------------------------------------------------------------
# BaselineModel – fit / predict
# ---------------------------------------------------------------------------

class TestBaselineModelFit:
    def test_fit_nie_rzuca(self):
        m = BaselineModel()
        m.fit(_make_df(100))

    def test_trained_date_ustawiony(self):
        m = BaselineModel()
        m.fit(_make_df(50), trained_date="2026-06-01")
        assert m.trained_date == "2026-06-01"

    def test_trained_date_auto_gdy_nie_podany(self):
        m = BaselineModel()
        m.fit(_make_df(50))
        assert m.trained_date is not None

    def test_global_ustawiony_po_fit(self):
        m = BaselineModel()
        m.fit(_make_df(50))
        assert m._global is not None

    def test_l3_zawiera_station(self):
        m = BaselineModel()
        m.fit(_make_df(50, station_ids=["33506"]))
        assert "33506" in m._l3

    def test_l2_zawiera_krotke_station_hour_bucket(self):
        m = BaselineModel()
        m.fit(_make_df(200, station_ids=["33506"]))
        assert any(k[0] == "33506" for k in m._l2)

    def test_l1_zawiera_krotke_station_hour_bucket_daytype(self):
        m = BaselineModel()
        m.fit(_make_df(200, station_ids=["33506"]))
        assert any(k[0] == "33506" for k in m._l1)

    def test_pomija_wiersze_z_nan_delay(self):
        df = _make_df(50)
        df.loc[0:9, "delay_departure_min"] = float("nan")
        m = BaselineModel()
        m.fit(df)  # nie rzuca
        # Globalna liczba próbek powinna być ≤ 50
        assert m._global["count"] <= 50


class TestBaselineModelPredict:
    def test_zwraca_baseline_prediction(self, fitted_model):
        pred = fitted_model.predict("33506", 10, "WORKING")
        assert isinstance(pred, BaselinePrediction)

    def test_median_delay_jest_dodatni(self, fitted_model):
        pred = fitted_model.predict("33506", 10, "WORKING")
        assert pred.median_delay is not None
        assert pred.median_delay >= 0

    def test_p75_wieksze_lub_rowne_median(self, fitted_model):
        pred = fitted_model.predict("33506", 10, "WORKING")
        if pred.median_delay is not None and pred.p75_delay is not None:
            assert pred.p75_delay >= pred.median_delay - 0.01  # float tolerance

    def test_p90_wieksze_lub_rowne_p75(self, fitted_model):
        pred = fitted_model.predict("33506", 10, "WORKING")
        if pred.p75_delay is not None and pred.p90_delay is not None:
            assert pred.p90_delay >= pred.p75_delay - 0.01

    def test_sample_count_dodatni(self, fitted_model):
        pred = fitted_model.predict("33506", 10, "WORKING")
        assert pred.sample_count > 0

    def test_hour_bucket_konwersja(self, fitted_model):
        # hour=10 i hour=11 mają ten sam hour_bucket=5
        pred10 = fitted_model.predict("33506", 10, "WORKING")
        pred11 = fitted_model.predict("33506", 11, "WORKING")
        # Mogą różnić się tylko jeśli mają dane w L1 per godzinę (nie per bucket)
        # Tu sprawdzamy że oba działają bez błędu
        assert isinstance(pred10, BaselinePrediction)
        assert isinstance(pred11, BaselinePrediction)


# ---------------------------------------------------------------------------
# Fallback hierarchy
# ---------------------------------------------------------------------------

class TestFallbackHierarchy:
    def test_nieznana_stacja_fallback_true(self, fitted_model):
        pred = fitted_model.predict("99999", 10, "WORKING")
        assert pred.fallback is True

    def test_nieznana_stacja_uzywa_globalnego_fallback(self, fitted_model):
        # Brak L1/L2/L3 → powinien zwrócić globalną medianą
        pred = fitted_model.predict("00000", 10, "WORKING")
        assert pred.median_delay is not None
        assert pred.sample_count > 0

    def test_znana_stacja_moze_byc_bez_fallback(self):
        # Dużo próbek z jedną stacją/godzina/day_type → L1 powinien mieć dane
        df = pd.DataFrame({
            "station_id":          ["33506"] * 50,
            "hour_of_day":         [10] * 50,
            "day_type":            ["WORKING"] * 50,
            "delay_departure_min": np.random.exponential(5, 50),
        })
        m = BaselineModel()
        m.fit(df)
        pred = m.predict("33506", 10, "WORKING")
        # hour_bucket dla godziny 10 = 5, day_type=WORKING → powinno trafić L1
        assert pred.fallback is False

    def test_fallback_do_l2_gdy_brak_day_type_w_l1(self):
        # Trenuj bez danych dla day_type=HOLIDAY
        df = pd.DataFrame({
            "station_id":          ["33506"] * 50,
            "hour_of_day":         [10] * 50,
            "day_type":            ["WORKING"] * 50,  # tylko WORKING
            "delay_departure_min": np.ones(50) * 3.0,
        })
        m = BaselineModel()
        m.fit(df)
        pred = m.predict("33506", 10, "HOLIDAY")
        # Brak HOLIDAY w L1 → L2 (station+hour_bucket) = fallback=True
        assert pred.fallback is True
        assert pred.median_delay is not None

    def test_pusty_model_zwraca_none_delay(self):
        m = BaselineModel()
        # Nie wołamy fit
        pred = m.predict("33506", 10, "WORKING")
        assert pred.median_delay is None
        assert pred.sample_count == 0
        assert pred.fallback is True


# ---------------------------------------------------------------------------
# Save / Load (joblib)
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_load_roundtrip(self, fitted_model, tmp_path):
        path = tmp_path / "test_model.pkl"
        fitted_model.save(path)
        assert path.exists()
        loaded = BaselineModel.load(path)
        assert loaded.trained_date == fitted_model.trained_date
        pred = loaded.predict("33506", 10, "WORKING")
        assert isinstance(pred, BaselinePrediction)

    def test_predykcje_identyczne_po_zaladowaniu(self, fitted_model, tmp_path):
        path = tmp_path / "m.pkl"
        fitted_model.save(path)
        loaded = BaselineModel.load(path)
        orig  = fitted_model.predict("33506", 10, "WORKING")
        resto = loaded.predict("33506", 10, "WORKING")
        assert orig.median_delay == pytest.approx(resto.median_delay)
        assert orig.sample_count == resto.sample_count


# ---------------------------------------------------------------------------
# Endpoint /predict/baseline
# ---------------------------------------------------------------------------

from httpx import AsyncClient
from fastapi.testclient import TestClient


def _fake_db_url() -> str:
    return "postgresql://test:test@localhost/test"


@pytest.fixture
def client_with_model(fitted_model):
    """TestClient z załadowanym modelem i nadpisaną zależnością DB."""
    app.dependency_overrides[_db_url] = _fake_db_url
    with TestClient(app) as client:
        client.app.state.baseline_model = fitted_model
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_model():
    """TestClient bez załadowanego modelu."""
    app.dependency_overrides[_db_url] = _fake_db_url
    with TestClient(app) as client:
        client.app.state.baseline_model = None
        yield client
    app.dependency_overrides.clear()


class TestPredictBaselineEndpoint:
    def test_zwraca_200_gdy_model_zaladowany(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "2026-05-31T10:00:00",
                "day_type": "WORKING",
            },
        )
        assert resp.status_code == 200

    def test_struktura_odpowiedzi(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "2026-05-31T10:00:00",
                "day_type": "WORKING",
            },
        )
        body = resp.json()
        assert body["station_id"] == "33506"
        assert body["model"] == "baseline"
        assert "predicted_delay_min" in body
        assert "p75_delay_min" in body
        assert "p90_delay_min" in body
        assert "sample_count" in body
        assert "fallback" in body

    def test_503_gdy_model_niezaladowany(self, client_no_model):
        resp = client_no_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "2026-05-31T10:00:00",
            },
        )
        assert resp.status_code == 503

    def test_400_gdy_zla_data(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "NIE-DATA",
            },
        )
        assert resp.status_code == 400

    def test_day_type_opcjonalny(self, client_with_model):
        # Brak day_type → CalendarService auto-detect
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "2026-05-31T10:00:00",
            },
        )
        assert resp.status_code == 200

    def test_fallback_dla_nieznanej_stacji(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "99999",
                "planned_departure": "2026-05-31T10:00:00",
                "day_type": "WORKING",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["fallback"] is True

    def test_model_date_obecny_w_odpowiedzi(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={
                "station_id": "33506",
                "planned_departure": "2026-05-31T10:00:00",
                "day_type": "WORKING",
            },
        )
        assert resp.json()["model_date"] == "2026-05-31"

    def test_brak_station_id_zwraca_422(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={"planned_departure": "2026-05-31T10:00:00"},
        )
        assert resp.status_code == 422

    def test_brak_planned_departure_zwraca_422(self, client_with_model):
        resp = client_with_model.get(
            "/predict/baseline",
            params={"station_id": "33506"},
        )
        assert resp.status_code == 422
