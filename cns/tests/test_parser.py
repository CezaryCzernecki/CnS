"""
Testy jednostkowe parsera.
Mock dane odzwierciedlają rzeczywistą strukturę API (zweryfikowaną 2026-05-27).
"""

import pytest
from datetime import datetime

from cns.collector.parser import (
    parse_carriers, parse_disruptions, parse_operations, parse_stations,
)
from cns.models.records import StationStop, TrainOperation

# ---------------------------------------------------------------------------
# Mock danych – rzeczywista struktura API
# ---------------------------------------------------------------------------

MOCK_OPERATIONS = {
    "generatedAt": "2026-05-27T19:45:00",
    "pagination": {"page": 1, "pageSize": 10000, "total": 10000},
    "trains": [
        {
            "scheduleId": 2026,           # int, nie string
            "orderId": 513569932,          # int, nie string
            "trainOrderId": 513569932,
            "operatingDate": "2026-05-27",
            "trainStatus": "P",            # in progress
            "stations": [
                {
                    "stationId": 33506,
                    "plannedSequenceNumber": 1,
                    "actualSequenceNumber": 1,
                    "plannedArrival": "2026-05-27T10:00:00",
                    "actualArrival": "2026-05-27T10:05:00",    # +5 min
                    "plannedDeparture": "2026-05-27T10:02:00",
                    "actualDeparture": "2026-05-27T10:08:00",  # +6 min
                    "arrivalDelayMinutes": 5,
                    "departureDelayMinutes": 6,
                    "isConfirmed": True,
                    "isCancelled": False,
                },
                {
                    "stationId": 33512,
                    "plannedSequenceNumber": 2,
                    "actualSequenceNumber": 2,
                    "plannedArrival": "2026-05-27T12:00:00",
                    "actualArrival": "2026-05-27T12:03:00",    # +3 min
                    "plannedDeparture": "2026-05-27T12:05:00",
                    "actualDeparture": "2026-05-27T12:05:00",  # punktualnie
                    "arrivalDelayMinutes": 3,
                    "departureDelayMinutes": 0,
                    "isConfirmed": True,
                    "isCancelled": False,
                },
            ],
        },
        {
            "scheduleId": 2026,
            "orderId": 999999999,
            "trainOrderId": 999999999,
            "operatingDate": "2026-05-27",
            "trainStatus": "X",            # odwołany
            "stations": [],
        },
        {
            "scheduleId": 2026,
            "orderId": 111111111,
            "trainOrderId": 111111111,
            "operatingDate": "2026-05-28",
            "trainStatus": "S",            # zaplanowany
            "stations": [
                {
                    "stationId": 35428,
                    "plannedSequenceNumber": 1,
                    "actualSequenceNumber": 1,
                    # Anomalia: przesunięcie o dobę (1440 min) – ma być odfiltrowana
                    "plannedDeparture": "2026-05-28T00:01:00",
                    "actualDeparture": "2026-05-29T00:01:00",
                    "plannedArrival": "2026-05-28T00:01:00",
                    "actualArrival": "2026-05-29T00:01:00",
                    "isConfirmed": False,
                    "isCancelled": False,
                },
            ],
        },
    ],
    # Słownik nazw stacji dołączony do odpowiedzi
    "stations": {
        "33506": "Warszawa Centralna",
        "33512": "Kraków Główny",
        "35428": "Grodzisk Mazowiecki Radońska",
    },
}

MOCK_STATIONS_RESPONSE = {
    "stations": [
        {"id": "33506", "name": "Warszawa Centralna", "latitude": 52.2288, "longitude": 21.0031},
        {"id": "33512", "name": "Kraków Główny", "latitude": 50.0667, "longitude": 19.945},
    ]
}

MOCK_CARRIERS_RESPONSE = {
    "carriers": [
        {"code": "IC", "name": "PKP Intercity S.A."},
        {"code": "KM", "name": "Koleje Mazowieckie"},
    ]
}

MOCK_DISRUPTIONS_RESPONSE = {
    "disruptions": [
        {
            "disruptionId": 1001,
            "disruptionTypeCode": "utr_32",
            "message": "Roboty torowe Warszawa–Łódź. Ograniczenie prędkości.",
            "startStationId": 33506,
            "endStationId": 33590,
            "affectedRoutes": [
                {
                    "scheduleId": 2026, "orderId": 513569932,
                    "operatingDate": "2026-05-27",
                    "stationId": 33506, "sequenceNumber": 1,
                },
                {
                    "scheduleId": 2026, "orderId": 513569932,
                    "operatingDate": "2026-05-27",
                    "stationId": 33590, "sequenceNumber": 5,
                },
            ],
        }
    ]
}


# ---------------------------------------------------------------------------
# Testy struktury odpowiedzi
# ---------------------------------------------------------------------------

class TestParseOperationsStruktura:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)
        self.snapshot = parse_operations(MOCK_OPERATIONS, self.now, "test-guid")

    def test_liczba_pociagow(self):
        assert self.snapshot.total_trains == 3

    def test_liczba_przystankow(self):
        # P: 2 stacje, X: 0 stacji, S: 1 stacja = 3
        assert self.snapshot.total_stops == 3

    def test_wersja_danych(self):
        assert self.snapshot.data_version_guid == "test-guid"

    def test_fetched_at(self):
        assert self.snapshot.fetched_at == self.now

    def test_slownik_nazw_stacji_wczytany(self):
        assert len(self.snapshot.station_names) == 3
        assert self.snapshot.station_names["33506"] == "Warszawa Centralna"
        assert self.snapshot.station_names["33512"] == "Kraków Główny"

    def test_active_trains_tylko_status_P(self):
        active = self.snapshot.active_trains
        assert len(active) == 1
        assert active[0].train_status == "P"

    def test_cancelled_trains_tylko_status_X(self):
        cancelled = self.snapshot.cancelled_trains
        assert len(cancelled) == 1
        assert cancelled[0].train_status == "X"

    def test_status_counts(self):
        counts = self.snapshot.status_counts()
        assert counts.get("P") == 1
        assert counts.get("X") == 1
        assert counts.get("S") == 1


# ---------------------------------------------------------------------------
# Testy parsowania typów danych
# ---------------------------------------------------------------------------

class TestTypyDanych:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)
        self.snapshot = parse_operations(MOCK_OPERATIONS, self.now)

    def test_schedule_id_to_string(self):
        # scheduleId w JSON to int – musi być castowany na str
        assert isinstance(self.snapshot.trains[0].schedule_id, str)
        assert self.snapshot.trains[0].schedule_id == "2026"

    def test_order_id_to_string(self):
        assert isinstance(self.snapshot.trains[0].order_id, str)
        assert self.snapshot.trains[0].order_id == "513569932"

    def test_station_id_to_string(self):
        stop = self.snapshot.trains[0].stops[0]
        assert isinstance(stop.station_id, str)
        assert stop.station_id == "33506"

    def test_train_number_zawsze_none(self):
        # trainNumber niedostępny w /operations
        for train in self.snapshot.trains:
            assert train.train_number is None

    def test_carrier_code_zawsze_none(self):
        # carrierCode niedostępny w /operations
        for train in self.snapshot.trains:
            assert train.carrier_code is None


# ---------------------------------------------------------------------------
# Testy nazw stacji
# ---------------------------------------------------------------------------

class TestNazwyStacji:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)
        self.snapshot = parse_operations(MOCK_OPERATIONS, self.now)

    def test_nazwa_stacji_wypelniona_ze_slownika(self):
        stop = self.snapshot.trains[0].stops[0]
        assert stop.station_name == "Warszawa Centralna"

    def test_druga_stacja_ma_nazwe(self):
        stop = self.snapshot.trains[0].stops[1]
        assert stop.station_name == "Kraków Główny"

    def test_stacja_bez_nazwy_w_slowniku_daje_pusty_string(self):
        raw = {
            "trains": [{
                "scheduleId": 1, "orderId": 1, "operatingDate": "2026-05-27",
                "trainStatus": "P",
                "stations": [{"stationId": 99999, "plannedSequenceNumber": 1,
                               "actualSequenceNumber": 1}],
            }],
            "stations": {},  # pusty słownik
        }
        snapshot = parse_operations(raw, self.now)
        assert snapshot.trains[0].stops[0].station_name == ""


# ---------------------------------------------------------------------------
# Testy opóźnień
# ---------------------------------------------------------------------------

class TestOpoznienia:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)
        self.snapshot = parse_operations(MOCK_OPERATIONS, self.now)
        self.active_train = self.snapshot.trains[0]

    def test_opoznienie_przyjazdu(self):
        assert self.active_train.stops[0].delay_arrival_minutes == 5

    def test_opoznienie_odjazdu(self):
        assert self.active_train.stops[0].delay_departure_minutes == 6

    def test_punktualny_odjazd(self):
        assert self.active_train.stops[1].delay_departure_minutes == 0

    def test_max_delay_na_trasie(self):
        assert self.active_train.max_delay_minutes == 6

    def test_is_on_time_dla_punktualnego(self):
        assert self.active_train.stops[1].is_on_time is True

    def test_is_on_time_dla_opoznionego(self):
        assert self.active_train.stops[0].is_on_time is False


# ---------------------------------------------------------------------------
# Testy filtra anomalii
# ---------------------------------------------------------------------------

class TestFiltrAnomalie:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)
        self.snapshot = parse_operations(MOCK_OPERATIONS, self.now)

    def test_anomalia_1440_min_odfiltrowana(self):
        # Trzeci pociąg (S) ma przesunięcie o dobę – powinno zwrócić None
        scheduled_train = next(t for t in self.snapshot.trains if t.train_status == "S")
        stop = scheduled_train.stops[0]
        assert stop.delay_departure_minutes is None
        assert stop.delay_arrival_minutes is None

    def test_realne_opoznienie_199_min_przechodzi(self):
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 27, 13, 19),  # +199 min
        )
        assert stop.delay_departure_minutes == 199

    def test_realne_opoznienie_300_min_przechodzi(self):
        # 5h opóźnienie — realne przy poważnych incydentach, nie powinno być filtrowane
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 27, 15, 0),  # +300 min
        )
        assert stop.delay_departure_minutes == 300

    def test_realne_opoznienie_1000_min_przechodzi(self):
        # ~16h — ekstremalne ale realne opóźnienie w Polsce
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 28, 2, 40),  # +1000 min
        )
        assert stop.delay_departure_minutes == 1000

    def test_anomalia_1201_min_odfiltrowana(self):
        # Powyżej progu 1200 min — filtrowane
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 28, 10, 1),  # +1201 min
        )
        assert stop.delay_departure_minutes is None

    def test_anomalia_1440_min_odfiltrowana(self):
        # Dobowe przesunięcie rozkładowe = 1440 min — filtrowane
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 28, 10, 0),  # +1440 min
        )
        assert stop.delay_departure_minutes is None

    def test_anomalia_201_min_przechodzi_po_podwyzszeniu_progu(self):
        # 201 min było filtrowane przy progu 200, teraz powinno przechodzić
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=datetime(2026, 5, 27, 10, 0),
            actual_departure=datetime(2026, 5, 27, 13, 21),  # +201 min
        )
        assert stop.delay_departure_minutes == 201

    def test_wczesniejszy_przyjazd_ujemny(self):
        stop = StationStop(
            station_id="1", station_name="Test",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=datetime(2026, 5, 27, 10, 0),
            actual_arrival=datetime(2026, 5, 27, 9, 57),   # -3 min (wcześniej)
            planned_departure=None, actual_departure=None,
        )
        assert stop.delay_arrival_minutes == -3


# ---------------------------------------------------------------------------
# Testy statusów pociągu
# ---------------------------------------------------------------------------

class TestStatusyPociagu:
    def _make_train(self, status: str) -> TrainOperation:
        return TrainOperation(
            collected_at=datetime.utcnow(),
            schedule_id="2026", order_id="123",
            operating_date="2026-05-27",
            train_status=status,
            train_number=None, carrier_code=None,
        )

    def test_status_X_to_cancelled(self):
        t = self._make_train("X")
        assert t.is_cancelled is True
        assert t.is_in_progress is False
        assert t.is_completed is False
        assert t.is_scheduled is False

    def test_status_P_to_in_progress(self):
        t = self._make_train("P")
        assert t.is_in_progress is True
        assert t.is_cancelled is False

    def test_status_C_to_completed(self):
        t = self._make_train("C")
        assert t.is_completed is True
        assert t.is_cancelled is False

    def test_status_S_to_scheduled(self):
        t = self._make_train("S")
        assert t.is_scheduled is True
        assert t.is_cancelled is False


# ---------------------------------------------------------------------------
# Testy edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def setup_method(self):
        self.now = datetime(2026, 5, 27, 19, 45, 0)

    def test_pusta_lista_pociagow(self):
        snapshot = parse_operations({"trains": [], "stations": {}}, self.now)
        assert snapshot.total_trains == 0
        assert snapshot.total_stops == 0

    def test_brak_klucza_trains(self):
        snapshot = parse_operations({}, self.now)
        assert snapshot.total_trains == 0

    def test_brak_slownika_stacji(self):
        raw = {"trains": [{"scheduleId": 1, "orderId": 1,
                           "operatingDate": "2026-05-27", "trainStatus": "P",
                           "stations": []}]}
        snapshot = parse_operations(raw, self.now)
        assert snapshot.station_names == {}

    def test_minimalny_rekord_nie_crashuje(self):
        raw = {"trains": [{"scheduleId": 1, "orderId": 2, "stations": []}]}
        snapshot = parse_operations(raw, self.now)
        assert len(snapshot.trains) == 1

    def test_brak_dat_daje_none(self):
        stop = StationStop(
            station_id="1", station_name="",
            planned_sequence=1, actual_sequence=1,
            planned_arrival=None, actual_arrival=None,
            planned_departure=None, actual_departure=None,
        )
        assert stop.delay_arrival_minutes is None
        assert stop.delay_departure_minutes is None


# ---------------------------------------------------------------------------
# Testy stacji i przewoźników
# ---------------------------------------------------------------------------

class TestSlowniki:
    def test_parse_stations(self):
        stations = parse_stations(MOCK_STATIONS_RESPONSE)
        assert len(stations) == 2
        assert stations[0].station_id == "33506"
        assert stations[0].name == "Warszawa Centralna"
        assert stations[0].latitude == 52.2288

    def test_parse_stations_puste(self):
        assert parse_stations({}) == []

    def test_parse_carriers(self):
        carriers = parse_carriers(MOCK_CARRIERS_RESPONSE)
        assert len(carriers) == 2
        codes = [c.code for c in carriers]
        assert "IC" in codes
        assert "KM" in codes

    def test_parse_disruptions(self):
        d = parse_disruptions(MOCK_DISRUPTIONS_RESPONSE, datetime.utcnow())
        assert len(d) == 1
        assert d[0].disruption_id == "1001"
        assert "33506" in d[0].affected_stations
        assert "33590" in d[0].affected_stations
        assert d[0].message == "Roboty torowe Warszawa–Łódź. Ograniczenie prędkości."
        assert d[0].disruption_type_code == "utr_32"
        assert d[0].start_station_id == 33506

    def test_parse_disruptions_puste(self):
        assert parse_disruptions({}, datetime.utcnow()) == []

    def test_parse_disruptions_brak_affected_routes(self):
        raw = {"disruptions": [{"disruptionId": 2, "message": "Test"}]}
        d = parse_disruptions(raw, datetime.utcnow())
        assert d[0].affected_stations == []


class TestIsConfirmed:
    def setup_method(self):
        self.snapshot = parse_operations(MOCK_OPERATIONS, datetime(2026, 5, 27, 19, 45, 0))
        self.active_train = self.snapshot.trains[0]

    def test_potwierdzony_przystanek(self):
        assert self.active_train.stops[0].is_confirmed is True
        assert self.active_train.stops[1].is_confirmed is True

    def test_niepotwierdzony_przystanek(self):
        scheduled_train = next(t for t in self.snapshot.trains if t.train_status == "S")
        assert scheduled_train.stops[0].is_confirmed is False

    def test_brak_pola_daje_false(self):
        raw = {
            "trains": [{"scheduleId": 1, "orderId": 1, "operatingDate": "2026-05-27",
                        "trainStatus": "P",
                        "stations": [{"stationId": 1, "plannedSequenceNumber": 1,
                                      "actualSequenceNumber": 1}]}],
            "stations": {},
        }
        snapshot = parse_operations(raw, datetime.utcnow())
        assert snapshot.trains[0].stops[0].is_confirmed is False
