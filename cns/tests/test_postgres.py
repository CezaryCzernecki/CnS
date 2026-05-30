"""
Testy jednostkowe PostgresStorage.
Używają mocków psycopg – nie wymagają działającej bazy danych.
"""

from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from cns.models.records import (
    Carrier,
    OperationsSnapshot,
    Station,
    StationStop,
    TrainOperation,
)
from cns.storage.postgres import PostgresStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn_mock():
    """Zwraca (mock_conn, mock_cursor) – gotowy do użycia w @patch('...._conn')."""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor_cm.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_cm)
    return mock_conn, mock_cursor


def _make_stop(station_id: str = "1", delay_dep: int = 5) -> StationStop:
    base = datetime(2026, 5, 27, 10, 0)
    from datetime import timedelta
    return StationStop(
        station_id=station_id,
        station_name="Test",
        planned_sequence=1,
        actual_sequence=1,
        planned_arrival=base,
        actual_arrival=base,
        planned_departure=base,
        actual_departure=base + timedelta(minutes=delay_dep),
    )


def _make_train(order_id: str = "100", status: str = "P", n_stops: int = 2) -> TrainOperation:
    return TrainOperation(
        collected_at=datetime(2026, 5, 27, 12, 0),
        schedule_id="2026",
        order_id=order_id,
        operating_date="2026-05-27",
        train_status=status,
        train_number=None,
        carrier_code=None,
        stops=[_make_stop(str(i)) for i in range(n_stops)],
    )


def _make_snapshot(n_trains: int = 2) -> OperationsSnapshot:
    trains = [_make_train(str(100 + i)) for i in range(n_trains)]
    total_stops = sum(len(t.stops) for t in trains)
    return OperationsSnapshot(
        fetched_at=datetime(2026, 5, 27, 12, 0),
        data_version_guid="test-guid",
        total_trains=n_trains,
        total_stops=total_stops,
        station_names={},
        trains=trains,
    )


@pytest.fixture
def storage():
    with patch.object(PostgresStorage, "_verify_connection"):
        return PostgresStorage("postgresql://test/testdb")


# ---------------------------------------------------------------------------
# upsert_stations
# ---------------------------------------------------------------------------

class TestUpsertStations:
    def test_pusta_lista_nie_otwiera_polaczenia(self, storage):
        with patch("cns.storage.postgres._conn") as mock_conn_fn:
            storage.upsert_stations([])
            mock_conn_fn.assert_not_called()

    def test_wywoluje_executemany_z_poprawnymi_danymi(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            stations = [
                Station(station_id="33506", name="Warszawa Centralna",
                        latitude=52.22, longitude=21.00),
                Station(station_id="33512", name="Kraków Główny"),
            ]
            storage.upsert_stations(stations)

        mock_cursor.executemany.assert_called_once()
        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 2
        assert rows[0] == (33506, "Warszawa Centralna", None, 52.22, 21.00)
        assert rows[1][0] == 33512

    def test_pomija_rekord_z_niepoprawnym_station_id(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            stations = [
                Station(station_id="nie_liczba", name="Błędna"),
                Station(station_id="1", name="OK"),
            ]
            storage.upsert_stations(stations)

        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 1
        assert rows[0][0] == 1


# ---------------------------------------------------------------------------
# upsert_carriers
# ---------------------------------------------------------------------------

class TestUpsertCarriers:
    def test_pusta_lista_nie_otwiera_polaczenia(self, storage):
        with patch("cns.storage.postgres._conn") as mock_conn_fn:
            storage.upsert_carriers([])
            mock_conn_fn.assert_not_called()

    def test_wywoluje_executemany_z_parami_code_name(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            carriers = [
                Carrier(code="IC", name="PKP Intercity S.A."),
                Carrier(code="KM", name="Koleje Mazowieckie"),
            ]
            storage.upsert_carriers(carriers)

        mock_cursor.executemany.assert_called_once()
        rows = mock_cursor.executemany.call_args[0][1]
        assert ("IC", "PKP Intercity S.A.") in rows
        assert ("KM", "Koleje Mazowieckie") in rows

    def test_pomija_przewoznika_bez_kodu(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            carriers = [
                Carrier(code="", name="Bez kodu"),
                Carrier(code="IC", name="OK"),
            ]
            storage.upsert_carriers(carriers)

        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# save_snapshot – optymalizacja batch insert
# ---------------------------------------------------------------------------

class TestSaveSnapshot:
    def test_wstawia_snapshot_i_zwraca_id(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (42,)
        mock_cursor.fetchall.return_value = [(100,), (101,)]

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(_make_snapshot(n_trains=2))

        # Pierwsze fetchone → id snapshotu
        mock_cursor.fetchone.assert_called_once()

    def test_batch_insert_trains_jedno_execute(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [(10,), (11,)]

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(_make_snapshot(n_trains=2))

        # execute wywołane 2 razy: INSERT snapshot + INSERT trains (unnest)
        assert mock_cursor.execute.call_count == 2

    def test_jeden_executemany_dla_wszystkich_przystankow(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (1,)
        # 2 pociągi × 2 przystanki = 4 rekordy
        mock_cursor.fetchall.return_value = [(10,), (11,)]

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(_make_snapshot(n_trains=2))

        mock_cursor.executemany.assert_called_once()
        stop_rows = mock_cursor.executemany.call_args[0][1]
        assert len(stop_rows) == 4  # 2 trains × 2 stops each

    def test_brak_pociagow_nie_wywoluje_executemany(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (1,)

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(_make_snapshot(n_trains=0))

        mock_cursor.executemany.assert_not_called()

    def test_pociag_z_niepoprawnym_id_jest_pomijany(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [(10,)]

        bad_train = TrainOperation(
            collected_at=datetime(2026, 5, 27, 12, 0),
            schedule_id="nie_liczba",
            order_id="tez_nie_liczba",
            operating_date="2026-05-27",
            train_status="P",
            train_number=None,
            carrier_code=None,
        )
        good_train = _make_train("999", n_stops=1)
        snapshot = OperationsSnapshot(
            fetched_at=datetime(2026, 5, 27, 12, 0),
            data_version_guid="guid",
            total_trains=2,
            total_stops=1,
            station_names={},
            trains=[bad_train, good_train],
        )

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(snapshot)

        # Tylko 1 poprawny pociąg przekazany do batch insert
        execute_calls = mock_cursor.execute.call_args_list
        train_insert_call = execute_calls[1]
        schedule_ids = train_insert_call[0][1][1]
        assert len(schedule_ids) == 1

    def test_stop_rows_zawieraja_delay_minutes(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [(10,)]

        train = _make_train("100", n_stops=0)
        stop = _make_stop("33506", delay_dep=7)
        train.stops = [stop]

        snapshot = OperationsSnapshot(
            fetched_at=datetime(2026, 5, 27, 12, 0),
            data_version_guid="guid",
            total_trains=1,
            total_stops=1,
            station_names={},
            trains=[train],
        )

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_snapshot(snapshot)

        stop_rows = mock_cursor.executemany.call_args[0][1]
        assert len(stop_rows) == 1
        # delay_departure_min (indeks 9) = 7
        assert stop_rows[0][9] == 7


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_zwraca_slownik_z_wymaganymi_kluczami(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (100, 5, 10, 5000, 80000, 30, None)

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            result = storage.get_stats()

        expected_keys = {"stations", "carriers", "snapshots", "train_ops", "stops",
                         "disruptions", "last_snapshot"}
        assert expected_keys == set(result.keys())
        assert result["stations"] == 100
        assert result["stops"] == 80000

    def test_wywoluje_pojedyncze_zapytanie(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (0, 0, 0, 0, 0, 0, None)

        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.get_stats()

        mock_cursor.execute.assert_called_once()
