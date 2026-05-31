"""
Testy jednostkowe WeatherClient i PostgresStorage.save_weather_observations.
Nie wymagają połączenia z siecią ani bazą danych.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from cns.collector.weather_client import WeatherClient
from cns.storage.postgres import PostgresStorage

# ---------------------------------------------------------------------------
# Fixtures – przykładowe odpowiedzi Open-Meteo
# ---------------------------------------------------------------------------

_START = datetime(2026, 5, 31, 0, 0)
_TIMES = [(_START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(50)]

CURRENT_FIXTURE = {
    "latitude": 52.22,
    "longitude": 21.0,
    "timezone": "GMT",
    "current_units": {
        "temperature_2m": "°C",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
        "snowfall": "cm",
        "visibility": "m",
        "cloud_cover": "%",
        "weather_code": "wmo code",
    },
    "current": {
        "time": "2026-05-31T12:00",
        "interval": 900,
        "temperature_2m": 18.5,
        "precipitation": 0.2,
        "wind_speed_10m": 12.3,
        "snowfall": 0.0,
        "visibility": 24000,
        "cloud_cover": 20,
        "weather_code": 2,
    },
}

FORECAST_FIXTURE = {
    "latitude": 52.22,
    "longitude": 21.0,
    "timezone": "GMT",
    "hourly_units": {
        "temperature_2m": "°C",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
        "snowfall": "cm",
        "visibility": "m",
        "cloud_cover": "%",
        "weather_code": "wmo code",
    },
    "hourly": {
        "time": _TIMES,
        "temperature_2m": [18.5 + i * 0.1 for i in range(50)],
        "precipitation": [0.0] * 50,
        "wind_speed_10m": [12.3] * 50,
        "snowfall": [0.0] * 50,
        "visibility": [24000] * 50,
        "cloud_cover": [20] * 50,
        "weather_code": [2] * 50,
    },
}

FORECAST_WITH_NULLS_FIXTURE = {
    "latitude": 52.22,
    "longitude": 21.0,
    "timezone": "GMT",
    "hourly": {
        "time": _TIMES[:3],
        "temperature_2m": [None, 10.0, 11.0],
        "precipitation": [None, 0.0, 0.0],
        "wind_speed_10m": [None, 5.0, 6.0],
        "snowfall": [None, 0.0, 0.0],
        "visibility": [None, 20000, 21000],
        "cloud_cover": [None, 50, 60],
        "weather_code": [None, 1, 2],
    },
}


def _mock_response(fixture: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = fixture
    resp.raise_for_status = MagicMock()
    return resp


def _make_conn_mock():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor_cm.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_cm)
    return mock_conn, mock_cursor


@pytest.fixture
def client():
    return WeatherClient()


@pytest.fixture
def storage():
    with patch.object(PostgresStorage, "_verify_connection"):
        return PostgresStorage("postgresql://test/testdb")


# ---------------------------------------------------------------------------
# WeatherClient.get_current
# ---------------------------------------------------------------------------

class TestGetCurrent:
    def test_parsuje_poprawna_odpowiedz(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(CURRENT_FIXTURE)):
            result = client.get_current("33506", 52.22, 21.0)

        assert result["station_id"] == "33506"
        assert result["is_forecast"] is False
        assert result["temperature_c"] == 18.5
        assert result["precipitation_mm"] == 0.2
        assert result["wind_speed_kmh"] == 12.3
        assert result["snowfall_cm"] == 0.0
        assert result["visibility_m"] == 24000
        assert result["cloud_cover_pct"] == 20
        assert result["weather_code"] == 2
        assert result["observed_at"] == "2026-05-31T12:00"

    def test_visibility_jest_int(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(CURRENT_FIXTURE)):
            result = client.get_current("1", 52.0, 21.0)
        assert isinstance(result["visibility_m"], int)
        assert isinstance(result["cloud_cover_pct"], int)
        assert isinstance(result["weather_code"], int)

    def test_brak_current_w_odpowiedzi_zwraca_none_pola(self, client):
        empty = {"latitude": 52.0, "longitude": 21.0, "current": {}}
        with patch.object(client.session, "get", return_value=_mock_response(empty)):
            result = client.get_current("1", 52.0, 21.0)
        assert result["temperature_c"] is None
        assert result["visibility_m"] is None

    def test_wyrzuca_connection_error(self, client):
        with patch.object(client.session, "get", side_effect=requests.ConnectionError("timeout")):
            with pytest.raises(requests.ConnectionError):
                client.get_current("1", 52.0, 21.0)

    def test_wyrzuca_http_error_po_raise_for_status(self, client):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("503 Server Error")
        with patch.object(client.session, "get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                client.get_current("1", 52.0, 21.0)


# ---------------------------------------------------------------------------
# WeatherClient.get_forecast_48h
# ---------------------------------------------------------------------------

class TestGetForecast48h:
    def test_zwraca_dokladnie_48_rekordow(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(FORECAST_FIXTURE)):
            result = client.get_forecast_48h("33506", 52.22, 21.0)
        assert len(result) == 48

    def test_kazdy_rekord_ma_wymagane_pola(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(FORECAST_FIXTURE)):
            result = client.get_forecast_48h("33506", 52.22, 21.0)
        required = {
            "station_id", "observed_at", "is_forecast",
            "temperature_c", "precipitation_mm", "wind_speed_kmh",
            "snowfall_cm", "visibility_m", "cloud_cover_pct", "weather_code",
        }
        for row in result:
            assert required == set(row.keys())

    def test_wszystkie_rekordy_sa_prognoza(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(FORECAST_FIXTURE)):
            result = client.get_forecast_48h("33506", 52.22, 21.0)
        assert all(r["is_forecast"] is True for r in result)

    def test_station_id_poprawnie_przypisany(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(FORECAST_FIXTURE)):
            result = client.get_forecast_48h("99999", 52.22, 21.0)
        assert all(r["station_id"] == "99999" for r in result)

    def test_null_w_tablicach_pozostaje_none(self, client):
        with patch.object(client.session, "get",
                          return_value=_mock_response(FORECAST_WITH_NULLS_FIXTURE)):
            result = client.get_forecast_48h("1", 52.0, 21.0)
        assert result[0]["temperature_c"] is None
        assert result[0]["visibility_m"] is None

    def test_kolumny_int_sa_int(self, client):
        with patch.object(client.session, "get", return_value=_mock_response(FORECAST_FIXTURE)):
            result = client.get_forecast_48h("1", 52.0, 21.0)
        row = result[0]
        assert isinstance(row["visibility_m"], int)
        assert isinstance(row["cloud_cover_pct"], int)
        assert isinstance(row["weather_code"], int)

    def test_wyrzuca_connection_error(self, client):
        with patch.object(client.session, "get", side_effect=requests.ConnectionError("fail")):
            with pytest.raises(requests.ConnectionError):
                client.get_forecast_48h("1", 52.0, 21.0)

    def test_zwraca_mniej_niz_48_gdy_api_daje_malo_godzin(self, client):
        short = {
            "latitude": 52.0, "longitude": 21.0,
            "hourly": {
                "time": _TIMES[:3],
                "temperature_2m": [10.0, 11.0, 12.0],
                "precipitation": [0.0, 0.0, 0.0],
                "wind_speed_10m": [5.0, 5.0, 5.0],
                "snowfall": [0.0, 0.0, 0.0],
                "visibility": [20000, 20000, 20000],
                "cloud_cover": [50, 50, 50],
                "weather_code": [1, 1, 1],
            },
        }
        with patch.object(client.session, "get", return_value=_mock_response(short)):
            result = client.get_forecast_48h("1", 52.0, 21.0)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# PostgresStorage.save_weather_observations
# ---------------------------------------------------------------------------

class TestSaveWeatherObservations:
    def _make_obs(self, n: int = 3) -> list[dict]:
        return [
            {
                "station_id": f"station_{i}",
                "observed_at": f"2026-05-31T{i:02d}:00",
                "is_forecast": True,
                "temperature_c": 18.5,
                "precipitation_mm": 0.0,
                "wind_speed_kmh": 12.0,
                "snowfall_cm": 0.0,
                "visibility_m": 24000,
                "cloud_cover_pct": 20,
                "weather_code": 2,
            }
            for i in range(n)
        ]

    def test_pusta_lista_nie_otwiera_polaczenia(self, storage):
        with patch("cns.storage.postgres._conn") as mock_conn_fn:
            storage.save_weather_observations([])
            mock_conn_fn.assert_not_called()

    def test_wywoluje_executemany_z_poprawnymi_danymi(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        obs = self._make_obs(3)
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_weather_observations(obs)
        mock_cursor.executemany.assert_called_once()
        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 3

    def test_wiersz_zawiera_poprawne_pola_w_kolejnosci(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        obs = [self._make_obs(1)[0]]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_weather_observations(obs)
        row = mock_cursor.executemany.call_args[0][1][0]
        # (station_id, observed_at, is_forecast, temperature_c, precipitation_mm,
        #  wind_speed_kmh, snowfall_cm, visibility_m, cloud_cover_pct, weather_code)
        assert row[0] == "station_0"
        assert row[2] is True   # is_forecast
        assert row[3] == 18.5   # temperature_c
        assert row[7] == 24000  # visibility_m
        assert row[9] == 2      # weather_code

    def test_pomija_rekordy_bez_station_id(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        obs = [
            {"station_id": None, "observed_at": "2026-05-31T00:00", "is_forecast": False},
            {"station_id": "", "observed_at": "2026-05-31T01:00", "is_forecast": False},
            {"station_id": "ok", "observed_at": "2026-05-31T02:00", "is_forecast": False,
             "temperature_c": 10.0},
        ]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_weather_observations(obs)
        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 1
        assert rows[0][0] == "ok"

    def test_pomija_rekordy_bez_observed_at(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        obs = [
            {"station_id": "1", "observed_at": None, "is_forecast": False},
            {"station_id": "2", "observed_at": "2026-05-31T00:00", "is_forecast": False},
        ]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_weather_observations(obs)
        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# PostgresStorage.get_weather_stations
# ---------------------------------------------------------------------------

class TestGetWeatherStations:
    def test_zwraca_wynik_z_bazy(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchall.return_value = [
            ("33506", 52.22, 21.00),
            ("30700", 50.07, 19.94),
        ]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            result = storage.get_weather_stations(limit=30)
        assert len(result) == 2
        assert result[0] == ("33506", 52.22, 21.00)

    def test_przekazuje_limit_do_sql(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchall.return_value = []
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.get_weather_stations(limit=5)
        sql_params = mock_cursor.execute.call_args[0][1]
        assert sql_params == (5,)
