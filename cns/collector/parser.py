"""
Parser odpowiedzi API → typed dataclass models.
Oparty na rzeczywistej strukturze odpowiedzi (zweryfikowanej empirycznie 2026-05-27).

Kluczowe ustalenia:
- Klucz listy pociągów to 'trains' (nie 'operations')
- Każdy pociąg zawiera zagnieżdżoną listę 'stations' (przystanki na trasie)
- Odpowiedź zawiera też 'stations' na poziomie głównym – słownik id→nazwa
- stationId, scheduleId, orderId to int w JSON – castujemy na str
- trainNumber i carrierCode są NIEDOSTĘPNE w /operations (zawsze None)
- Opóźnienia liczone z actual - planned (API nie zwraca gotowych wartości)
"""

import logging
from datetime import datetime
from typing import Optional

from cns.models.records import (
    Carrier, Disruption, OperationsSnapshot, Station,
    StationStop, TrainOperation,
)

logger = logging.getLogger(__name__)

# Słowa kluczowe wskazujące na autobusową komunikację zastępczą (KZ).
# Pokrywają wszystkie formy fleksyjne polskiego przymiotnika "zastępczy".
_BUS_KW: tuple[str, ...] = (
    "komunikacja zastępcz",
    "komunikację zastępcz",
    "komunikacji zastępcz",
    "zastępcza komunikacja",
    "zastępczą komunikacj",
    "autobus zastępczy",
    "autobusy zastępcze",
    "autobusami zastępczymi",
)


def detect_bus_replacement(message: Optional[str], type_code: Optional[str]) -> bool:
    """Zwraca True jeśli utrudnienie wiąże się z autobusową komunikacją zastępczą."""
    msg = (message or "").lower()
    if any(kw in msg for kw in _BUS_KW):
        return True
    code = (type_code or "").lower()
    return "kz" in code or "bus_rep" in code



def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parsuj datetime z formatu API: '2026-05-27T15:00:00' (bez strefy czasowej)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except (ValueError, AttributeError):
        logger.debug("Nie można sparsować daty: %r", value)
        return None


def parse_stations(raw: dict) -> list[Station]:
    """Parsuj odpowiedź z /dictionaries/stations."""
    items = raw.get("stations") or raw.get("items") or raw.get("data") or []
    # /dictionaries/stations zwraca listę obiektów
    if isinstance(items, dict):
        # gdyby kiedyś zwróciło słownik id→nazwa (jak w /operations)
        return [Station(station_id=k, name=v) for k, v in items.items()]
    stations = []
    for item in items:
        stations.append(Station(
            station_id=str(item.get("id") or item.get("stationId") or ""),
            name=item.get("name") or item.get("stationName") or "",
            short_name=item.get("shortName"),
            latitude=item.get("latitude") or item.get("lat"),
            longitude=item.get("longitude") or item.get("lon"),
        ))
    logger.info("Sparsowano %d stacji", len(stations))
    return stations


def parse_carriers(raw: dict) -> list[Carrier]:
    """Parsuj odpowiedź z /dictionaries/carriers."""
    items = raw.get("carriers") or raw.get("items") or raw.get("data") or []
    carriers = []
    for item in items:
        carriers.append(Carrier(
            code=item.get("code") or "",
            name=item.get("name") or "",
            valid_from=item.get("validFrom"),
            valid_to=item.get("validTo"),
        ))
    logger.info("Sparsowano %d przewoźników", len(carriers))
    return carriers


def parse_operations(raw: dict, fetched_at: datetime, data_version: str = None) -> OperationsSnapshot:
    """
    Parsuj odpowiedź z /operations.

    Rzeczywista struktura (zweryfikowana empirycznie):
    {
      "generatedAt": "...",
      "pagination": {...},
      "trains": [ { ...pociąg z listą stations... } ],
      "stations": { "35428": "Grodzisk Mazowiecki Radońska", ... }  ← słownik nazw!
    }
    """
    trains_raw = raw.get("trains") or raw.get("data") or []

    # Słownik nazw stacji dołączony do odpowiedzi – używamy zamiast osobnego zapytania
    station_names: dict[str, str] = {}
    raw_stations = raw.get("stations", {})
    if isinstance(raw_stations, dict):
        # Klucze mogą być int lub str – normalizujemy na str
        station_names = {str(k): v for k, v in raw_stations.items()}
    logger.debug("Słownik stacji w odpowiedzi: %d pozycji", len(station_names))

    trains = []
    total_stops = 0

    for item in trains_raw:
        try:
            train = _parse_single_train(item, fetched_at, station_names)
            trains.append(train)
            total_stops += len(train.stops)
        except Exception as e:
            logger.warning("Pominięto rekord pociągu z powodu błędu: %s", e)

    snapshot = OperationsSnapshot(
        fetched_at=fetched_at,
        data_version_guid=data_version,
        total_trains=len(trains),
        total_stops=total_stops,
        station_names=station_names,
        trains=trains,
        raw=raw,
    )

    logger.info(
        "Snapshot: %d pociągów, %d przystanków, %d nazw stacji, wersja=%s",
        len(trains), total_stops, len(station_names), data_version or "brak",
    )
    return snapshot


def _parse_single_train(
    item: dict,
    fetched_at: datetime,
    station_names: dict[str, str],
) -> TrainOperation:
    """Parsuj jeden rekord pociągu z listy trains."""
    stops_raw = item.get("stations") or item.get("stops") or []
    stops = [_parse_stop(s, station_names) for s in stops_raw]

    # orderId = ID trasy rozkładowej (stabilne). trainOrderId = ID konkretnego
    # przejazdu, obecny TYLKO gdy różni się od orderId. Nie używamy trainOrderId
    # jako fallback – miałby inne znaczenie semantyczne przy joinie z schedules.
    return TrainOperation(
        collected_at=fetched_at,
        schedule_id=str(item.get("scheduleId") or ""),
        order_id=str(item.get("orderId") or ""),
        operating_date=item.get("operatingDate") or "",
        train_status=item.get("trainStatus") or "",
        train_number=None,
        carrier_code=None,
        stops=stops,
    )


def _parse_stop(s: dict, station_names: dict[str, str]) -> StationStop:
    """Parsuj jeden przystanek na trasie pociągu."""
    station_id = str(s.get("stationId") or "")
    station_name = station_names.get(station_id, "")

    return StationStop(
        station_id=station_id,
        station_name=station_name,
        planned_sequence=int(s.get("plannedSequenceNumber") or 0),
        actual_sequence=int(s.get("actualSequenceNumber") or 0),
        planned_arrival=_parse_dt(s.get("plannedArrival")),
        actual_arrival=_parse_dt(s.get("actualArrival")),
        planned_departure=_parse_dt(s.get("plannedDeparture")),
        actual_departure=_parse_dt(s.get("actualDeparture")),
        is_confirmed=bool(s.get("isConfirmed", False)),
        is_cancelled=bool(s.get("isCancelled", False)),
    )


def parse_disruptions(raw: dict, collected_at: datetime) -> list[Disruption]:
    """Parsuj odpowiedź z /disruptions.

    Spec DisruptionDto: disruptionId, disruptionTypeCode, startStationId,
    endStationId, message, affectedRoutes[].
    Brak: title, dateFrom, dateTo, carriers — pola nieistniejące w schemacie API.
    affected_stations wyciągamy z affectedRoutes[].stationId.
    """
    items = raw.get("disruptions") or raw.get("items") or raw.get("data") or []
    disruptions = []
    for item in items:
        affected_routes = item.get("affectedRoutes") or []
        affected_stations = list({
            str(r["stationId"])
            for r in affected_routes
            if r.get("stationId")
        })
        type_code = item.get("disruptionTypeCode")
        message = item.get("message")
        disruptions.append(Disruption(
            disruption_id=str(item.get("disruptionId") or item.get("id") or ""),
            message=message,
            disruption_type_code=type_code,
            start_station_id=item.get("startStationId"),
            end_station_id=item.get("endStationId"),
            has_bus_replacement=detect_bus_replacement(message, type_code),
            affected_stations=affected_stations,
            collected_at=collected_at,
        ))
    logger.info("Sparsowano %d utrudnień", len(disruptions))
    return disruptions
