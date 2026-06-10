"""
Testy endpointów rankingowych FastAPI.

Używają TestClient z httpx — nie wymagają działającej bazy.
psycopg.connect jest mockowany, DATABASE_URL ustawiony przez env patch.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cns.api.app import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DB_URL = "postgresql://test/testdb"


def _make_psycopg_mock(rows: list) -> tuple:
    """Zwraca (mock_connect_ctx, mock_cursor) — cursor.fetchall() → rows."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows

    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor_cm.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_cm)

    mock_connect = MagicMock(return_value=mock_conn)
    return mock_connect, mock_cursor


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /rankings/all-time
# ---------------------------------------------------------------------------

class TestAllTimeRanking:
    def test_returns_list(self, client):
        rows = [("12345", "Ekspres Regionalny", "PKP Intercity S.A.", date(2026, 6, 1), 120, "Warszawa Centralna", "Kraków Główny", None, None, None, None)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["train_number"] == "12345"
        assert data[0]["max_delay_min"] == 120
        assert data[0]["first_station"] == "Warszawa Centralna"
        assert data[0]["last_station"] == "Kraków Główny"
        assert data[0]["has_bus_replacement"] is False
        assert data[0]["bus_segment"] is None

    def test_returns_list_z_kz_z_disruption(self, client):
        rows = [("99999", "TLK", "PKP Intercity S.A.", date(2026, 6, 1), 200, "Gdańsk Główny", "Gdynia Główna", True, "Gdańsk Główny", "Gdynia Główna", None)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["has_bus_replacement"] is True
        assert data[0]["bus_segment"] == "Gdańsk Główny → Gdynia Główna"

    def test_returns_list_z_kz_z_is_cancelled(self, client):
        # KZ wykryte z all_cancelled=True, brak disruption
        rows = [("87104", None, "IC", date(2026, 6, 3), 413, "Gorzów Wielkopolski", "Jarocin", None, None, None, True)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["has_bus_replacement"] is True
        assert data[0]["bus_segment"] == "Gorzów Wielkopolski → Jarocin"

    def test_limit_param_forwarded(self, client):
        mock_connect, mock_cursor = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time?limit=25")
        assert resp.status_code == 200
        call_args = mock_cursor.execute.call_args
        assert 25 in call_args[0][1]

    def test_limit_max_100(self, client):
        resp = client.get("/rankings/all-time?limit=101")
        assert resp.status_code == 422

    def test_limit_min_1(self, client):
        resp = client.get("/rankings/all-time?limit=0")
        assert resp.status_code == 422

    def test_null_train_number_allowed(self, client):
        rows = [(None, None, "PKP Intercity S.A.", date(2026, 6, 1), 80, None, None, None, None, None, None)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time")
        assert resp.status_code == 200
        assert resp.json()[0]["train_number"] is None

    def test_empty_result(self, client):
        mock_connect, _ = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/all-time")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# /rankings/daily
# ---------------------------------------------------------------------------

class TestDailyRanking:
    def test_returns_list(self, client):
        rows = [("54321", "Bielik", "PKP Intercity S.A.", 75, "Gdańsk Główny", "Warszawa Centralna")]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/daily?date=2026-06-04")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["max_delay_min"] == 75
        assert data[0]["first_station"] == "Gdańsk Główny"
        assert data[0]["last_station"] == "Warszawa Centralna"

    def test_date_required(self, client):
        resp = client.get("/rankings/daily")
        assert resp.status_code == 422

    def test_invalid_date_format(self, client):
        resp = client.get("/rankings/daily?date=04-06-2026")
        assert resp.status_code == 400

    def test_limit_forwarded(self, client):
        mock_connect, mock_cursor = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/daily?date=2026-06-04&limit=50")
        assert resp.status_code == 200
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert 50 in params

    def test_date_sent_to_db(self, client):
        mock_connect, mock_cursor = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/daily?date=2026-05-15")
        assert resp.status_code == 200
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert date(2026, 5, 15) in params


# ---------------------------------------------------------------------------
# /rankings/monthly/trains
# ---------------------------------------------------------------------------

class TestMonthlyTrainsRanking:
    def test_returns_list(self, client):
        rows = [("99999", "Expres", "Koleje Śląskie", "Kraków Główny", "Zakopane", 30, 450, 15.0)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/trains?year=2026&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_delay_min"] == 450
        assert data[0]["trip_count"] == 30
        assert data[0]["first_station"] == "Kraków Główny"
        assert data[0]["last_station"] == "Zakopane"

    def test_year_and_month_required(self, client):
        resp = client.get("/rankings/monthly/trains?year=2026")
        assert resp.status_code == 422

    def test_month_out_of_range(self, client):
        resp = client.get("/rankings/monthly/trains?year=2026&month=13")
        assert resp.status_code == 422

    def test_limit_forwarded(self, client):
        mock_connect, mock_cursor = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/trains?year=2026&month=6&limit=25")
        assert resp.status_code == 200
        call_args = mock_cursor.execute.call_args
        assert 25 in call_args[0][1]

    def test_year_month_forwarded_to_db(self, client):
        mock_connect, mock_cursor = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/trains?year=2026&month=3&limit=10")
        assert resp.status_code == 200
        params = mock_cursor.execute.call_args[0][1]
        assert 2026 in params
        assert 3 in params


# ---------------------------------------------------------------------------
# /rankings/monthly/carriers
# ---------------------------------------------------------------------------

class TestMonthlyCarriersRanking:
    def test_returns_all_carriers(self, client):
        rows = [
            ("PKP Intercity S.A.", 500, 8200, 16.4, 12),
            ("Koleje Mazowieckie", 300, 4500, 15.0, 3),
        ]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/carriers?year=2026&month=6")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["carrier_name"] == "PKP Intercity S.A."
        assert data[1]["total_delay_min"] == 4500
        assert data[0]["cancelled_count"] == 12
        assert data[1]["cancelled_count"] == 3

    def test_year_and_month_required(self, client):
        resp = client.get("/rankings/monthly/carriers?year=2026")
        assert resp.status_code == 422

    def test_no_limit_param(self, client):
        mock_connect, _ = _make_psycopg_mock([])
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/carriers?year=2026&month=6")
        assert resp.status_code == 200

    def test_null_carrier_name_allowed(self, client):
        rows = [(None, 10, 150, 15.0, 0)]
        mock_connect, _ = _make_psycopg_mock(rows)
        with patch("psycopg.connect", mock_connect):
            resp = client.get("/rankings/monthly/carriers?year=2026&month=6")
        assert resp.status_code == 200
        assert resp.json()[0]["carrier_name"] is None
