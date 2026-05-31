"""
Polski kalendarz kolejowy – święta, ferie, wakacje, długie weekendy.
Używany do feature engineering modelu predykcji opóźnień.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from cns.models.records import DayType


# ---------------------------------------------------------------------------
# Algorytm Butchera/Meeusa – wyznacza datę Wielkanocy (kalendarz gregoriański)
# ---------------------------------------------------------------------------

def _easter(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Ferie zimowe – hardcoded 2024-2030 per strefa MEN
#   Strefa A: dolnośląskie, opolskie, zachodniopomorskie, wielkopolskie
#   Strefa B: kujawsko-pomorskie, lubuskie, łódzkie, małopolskie, świętokrzyskie, pomorskie
#   Strefa C: lubelskie, mazowieckie, podkarpackie, podlaskie, śląskie, warmińsko-mazurskie
# ---------------------------------------------------------------------------

_WINTER_BREAKS: dict[int, dict[str, tuple[date, date]]] = {
    2024: {
        "A": (date(2024, 1, 29), date(2024, 2, 11)),
        "B": (date(2024, 2, 12), date(2024, 2, 25)),
        "C": (date(2024, 2, 5),  date(2024, 2, 18)),
    },
    2025: {
        "A": (date(2025, 1, 20), date(2025, 2, 2)),
        "B": (date(2025, 2, 3),  date(2025, 2, 16)),
        "C": (date(2025, 1, 27), date(2025, 2, 9)),
    },
    2026: {
        "A": (date(2026, 2, 2),  date(2026, 2, 15)),
        "B": (date(2026, 1, 26), date(2026, 2, 8)),
        "C": (date(2026, 2, 9),  date(2026, 2, 22)),
    },
    2027: {
        "A": (date(2027, 2, 15), date(2027, 2, 28)),
        "B": (date(2027, 2, 1),  date(2027, 2, 14)),
        "C": (date(2027, 1, 25), date(2027, 2, 7)),
    },
    2028: {
        "A": (date(2028, 1, 24), date(2028, 2, 6)),
        "B": (date(2028, 2, 7),  date(2028, 2, 20)),
        "C": (date(2028, 2, 14), date(2028, 2, 27)),
    },
    2029: {
        "A": (date(2029, 2, 11), date(2029, 2, 24)),
        "B": (date(2029, 1, 28), date(2029, 2, 10)),
        "C": (date(2029, 2, 4),  date(2029, 2, 17)),
    },
    2030: {
        "A": (date(2030, 1, 27), date(2030, 2, 9)),
        "B": (date(2030, 2, 10), date(2030, 2, 23)),
        "C": (date(2030, 2, 3),  date(2030, 2, 16)),
    },
}


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# CalendarService
# ---------------------------------------------------------------------------

class CalendarService:
    """
    Wyznacza typ dnia kalendarzowego dla potrzeb feature engineering.

    Hierarchia priorytetów w get_day_type():
      HOLIDAY > WINTER_BREAK > SUMMER_BREAK > WEEKEND >
      LONG_WEEKEND > HOLIDAY_EVE > HOLIDAY_RETURN > WORKING
    """

    def __init__(self) -> None:
        self._holiday_cache: dict[int, dict[date, str]] = {}

    # ------------------------------------------------------------------
    # Wewnętrzne helpers
    # ------------------------------------------------------------------

    def _get_holidays(self, year: int) -> dict[date, str]:
        if year not in self._holiday_cache:
            easter = _easter(year)
            corpus = easter + timedelta(days=60)
            self._holiday_cache[year] = {
                date(year, 1, 1):          "Nowy Rok",
                date(year, 1, 6):          "Trzech Króli",
                easter:                    "Wielkanoc",
                easter + timedelta(days=1):"Poniedziałek Wielkanocny",
                date(year, 5, 1):          "Święto Pracy",
                date(year, 5, 3):          "Święto Konstytucji 3 Maja",
                corpus:                    "Boże Ciało",
                date(year, 8, 15):         "Wniebowzięcie NMP",
                date(year, 11, 1):         "Wszystkich Świętych",
                date(year, 11, 11):        "Święto Niepodległości",
                date(year, 12, 25):        "Boże Narodzenie",
                date(year, 12, 26):        "Drugi dzień Bożego Narodzenia",
            }
        return self._holiday_cache[year]

    def _is_holiday(self, d: date) -> bool:
        return d in self._get_holidays(d.year)

    def _is_summer_break(self, d: date) -> bool:
        return date(d.year, 7, 1) <= d <= date(d.year, 8, 31)

    def _is_winter_break(self, d: date, zone: str) -> bool:
        entry = _WINTER_BREAKS.get(d.year, {}).get(zone.upper())
        if entry:
            return entry[0] <= d <= entry[1]
        return False

    def _is_nonworking(self, d: date) -> bool:
        return self._is_holiday(d) or d.weekday() >= 5

    # ------------------------------------------------------------------
    # Publiczne API
    # ------------------------------------------------------------------

    def get_day_type(self, d: date, zone: str = "B") -> DayType:
        if self._is_holiday(d):
            return DayType.HOLIDAY
        if self._is_winter_break(d, zone):
            return DayType.WINTER_BREAK
        if self._is_summer_break(d):
            return DayType.SUMMER_BREAK
        if d.weekday() >= 5:
            return DayType.WEEKEND
        if self.is_long_weekend(d):
            return DayType.LONG_WEEKEND
        if self._is_holiday(d + timedelta(days=1)):
            return DayType.HOLIDAY_EVE
        if self._is_holiday(d - timedelta(days=1)):
            return DayType.HOLIDAY_RETURN
        return DayType.WORKING

    def is_long_weekend(self, d: date) -> bool:
        """True jeśli dzień roboczy jest pomostem (bridge) między świętem a weekendem."""
        if d.weekday() >= 5 or self._is_holiday(d):
            return False
        return self._is_nonworking(d - timedelta(days=1)) and self._is_nonworking(d + timedelta(days=1))

    def days_to_next_holiday(self, d: date) -> int:
        for i in range(1, 367):
            if self._is_holiday(d + timedelta(days=i)):
                return i
        return 366

    def days_since_last_holiday(self, d: date) -> int:
        for i in range(1, 367):
            if self._is_holiday(d - timedelta(days=i)):
                return i
        return 366

    def get_season(self, d: date) -> str:
        m = d.month
        if m in (3, 4, 5):
            return "SPRING"
        if m in (6, 7, 8):
            return "SUMMER"
        if m in (9, 10, 11):
            return "AUTUMN"
        return "WINTER"

    def generate_events(self, year_from: int, year_to: int) -> list[dict]:
        """Generuje rekordy do tabeli calendar_events dla podanego zakresu lat."""
        rows: list[dict] = []
        for year in range(year_from, year_to + 1):
            for d, name in self._get_holidays(year).items():
                rows.append({
                    "event_date": d, "zone": None,
                    "day_type": DayType.HOLIDAY, "event_name": name,
                })
            for zone in ("A", "B", "C"):
                entry = _WINTER_BREAKS.get(year, {}).get(zone)
                if entry:
                    for d in _date_range(*entry):
                        rows.append({
                            "event_date": d, "zone": zone,
                            "day_type": DayType.WINTER_BREAK,
                            "event_name": f"Ferie zimowe strefa {zone}",
                        })
            for d in _date_range(date(year, 7, 1), date(year, 8, 31)):
                rows.append({
                    "event_date": d, "zone": None,
                    "day_type": DayType.SUMMER_BREAK,
                    "event_name": "Wakacje letnie",
                })
        return rows
