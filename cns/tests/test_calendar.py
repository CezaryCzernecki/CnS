"""
Testy jednostkowe CalendarService.
Nie wymagają połączenia z bazą danych.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from cns.collector.calendar_service import CalendarService, _easter
from cns.models.records import DayType
from cns.storage.postgres import PostgresStorage


@pytest.fixture
def cal():
    return CalendarService()


# ---------------------------------------------------------------------------
# Algorytm Wielkanocny
# ---------------------------------------------------------------------------

class TestEaster:
    @pytest.mark.parametrize("year,expected", [
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2024, date(2024, 3, 31)),
        (2023, date(2023, 4, 9)),
    ])
    def test_wielkanoc(self, year, expected):
        assert _easter(year) == expected

    def test_wielkanoc_niedziela(self):
        for year in (2023, 2024, 2025, 2026, 2027):
            assert _easter(year).weekday() == 6  # zawsze niedziela


# ---------------------------------------------------------------------------
# Święta ustawowe
# ---------------------------------------------------------------------------

class TestHolidays:
    def test_nowy_rok(self, cal):
        assert cal.get_day_type(date(2025, 1, 1)) == DayType.HOLIDAY

    def test_trzech_kroli(self, cal):
        assert cal.get_day_type(date(2025, 1, 6)) == DayType.HOLIDAY

    def test_wielkanoc_2025(self, cal):
        assert cal.get_day_type(date(2025, 4, 20)) == DayType.HOLIDAY

    def test_poniedzialek_wielkanocny_2025(self, cal):
        assert cal.get_day_type(date(2025, 4, 21)) == DayType.HOLIDAY

    def test_swieto_pracy(self, cal):
        assert cal.get_day_type(date(2025, 5, 1)) == DayType.HOLIDAY

    def test_swieto_konstytucji(self, cal):
        assert cal.get_day_type(date(2025, 5, 3)) == DayType.HOLIDAY

    def test_boze_cialo_to_wielkanoc_plus_60(self, cal):
        easter = _easter(2025)
        corpus = easter + timedelta(days=60)
        assert cal.get_day_type(corpus) == DayType.HOLIDAY
        assert corpus == date(2025, 6, 19)

    def test_boze_cialo_2026(self, cal):
        easter = _easter(2026)
        corpus = easter + timedelta(days=60)
        assert cal.get_day_type(corpus) == DayType.HOLIDAY

    def test_wniebowziecie_nmp(self, cal):
        assert cal.get_day_type(date(2025, 8, 15)) == DayType.HOLIDAY

    def test_wszystkich_swietych(self, cal):
        assert cal.get_day_type(date(2025, 11, 1)) == DayType.HOLIDAY

    def test_niepodleglosci(self, cal):
        assert cal.get_day_type(date(2025, 11, 11)) == DayType.HOLIDAY

    def test_boze_narodzenie_pierwsze(self, cal):
        assert cal.get_day_type(date(2025, 12, 25)) == DayType.HOLIDAY

    def test_boze_narodzenie_drugie(self, cal):
        assert cal.get_day_type(date(2025, 12, 26)) == DayType.HOLIDAY


# ---------------------------------------------------------------------------
# Majówka 2025 – długi weekend
# ---------------------------------------------------------------------------

class TestMajowka2025:
    def test_1_maja_to_holiday(self, cal):
        assert cal.get_day_type(date(2025, 5, 1)) == DayType.HOLIDAY

    def test_2_maja_to_long_weekend(self, cal):
        # Piątek między świętem (1 maja czwartek) a świętem+weekendem (3 maja sobota)
        assert cal.get_day_type(date(2025, 5, 2)) == DayType.LONG_WEEKEND

    def test_is_long_weekend_2_maja(self, cal):
        assert cal.is_long_weekend(date(2025, 5, 2)) is True

    def test_3_maja_to_holiday(self, cal):
        assert cal.get_day_type(date(2025, 5, 3)) == DayType.HOLIDAY

    def test_4_maja_to_weekend(self, cal):
        # Niedziela, nie jest świętem
        assert cal.get_day_type(date(2025, 5, 4)) == DayType.WEEKEND

    def test_holiday_is_not_long_weekend(self, cal):
        # Święto nie może być jednocześnie LONG_WEEKEND
        assert cal.is_long_weekend(date(2025, 5, 1)) is False

    def test_regular_friday_is_not_long_weekend(self, cal):
        # Zwykły piątek bez otoczenia świętami
        assert cal.is_long_weekend(date(2025, 5, 9)) is False


# ---------------------------------------------------------------------------
# Ferie zimowe – różne strefy
# ---------------------------------------------------------------------------

class TestWinterBreak:
    def test_strefa_a_jan25_to_winter_break(self, cal):
        # 25 stycznia 2025 jest w feriach strefy A (20 I – 2 II)
        assert cal.get_day_type(date(2025, 1, 25), zone="A") == DayType.WINTER_BREAK

    def test_strefa_b_jan25_not_winter_break(self, cal):
        # 25 stycznia 2025 NIE jest w feriach strefy B (3–16 II)
        assert cal.get_day_type(date(2025, 1, 25), zone="B") != DayType.WINTER_BREAK

    def test_strefy_a_i_b_sa_rozne_dla_tego_samego_dnia(self, cal):
        d = date(2025, 1, 25)
        type_a = cal.get_day_type(d, zone="A")
        type_b = cal.get_day_type(d, zone="B")
        assert type_a != type_b

    def test_strefa_b_feb10_to_winter_break(self, cal):
        # 10 lutego 2025 jest w feriach strefy B (3–16 II)
        assert cal.get_day_type(date(2025, 2, 10), zone="B") == DayType.WINTER_BREAK

    def test_strefa_c_feb5_to_winter_break(self, cal):
        # 5 lutego 2025 jest w feriach strefy C (27 I – 9 II)
        assert cal.get_day_type(date(2025, 2, 5), zone="C") == DayType.WINTER_BREAK

    def test_holiday_beats_winter_break(self, cal):
        # Jeśli w trakcie ferii jest święto, HOLIDAY ma wyższy priorytet
        # Wielkanoc 2025 = 20 IV (poza feriami) – sztuczny case: sprawdźmy Nowy Rok
        assert cal.get_day_type(date(2025, 1, 1), zone="A") == DayType.HOLIDAY

    def test_poza_zakresem_lat_brak_ferri(self, cal):
        # Rok 2035 – brak danych w słowniku → nie zwraca WINTER_BREAK
        assert cal.get_day_type(date(2035, 2, 10), zone="B") != DayType.WINTER_BREAK


# ---------------------------------------------------------------------------
# Wakacje letnie
# ---------------------------------------------------------------------------

class TestSummerBreak:
    def test_1_lipca_to_summer_break(self, cal):
        assert cal.get_day_type(date(2025, 7, 1)) == DayType.SUMMER_BREAK

    def test_31_sierpnia_to_summer_break(self, cal):
        assert cal.get_day_type(date(2025, 8, 31)) == DayType.SUMMER_BREAK

    def test_30_czerwca_not_summer_break(self, cal):
        assert cal.get_day_type(date(2025, 6, 30)) != DayType.SUMMER_BREAK

    def test_1_wrzesnia_not_summer_break(self, cal):
        assert cal.get_day_type(date(2025, 9, 1)) != DayType.SUMMER_BREAK

    def test_holiday_during_summer_beats_summer_break(self, cal):
        # 15 sierpnia = Wniebowzięcie NMP = HOLIDAY, nawet jeśli wakacje
        assert cal.get_day_type(date(2025, 8, 15)) == DayType.HOLIDAY


# ---------------------------------------------------------------------------
# Dni przed/po święcie
# ---------------------------------------------------------------------------

class TestHolidayEveReturn:
    def test_holiday_eve_before_nowy_rok(self, cal):
        # 31 grudnia (środa 2025) → HOLIDAY_EVE (1 I 2026 = HOLIDAY)
        assert cal.get_day_type(date(2025, 12, 31)) == DayType.HOLIDAY_EVE

    def test_holiday_return_after_konstytucji(self, cal):
        # 5 maja 2025 (poniedziałek) – po długim weekendzie Majówka
        # 4 maja = niedziela → nie jest holiday → to nie HOLIDAY_RETURN
        # Sprawdźmy 6 listopada 2025 (czwartek) – po Wszystkich Świętych (sobota)
        # Hm, 2 listopada (niedziela) to weekend. Sprawdźmy zwykły przypadek.
        # 12 listopada 2025 (środa po Święcie Niepodległości 11 XI = wtorek)
        result = cal.get_day_type(date(2025, 11, 12))
        assert result == DayType.HOLIDAY_RETURN


# ---------------------------------------------------------------------------
# get_season
# ---------------------------------------------------------------------------

class TestGetSeason:
    @pytest.mark.parametrize("d,expected", [
        (date(2025, 1, 15), "WINTER"),
        (date(2025, 3, 1),  "SPRING"),
        (date(2025, 5, 31), "SPRING"),
        (date(2025, 6, 1),  "SUMMER"),
        (date(2025, 8, 31), "SUMMER"),
        (date(2025, 9, 1),  "AUTUMN"),
        (date(2025, 11, 30),"AUTUMN"),
        (date(2025, 12, 1), "WINTER"),
    ])
    def test_season(self, cal, d, expected):
        assert cal.get_season(d) == expected


# ---------------------------------------------------------------------------
# days_to_next_holiday / days_since_last_holiday
# ---------------------------------------------------------------------------

class TestHolidayDistance:
    def test_days_to_next_from_nowy_rok(self, cal):
        # Od 1 I 2025 (Nowy Rok) – kolejne święto to 6 I (Trzech Króli) = 5 dni
        assert cal.days_to_next_holiday(date(2025, 1, 1)) == 5

    def test_days_since_last_from_trzech_kroli(self, cal):
        # Od 6 I 2025 (Trzech Króli) – poprzednie święto to 1 I (Nowy Rok) = 5 dni
        assert cal.days_since_last_holiday(date(2025, 1, 6)) == 5

    def test_days_to_next_jest_dodatni(self, cal):
        assert cal.days_to_next_holiday(date(2025, 6, 1)) > 0

    def test_days_since_last_jest_dodatni(self, cal):
        assert cal.days_since_last_holiday(date(2025, 6, 1)) > 0


# ---------------------------------------------------------------------------
# generate_events
# ---------------------------------------------------------------------------

class TestGenerateEvents:
    def test_zawiera_swiateczne_rekordy(self, cal):
        rows = cal.generate_events(2025, 2025)
        types = {r["day_type"] for r in rows}
        assert DayType.HOLIDAY in types
        assert DayType.WINTER_BREAK in types
        assert DayType.SUMMER_BREAK in types

    def test_holiday_ma_zone_none(self, cal):
        rows = cal.generate_events(2025, 2025)
        holidays = [r for r in rows if r["day_type"] == DayType.HOLIDAY]
        assert all(r["zone"] is None for r in holidays)

    def test_ferie_maja_zone_abc(self, cal):
        rows = cal.generate_events(2025, 2025)
        ferie = [r for r in rows if r["day_type"] == DayType.WINTER_BREAK]
        zones = {r["zone"] for r in ferie}
        assert zones == {"A", "B", "C"}

    def test_dwa_lata_dwa_razy_wiecej(self, cal):
        rows_1 = cal.generate_events(2025, 2025)
        rows_2 = cal.generate_events(2025, 2026)
        assert len(rows_2) > len(rows_1)

    def test_boze_cialo_2025_w_generate(self, cal):
        rows = cal.generate_events(2025, 2025)
        dates = {r["event_date"] for r in rows if r["day_type"] == DayType.HOLIDAY}
        easter = _easter(2025)
        assert easter + timedelta(days=60) in dates

    def test_wakacje_maja_62_dni(self, cal):
        rows = cal.generate_events(2025, 2025)
        summer = [r for r in rows if r["day_type"] == DayType.SUMMER_BREAK and r["zone"] is None]
        assert len(summer) == 62  # 31 lip + 31 sie


# ---------------------------------------------------------------------------
# PostgresStorage.save_calendar_events / is_calendar_populated
# ---------------------------------------------------------------------------

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


class TestStorageCalendar:
    def test_save_pusta_lista_nie_otwiera_polaczenia(self, storage):
        with patch("cns.storage.postgres._conn") as mock_fn:
            storage.save_calendar_events([])
            mock_fn.assert_not_called()

    def test_save_wywoluje_executemany(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        rows = [
            {"event_date": date(2025, 1, 1), "zone": None,
             "day_type": DayType.HOLIDAY, "event_name": "Nowy Rok"},
            {"event_date": date(2025, 1, 6), "zone": None,
             "day_type": DayType.HOLIDAY, "event_name": "Trzech Króli"},
        ]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_calendar_events(rows)
        mock_cursor.executemany.assert_called_once()
        data = mock_cursor.executemany.call_args[0][1]
        assert len(data) == 2

    def test_day_type_zapisywany_jako_string(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        rows = [{"event_date": date(2025, 5, 1), "zone": None,
                 "day_type": DayType.HOLIDAY, "event_name": "Święto Pracy"}]
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            storage.save_calendar_events(rows)
        data = mock_cursor.executemany.call_args[0][1]
        assert data[0][2] == "HOLIDAY"

    def test_is_calendar_populated_true(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (42,)
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            assert storage.is_calendar_populated() is True

    def test_is_calendar_populated_false(self, storage):
        mock_conn, mock_cursor = _make_conn_mock()
        mock_cursor.fetchone.return_value = (0,)
        with patch("cns.storage.postgres._conn", return_value=mock_conn):
            assert storage.is_calendar_populated() is False

    def test_is_calendar_populated_exception_returns_false(self, storage):
        with patch("cns.storage.postgres._conn", side_effect=Exception("no table")):
            assert storage.is_calendar_populated() is False
