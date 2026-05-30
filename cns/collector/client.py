"""
PKP PLK Open Data API client.
Dokumentacja: https://pdp-api.plk-sa.pl/api-documentation
"""

import time
import logging
from datetime import date, datetime
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://pdp-api.plk-sa.pl/api/v1"


class RateLimitError(Exception):
    """HTTP 429 – przekroczono limit zapytań."""
    def __init__(self, message: str, retry_after: int = None):
        super().__init__(message)
        self.retry_after = retry_after  # sekundy do odczekania (z nagłówka Retry-After)


class PKPClient:
    """
    Klient HTTP dla PKP PLK Open Data API.

    Obsługuje:
    - autoryzację przez X-API-Key
    - automatyczne retry dla błędów 5xx
    - respektowanie nagłówków rate-limit (X-RateLimit-*)
    - sprawdzanie wersji danych przed pobraniem (oszczędność limitów)
    """

    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

        self.rate_limit_hourly_remaining: Optional[int] = None
        self.rate_limit_daily_remaining: Optional[int] = None

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{BASE_URL}{path}"
        params = params or {}
        logger.debug("GET %s params=%s", path, params)

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.ConnectionError as e:
            logger.error("Błąd połączenia z API PKP: %s", e)
            raise

        self._update_rate_limits(response)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                f"Przekroczono limit API. "
                f"Godzinowy: {self.rate_limit_hourly_remaining}, "
                f"Dzienny: {self.rate_limit_daily_remaining}. "
                f"Retry-After: {retry_after or 'brak'}s",
                retry_after=int(retry_after) if retry_after else None,
            )

        response.raise_for_status()
        return response.json()

    def _update_rate_limits(self, response: requests.Response) -> None:
        hourly = response.headers.get("X-RateLimit-Hourly-Remaining")
        daily = response.headers.get("X-RateLimit-Daily-Remaining")

        if hourly is not None:
            self.rate_limit_hourly_remaining = int(hourly)
        if daily is not None:
            self.rate_limit_daily_remaining = int(daily)

        if self.rate_limit_hourly_remaining is not None:
            logger.debug(
                "Rate limit – godzinowy: %d, dzienny: %d",
                self.rate_limit_hourly_remaining,
                self.rate_limit_daily_remaining or -1,
            )
            if self.rate_limit_hourly_remaining < 10:
                logger.warning(
                    "⚠️  Mało pozostałych zapytań godzinowych: %d",
                    self.rate_limit_hourly_remaining,
                )

    # -------------------------------------------------------------------------
    # Słowniki
    # -------------------------------------------------------------------------

    def get_stations(self, search: str = None, page: int = 1, page_size: int = 5000) -> dict:
        params = {"page": page, "pageSize": page_size}
        if search:
            params["search"] = search
        return self._get("/dictionaries/stations", params)

    def get_carriers(self) -> dict:
        return self._get("/dictionaries/carriers")

    # -------------------------------------------------------------------------
    # Wersja danych
    # -------------------------------------------------------------------------

    def get_data_version(self) -> dict:
        return self._get("/data-version")

    # -------------------------------------------------------------------------
    # Rozkład planowy
    # -------------------------------------------------------------------------

    def get_schedules(
        self,
        date_from: date = None,
        date_to: date = None,
        stations: list[str] = None,
        carriers_include: list[str] = None,
        carriers_exclude: list[str] = None,
        shortened: bool = False,
    ) -> dict:
        params = {}
        if date_from:
            params["dateFrom"] = date_from.isoformat()
        if date_to:
            params["dateTo"] = date_to.isoformat()
        if stations:
            params["stations"] = ",".join(stations)
        if carriers_include:
            params["carriersInclude"] = ",".join(carriers_include)
        if carriers_exclude:
            params["carriersExclude"] = ",".join(carriers_exclude)
        path = "/schedules/shortened" if shortened else "/schedules"
        return self._get(path, params)

    # -------------------------------------------------------------------------
    # Dane operacyjne real-time
    # -------------------------------------------------------------------------

    def get_operations(
        self,
        stations: list[str] = None,
        carriers_include: list[str] = None,
        carriers_exclude: list[str] = None,
        full_routes: bool = False,
        with_planned: bool = True,
        page: int = 1,
        page_size: int = 10000,
        shortened: bool = False,
    ) -> dict:
        params = {
            "withPlanned": str(with_planned).lower(),
            "fullRoutes": str(full_routes).lower(),
            "page": page,
            "pageSize": page_size,
        }
        if stations:
            params["stations"] = ",".join(stations)
        if carriers_include:
            params["carriersInclude"] = ",".join(carriers_include)
        if carriers_exclude:
            params["carriersExclude"] = ",".join(carriers_exclude)
        path = "/operations/shortened" if shortened else "/operations"
        return self._get(path, params)

    def get_operations_statistics(self, stats_date: date = None) -> dict:
        params = {}
        if stats_date:
            params["date"] = stats_date.isoformat()
        return self._get("/operations/statistics", params)

    # -------------------------------------------------------------------------
    # Utrudnienia
    # -------------------------------------------------------------------------

    def get_disruptions(
        self,
        date_from: date = None,
        date_to: date = None,
        stations: list[str] = None,
        carriers_include: list[str] = None,
    ) -> dict:
        params = {}
        if date_from:
            params["dateFrom"] = date_from.isoformat()
        if date_to:
            params["dateTo"] = date_to.isoformat()
        if stations:
            params["stations"] = ",".join(stations)
        if carriers_include:
            params["carriersInclude"] = ",".join(carriers_include)
        return self._get("/disruptions", params)

    # -------------------------------------------------------------------------
    # Info o kluczu
    # -------------------------------------------------------------------------

    def get_api_key_info(self) -> dict:
        return self._get("/apikey/info")

    def get_api_usage(self) -> dict:
        return self._get("/apikey/usage")