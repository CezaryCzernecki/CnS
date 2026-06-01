"""
Testy feature store (mv_training_features).

Ponieważ widok zmaterializowany jest definicją SQL i nie da się go uruchomić bez bazy,
testy weryfikują trzy rzeczy:
  1. Logikę biznesową poprzez równoważne funkcje Pythona (LAG, LATERAL, flagi binarne)
  2. Metodę PostgresStorage.refresh_features() przez mock psycopg
  3. Asynchroniczny dispatch w DataCollector (wątek tła po save_snapshot)
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from cns.storage.postgres import PostgresStorage


# ---------------------------------------------------------------------------
# Helpers – Pythonowe odpowiedniki logiki SQL widoku
# ---------------------------------------------------------------------------

def _get_weather_for_stop(
    observations: list[dict],
    station_id: str,
    planned_departure: datetime,
) -> dict | None:
    """
    Emuluje LATERAL JOIN z weather_observations (po migracji 014):
      WHERE station_id = %s
        AND observed_at <= planned_departure
      ORDER BY is_forecast ASC, observed_at DESC
      LIMIT 1

    Preferuje obserwacje (is_forecast=False) nad prognozami (is_forecast=True).
    Jeśli są tylko prognozy – zwraca najnowszą prognozę.
    """
    matching = [
        obs for obs in observations
        if str(obs["station_id"]) == str(station_id)
        and obs["observed_at"] <= planned_departure
    ]
    if not matching:
        return None
    # is_forecast ASC (False=0 < True=1), observed_at DESC (nowszy = lepszy)
    return sorted(
        matching,
        key=lambda o: (int(o.get("is_forecast", True)), -o["observed_at"].timestamp()),
    )[0]


def _compute_lag(stops: list[dict]) -> list[dict]:
    """
    Emuluje LAG(delay_departure_min) OVER (PARTITION BY train_op_id ORDER BY planned_sequence).
    Zakłada że wszystkie stops należą do jednego pociągu (jedna partycja).
    """
    ordered = sorted(stops, key=lambda s: s["planned_sequence"])
    return [
        {
            **s,
            "prev_stop_delay_min": ordered[i - 1]["delay_departure_min"] if i > 0 else None,
        }
        for i, s in enumerate(ordered)
    ]


def _compute_flags(row: dict) -> dict:
    """
    Emuluje flagi binarne z SELECT:
      (snowfall_cm > 1)::BOOLEAN     AS is_snowing
      (precipitation_mm > 5)::BOOLEAN AS is_heavy_rain
      (wind_speed_kmh > 70)::BOOLEAN AS is_strong_wind
      (temperature_c < -10)::BOOLEAN AS is_frost
      (visibility_m < 200)::BOOLEAN  AS is_dense_fog
    W SQL NULL > 1 = NULL (nie TRUE), więc None → False.
    """
    def _gt(val, threshold):
        return val is not None and val > threshold

    def _lt(val, threshold):
        return val is not None and val < threshold

    return {
        "is_snowing":     _gt(row.get("snowfall_cm"), 1),
        "is_heavy_rain":  _gt(row.get("precipitation_mm"), 5),
        "is_strong_wind": _gt(row.get("wind_speed_kmh"), 70),
        "is_frost":       _lt(row.get("temperature_c"), -10),
        "is_dense_fog":   _lt(row.get("visibility_m"), 200),
    }


def _make_conn_mock():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_cursor)
    cm.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=cm)
    return mock_conn, mock_cursor


@pytest.fixture
def storage():
    with patch.object(PostgresStorage, "_verify_connection"):
        return PostgresStorage("postgresql://test/testdb")


# ---------------------------------------------------------------------------
# LATERAL JOIN – temporal constraint
# ---------------------------------------------------------------------------

class TestLateralWeatherJoin:
    _BASE = datetime(2026, 5, 31, 10, 0)

    def _obs(self, station, offset_h, forecast=False, temp=20.0):
        return {
            "station_id": station,
            "observed_at": self._BASE + timedelta(hours=offset_h),
            "is_forecast": forecast,
            "temperature_c": temp,
        }

    def test_zwraca_najnowsza_obserwacje_przed_odjazdem(self):
        observations = [
            self._obs("33506", -2, temp=14.0),
            self._obs("33506", -1, temp=16.0),
            self._obs("33506",  0, temp=18.0),   # dokładnie o czasie – jest <= więc pasuje
        ]
        result = _get_weather_for_stop(observations, "33506", self._BASE)
        assert result["temperature_c"] == 18.0

    def test_nie_zwraca_obserwacji_z_przyszlosci(self):
        observations = [
            self._obs("33506", -1, temp=14.0),
            self._obs("33506", +1, temp=99.0),   # przyszłość – nie może być wybrany
        ]
        result = _get_weather_for_stop(observations, "33506", self._BASE)
        assert result["temperature_c"] == 14.0

    def test_zwraca_none_gdy_brak_pasujacych(self):
        observations = [
            self._obs("33506", +1, temp=15.0),   # tylko przyszłość
        ]
        assert _get_weather_for_stop(observations, "33506", self._BASE) is None

    def test_preferuje_obserwacje_nad_prognoza_nawet_gdy_starsza(self):
        # Obserwacja sprzed 2h vs prognoza sprzed 1h – winna wygrać obserwacja
        observations = [
            self._obs("33506", -1, forecast=True,  temp=10.0),
            self._obs("33506", -2, forecast=False, temp=12.0),
        ]
        result = _get_weather_for_stop(observations, "33506", self._BASE)
        assert result["temperature_c"] == 12.0

    def test_fallback_na_prognize_gdy_brak_obserwacji(self):
        # Tylko prognozy dostępne – używamy najnowszej
        observations = [
            self._obs("33506", -2, forecast=True, temp=9.0),
            self._obs("33506", -1, forecast=True, temp=11.0),  # nowsza prognoza
        ]
        result = _get_weather_for_stop(observations, "33506", self._BASE)
        assert result["temperature_c"] == 11.0

    def test_filtruje_po_station_id(self):
        observations = [
            self._obs("33506", -1, temp=15.0),
            self._obs("99999", -1, temp=99.0),   # inna stacja
        ]
        result = _get_weather_for_stop(observations, "33506", self._BASE)
        assert result["temperature_c"] == 15.0

    def test_brak_obserwacji_dla_stacji_zwraca_none(self):
        observations = [self._obs("99999", -1, temp=15.0)]
        assert _get_weather_for_stop(observations, "33506", self._BASE) is None


# ---------------------------------------------------------------------------
# LAG – poprzednie opóźnienie
# ---------------------------------------------------------------------------

class TestLagPrevStopDelay:
    def test_pierwszy_przystanek_ma_null(self):
        stops = [
            {"planned_sequence": 1, "delay_departure_min": 5},
            {"planned_sequence": 2, "delay_departure_min": 3},
        ]
        result = _compute_lag(stops)
        assert result[0]["prev_stop_delay_min"] is None

    def test_drugi_przystanek_ma_opoznienie_pierwszego(self):
        stops = [
            {"planned_sequence": 1, "delay_departure_min": 5},
            {"planned_sequence": 2, "delay_departure_min": 3},
        ]
        result = _compute_lag(stops)
        assert result[1]["prev_stop_delay_min"] == 5

    def test_trzeci_przystanek_ma_opoznienie_drugiego(self):
        stops = [
            {"planned_sequence": 1, "delay_departure_min": 2},
            {"planned_sequence": 2, "delay_departure_min": 7},
            {"planned_sequence": 3, "delay_departure_min": 4},
        ]
        result = _compute_lag(stops)
        assert result[2]["prev_stop_delay_min"] == 7

    def test_lag_gdy_poprzedni_delay_jest_none(self):
        stops = [
            {"planned_sequence": 1, "delay_departure_min": None},
            {"planned_sequence": 2, "delay_departure_min": 5},
        ]
        result = _compute_lag(stops)
        assert result[1]["prev_stop_delay_min"] is None

    def test_jeden_przystanek_ma_null(self):
        stops = [{"planned_sequence": 1, "delay_departure_min": 10}]
        result = _compute_lag(stops)
        assert result[0]["prev_stop_delay_min"] is None

    def test_kolejnosc_wedlug_planned_sequence(self):
        # Nieposortowane wejście
        stops = [
            {"planned_sequence": 3, "delay_departure_min": 4},
            {"planned_sequence": 1, "delay_departure_min": 2},
            {"planned_sequence": 2, "delay_departure_min": 7},
        ]
        result = _compute_lag(stops)
        # Po posortowaniu: seq 1→2→3, lag: None, 2, 7
        by_seq = {r["planned_sequence"]: r for r in result}
        assert by_seq[1]["prev_stop_delay_min"] is None
        assert by_seq[2]["prev_stop_delay_min"] == 2
        assert by_seq[3]["prev_stop_delay_min"] == 7


# ---------------------------------------------------------------------------
# Flagi binarne
# ---------------------------------------------------------------------------

class TestBinaryFlags:
    def test_is_snowing_prog_powyzej_1_cm(self):
        assert not _compute_flags({"snowfall_cm": 0.0})["is_snowing"]
        assert not _compute_flags({"snowfall_cm": 1.0})["is_snowing"]   # dokładnie 1 – FALSE
        assert _compute_flags({"snowfall_cm": 1.001})["is_snowing"]
        assert _compute_flags({"snowfall_cm": 5.0})["is_snowing"]

    def test_is_heavy_rain_prog_powyzej_5_mm(self):
        assert not _compute_flags({"precipitation_mm": 0.0})["is_heavy_rain"]
        assert not _compute_flags({"precipitation_mm": 5.0})["is_heavy_rain"]
        assert _compute_flags({"precipitation_mm": 5.1})["is_heavy_rain"]

    def test_is_strong_wind_prog_powyzej_70_kmh(self):
        assert not _compute_flags({"wind_speed_kmh": 70.0})["is_strong_wind"]
        assert _compute_flags({"wind_speed_kmh": 70.1})["is_strong_wind"]

    def test_is_frost_prog_ponizej_minus_10(self):
        assert not _compute_flags({"temperature_c": -10.0})["is_frost"]   # dokładnie -10 – FALSE
        assert _compute_flags({"temperature_c": -10.1})["is_frost"]
        assert not _compute_flags({"temperature_c": 0.0})["is_frost"]

    def test_is_dense_fog_prog_ponizej_200_m(self):
        assert not _compute_flags({"visibility_m": 200})["is_dense_fog"]  # dokładnie 200 – FALSE
        assert _compute_flags({"visibility_m": 199})["is_dense_fog"]
        assert _compute_flags({"visibility_m": 0})["is_dense_fog"]

    def test_none_daje_false_dla_wszystkich_flag(self):
        flags = _compute_flags({})
        assert not any(flags.values())

    def test_wszystkie_flagi_moga_byc_aktywne(self):
        row = {
            "snowfall_cm": 10, "precipitation_mm": 20,
            "wind_speed_kmh": 100, "temperature_c": -20,
            "visibility_m": 10,
        }
        flags = _compute_flags(row)
        assert all(flags.values())


# ---------------------------------------------------------------------------
# PostgresStorage.refresh_features – mock psycopg
# ---------------------------------------------------------------------------

class TestRefreshFeatures:
    def _mock_autoconn(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        return mock_conn

    def test_wywoluje_refresh_concurrently(self, storage):
        mock_conn = self._mock_autoconn()
        with patch("cns.storage.postgres._conn_autocommit", return_value=mock_conn):
            storage.refresh_features()
        sql = mock_conn.execute.call_args[0][0]
        assert "REFRESH MATERIALIZED VIEW CONCURRENTLY" in sql
        assert "mv_training_features" in sql

    def test_przekazuje_poprawny_url(self, storage):
        mock_conn = self._mock_autoconn()
        with patch("cns.storage.postgres._conn_autocommit", return_value=mock_conn) as mock_fn:
            storage.refresh_features()
        assert mock_fn.call_args[0][0] == "postgresql://test/testdb"

    def test_blad_polaczenia_nie_rzuca_wyjatku(self, storage):
        with patch("cns.storage.postgres._conn_autocommit",
                   side_effect=Exception("conn refused")):
            storage.refresh_features()  # powinno zakończyć się bez wyjątku

    def test_blad_sql_nie_rzuca_wyjatku(self, storage):
        mock_conn = self._mock_autoconn()
        mock_conn.execute.side_effect = Exception("relation does not exist")
        with patch("cns.storage.postgres._conn_autocommit", return_value=mock_conn):
            storage.refresh_features()  # logger.warning, nie raise


# ---------------------------------------------------------------------------
# DataCollector – asynchroniczny dispatch po save_snapshot
# ---------------------------------------------------------------------------

class TestRefreshAsync:
    def _make_storage_mock(self):
        mock = MagicMock()
        mock.refresh_features = MagicMock()
        return mock

    def test_refresh_uruchamia_nowy_watek(self):
        from cns.collector.collector import DataCollector

        storage = self._make_storage_mock()
        with patch.object(DataCollector, "_verify_connection", create=True):
            dc = DataCollector.__new__(DataCollector)
            dc.storage = storage
            dc.dry_run = False

        launched = []

        def fake_start(self_thread):
            launched.append(self_thread.name)

        with patch.object(threading.Thread, "start", fake_start):
            dc._refresh_features_async()

        assert "feature-refresh" in launched

    def test_refresh_watek_jest_demonem(self):
        from cns.collector.collector import DataCollector

        storage = self._make_storage_mock()
        dc = DataCollector.__new__(DataCollector)
        dc.storage = storage
        dc.dry_run = False

        created_threads = []

        real_init = threading.Thread.__init__

        def capture_init(self_thread, *a, **kw):
            real_init(self_thread, *a, **kw)
            created_threads.append(self_thread)

        with patch.object(threading.Thread, "__init__", capture_init), \
             patch.object(threading.Thread, "start"):
            dc._refresh_features_async()

        assert created_threads[0].daemon is True

    def test_refresh_pomijany_gdy_brak_metody_w_storage(self):
        from cns.collector.collector import DataCollector

        storage = MagicMock(spec=[])  # brak atrybutu refresh_features
        dc = DataCollector.__new__(DataCollector)
        dc.storage = storage
        dc.dry_run = False

        with patch("threading.Thread") as mock_thread:
            dc._refresh_features_async()
            mock_thread.assert_not_called()

    def test_do_refresh_features_wywoluje_metode_storage(self):
        from cns.collector.collector import DataCollector

        storage = self._make_storage_mock()
        dc = DataCollector.__new__(DataCollector)
        dc.storage = storage
        dc.dry_run = False

        dc._do_refresh_features()
        storage.refresh_features.assert_called_once()

    def test_do_refresh_features_pochyla_wyjatki(self):
        from cns.collector.collector import DataCollector

        storage = self._make_storage_mock()
        storage.refresh_features.side_effect = Exception("crash")
        dc = DataCollector.__new__(DataCollector)
        dc.storage = storage
        dc.dry_run = False

        dc._do_refresh_features()  # nie może rzucić
