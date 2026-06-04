"""
Testy jednostkowe DataCollector – głównie logika harmonogramu pobierania danych.
Wszystkie wywołania HTTP i storage są mockowane.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from cns.collector.collector import DataCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage():
    storage = MagicMock()
    storage.save_schedules = MagicMock()
    storage.save_snapshot = MagicMock()
    storage.save_disruptions = MagicMock()
    return storage


def _make_collector(storage=None, dry_run=False):
    with patch("cns.collector.collector.PKPClient") as MockClient:
        instance = MockClient.return_value
        instance.get_carriers.return_value = {"carriers": []}
        instance.get_stations.return_value = {"stations": {}}
        instance.get_schedules.return_value = {"routes": []}
        instance.get_data_version.return_value = {"operationsVersion": "v1"}
        dc = DataCollector(
            api_key="test-key",
            storage=storage or _make_storage(),
            dry_run=dry_run,
        )
        dc.client = instance
        return dc


# ---------------------------------------------------------------------------
# Testy _fetch_schedules_if_needed
# ---------------------------------------------------------------------------

class TestFetchSchedules:
    def test_fetches_yesterday_and_today(self):
        """Powinien pobierać rozkłady od wczoraj do dziś – dla pociągów nocnych."""
        storage = _make_storage()
        dc = _make_collector(storage)
        dc._last_schedules_date = None

        today = date.today()
        yesterday = today - timedelta(days=1)

        dc.client.get_schedules.return_value = {"routes": []}

        dc._fetch_schedules_if_needed()

        dc.client.get_schedules.assert_called_once_with(
            date_from=yesterday, date_to=today
        )

    def test_skips_when_already_fetched_today(self):
        """Nie powinien wykonywać zapytania gdy rozkład już pobrano dziś."""
        dc = _make_collector()
        dc._last_schedules_date = date.today()

        dc._fetch_schedules_if_needed()

        dc.client.get_schedules.assert_not_called()

    def test_saves_to_storage_in_normal_mode(self):
        """W trybie normalnym (nie dry_run) dane trafiają do storage."""
        storage = _make_storage()
        dc = _make_collector(storage, dry_run=False)
        dc._last_schedules_date = None

        raw = {"routes": [{"scheduleId": 1}]}
        dc.client.get_schedules.return_value = raw

        dc._fetch_schedules_if_needed()

        storage.save_schedules.assert_called_once_with(raw)

    def test_dry_run_skips_storage(self):
        """W trybie dry_run storage.save_schedules nie jest wywoływane."""
        storage = _make_storage()
        dc = _make_collector(storage, dry_run=True)
        dc._last_schedules_date = None

        dc.client.get_schedules.return_value = {"routes": []}

        dc._fetch_schedules_if_needed()

        storage.save_schedules.assert_not_called()

    def test_updates_last_schedules_date_after_fetch(self):
        """Po pobraniu _last_schedules_date powinno być ustawione na dziś."""
        dc = _make_collector()
        dc._last_schedules_date = None

        dc._fetch_schedules_if_needed()

        assert dc._last_schedules_date == date.today()

    def test_refetches_on_new_day(self):
        """Gdy _last_schedules_date to wczoraj, powinien ponownie pobrać."""
        dc = _make_collector()
        dc._last_schedules_date = date.today() - timedelta(days=1)

        dc._fetch_schedules_if_needed()

        dc.client.get_schedules.assert_called_once()
