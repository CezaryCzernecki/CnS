"""
Modele danych odpowiedzi z PKP PLK API.
Oparte na rzeczywistej strukturze odpowiedzi (zweryfikowanej empirycznie 2026-05-27).

Struktura /operations (zweryfikowana):
  {
    "generatedAt": "...",
    "pagination": {...},
    "trains": [
      {
        "scheduleId": 2026,          ← int, nie string
        "orderId": 513569932,        ← int, nie string
        "trainOrderId": 513569932,
        "operatingDate": "2026-05-27",
        "trainStatus": "P",          ← S/P/C/X/Q
        "stations": [                ← BRAK trainNumber i carrierCode!
          {
            "stationId": 35428,      ← int
            "plannedSequenceNumber": 1,
            "actualSequenceNumber": 1,
            "plannedArrival": "2026-05-27T15:00:00",
            "plannedDeparture": "2026-05-27T15:00:00",
            "plannedArrivalTime": "15:00:00",
            "plannedDepartureTime": "15:00:00",
            "actualArrival": "2026-05-27T15:00:00",
            "actualDeparture": "2026-05-27T15:00:00"
          }
        ]
      }
    ],
    "stations": {                    ← słownik id→nazwa dołączony do odpowiedzi
      "35428": "Grodzisk Mazowiecki Radońska",
      ...
    }
  }

Statusy trainStatus (zweryfikowane empirycznie):
  S = scheduled   – zaplanowany, jeszcze nie ruszył
  P = in_progress – aktualnie w trasie
  C = completed   – zakończył kurs
  X = cancelled   – odwołany
  Q = unknown     – nieznany edge case

UWAGA: trainNumber i carrierCode są niedostępne w /operations.
Dostępne w /schedules – do połączenia po scheduleId + orderId.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class DayType(str, Enum):
    WORKING = "WORKING"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"
    HOLIDAY_EVE = "HOLIDAY_EVE"
    HOLIDAY_RETURN = "HOLIDAY_RETURN"
    WINTER_BREAK = "WINTER_BREAK"
    SUMMER_BREAK = "SUMMER_BREAK"
    LONG_WEEKEND = "LONG_WEEKEND"


@dataclass
class Station:
    station_id: str
    name: str
    short_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass
class Carrier:
    code: str
    name: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclass
class StationStop:
    """Jeden przystanek pociągu na trasie."""
    station_id: str          # zawsze string (castujemy z int z API)
    station_name: str        # wypełniane ze słownika stations z odpowiedzi
    planned_sequence: int
    actual_sequence: int
    planned_arrival: Optional[datetime]
    actual_arrival: Optional[datetime]
    planned_departure: Optional[datetime]
    actual_departure: Optional[datetime]

    # Próg filtrowania anomalii danych.
    # Przesunięcia rozkładowe (pociąg przełożony o dobę = 1440 min)
    # generują fałszywe wartości. Próg 1439 min to tuż przed dobowym artefaktem,
    # co pozwala rejestrować nawet ekstremalne realne opóźnienia (~1000 min).
    MAX_REALISTIC_DELAY = 1439

    # isConfirmed=True oznacza że pociąg faktycznie przejechał przez przystanek.
    # Dla przyszłych przystanków API zwraca isConfirmed=False.
    is_confirmed: bool = False
    is_cancelled: bool = False

    @property
    def delay_arrival_minutes(self) -> Optional[int]:
        """Opóźnienie przyjazdu w minutach. None jeśli brak danych lub anomalia."""
        if self.actual_arrival and self.planned_arrival:
            delta = int((self.actual_arrival - self.planned_arrival).total_seconds() / 60)
            if abs(delta) > self.MAX_REALISTIC_DELAY:
                return None
            return delta
        return None

    @property
    def delay_departure_minutes(self) -> Optional[int]:
        """Opóźnienie odjazdu w minutach. None jeśli brak danych lub anomalia."""
        if self.actual_departure and self.planned_departure:
            delta = int((self.actual_departure - self.planned_departure).total_seconds() / 60)
            if abs(delta) > self.MAX_REALISTIC_DELAY:
                return None
            return delta
        return None

    @property
    def is_on_time(self) -> bool:
        d = self.delay_departure_minutes
        return d is not None and d <= 0


@dataclass
class TrainOperation:
    """
    Realizacja jednego pociągu – cała trasa z wszystkimi przystankami.
    Odpowiada jednemu elementowi z listy 'trains' w odpowiedzi API.
    """
    collected_at: datetime
    schedule_id: str         # castujemy z int
    order_id: str            # castujemy z int
    operating_date: str
    train_status: str
    # Poniższe pola są niedostępne w /operations – zawsze None.
    # Dostępne w /schedules po połączeniu scheduleId + orderId.
    train_number: Optional[str]
    carrier_code: Optional[str]
    stops: list[StationStop] = field(default_factory=list)

    @property
    def is_cancelled(self) -> bool:
        return self.train_status == "X"

    @property
    def is_completed(self) -> bool:
        return self.train_status == "C"

    @property
    def is_in_progress(self) -> bool:
        return self.train_status == "P"

    @property
    def is_scheduled(self) -> bool:
        return self.train_status == "S"

    @property
    def max_delay_minutes(self) -> Optional[int]:
        """Maksymalne opóźnienie odjazdu na trasie (anomalie odfiltrowane)."""
        delays = [
            s.delay_departure_minutes
            for s in self.stops
            if s.delay_departure_minutes is not None
        ]
        return max(delays) if delays else None

    @property
    def current_delay_minutes(self) -> Optional[int]:
        """Opóźnienie na ostatnim przystanku który już minął."""
        now = datetime.utcnow()
        passed = [
            s for s in self.stops
            if s.actual_departure and s.actual_departure <= now
        ]
        if not passed:
            return None
        return passed[-1].delay_departure_minutes


@dataclass
class OperationsSnapshot:
    """Pełna odpowiedź z /operations – jeden snapshot w czasie."""
    fetched_at: datetime
    data_version_guid: Optional[str]
    total_trains: int
    total_stops: int
    station_names: dict[str, str]    # id → nazwa, ze słownika w odpowiedzi API
    trains: list[TrainOperation] = field(default_factory=list)
    raw: Optional[dict] = field(default=None, repr=False)

    @property
    def active_trains(self) -> list[TrainOperation]:
        """Tylko pociągi aktualnie w trasie (status P)."""
        return [t for t in self.trains if t.is_in_progress]

    @property
    def cancelled_trains(self) -> list[TrainOperation]:
        return [t for t in self.trains if t.is_cancelled]

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.trains:
            counts[t.train_status] = counts.get(t.train_status, 0) + 1
        return counts


@dataclass
class Disruption:
    """Utrudnienie kolejowe z /disruptions.
    Spec: DisruptionDto — brak title/dateFrom/dateTo/carriers w schemacie API.
    affected_stations to lista stationId z affectedRoutes[].stationId.
    """
    disruption_id: Optional[str]
    message: Optional[str]
    disruption_type_code: Optional[str] = None
    start_station_id: Optional[int] = None
    end_station_id: Optional[int] = None
    affected_stations: list[str] = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.utcnow)
