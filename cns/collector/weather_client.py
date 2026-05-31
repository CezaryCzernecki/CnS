"""
Open-Meteo weather client – bezpłatne API pogodowe bez klucza API.
Dokumentacja: https://open-meteo.com/en/docs
"""

import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_FIELDS = (
    "temperature_2m,precipitation,wind_speed_10m,"
    "snowfall,visibility,cloud_cover,weather_code"
)


class WeatherClient:
    """
    Klient Open-Meteo API (bezpłatny, bez klucza API).
    Retry 3x z backoff 2s/4s/8s — identycznie jak PKPClient.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _get(self, params: dict) -> dict:
        logger.debug(
            "Open-Meteo GET lat=%.4f lon=%.4f",
            params.get("latitude", 0),
            params.get("longitude", 0),
        )
        try:
            response = self.session.get(_BASE_URL, params=params, timeout=self.timeout)
        except requests.ConnectionError as e:
            logger.error("Błąd połączenia z Open-Meteo: %s", e)
            raise
        response.raise_for_status()
        return response.json()

    def get_current(self, station_id: str, lat: float, lon: float) -> dict:
        """Pobiera bieżące warunki pogodowe dla stacji (is_forecast=False)."""
        raw = self._get({
            "latitude": lat,
            "longitude": lon,
            "current": _FIELDS,
            "timezone": "UTC",
        })
        c = raw.get("current", {})
        return {
            "station_id": station_id,
            "observed_at": c.get("time"),
            "is_forecast": False,
            "temperature_c": c.get("temperature_2m"),
            "precipitation_mm": c.get("precipitation"),
            "wind_speed_kmh": c.get("wind_speed_10m"),
            "snowfall_cm": c.get("snowfall"),
            "visibility_m": _to_int(c.get("visibility")),
            "cloud_cover_pct": _to_int(c.get("cloud_cover")),
            "weather_code": _to_int(c.get("weather_code")),
        }

    def get_forecast_48h(self, station_id: str, lat: float, lon: float) -> list[dict]:
        """Pobiera prognozę godzinową na 48h dla stacji (is_forecast=True)."""
        raw = self._get({
            "latitude": lat,
            "longitude": lon,
            "hourly": _FIELDS,
            "forecast_days": 2,
            "timezone": "UTC",
        })
        hourly = raw.get("hourly", {})
        times = hourly.get("time", [])
        arrays = {
            "temperature_c": hourly.get("temperature_2m", []),
            "precipitation_mm": hourly.get("precipitation", []),
            "wind_speed_kmh": hourly.get("wind_speed_10m", []),
            "snowfall_cm": hourly.get("snowfall", []),
            "visibility_m": hourly.get("visibility", []),
            "cloud_cover_pct": hourly.get("cloud_cover", []),
            "weather_code": hourly.get("weather_code", []),
        }
        _int_cols = {"visibility_m", "cloud_cover_pct", "weather_code"}
        result = []
        for i, t in enumerate(times[:48]):
            row: dict = {"station_id": station_id, "observed_at": t, "is_forecast": True}
            for col, values in arrays.items():
                v = values[i] if i < len(values) else None
                row[col] = _to_int(v) if col in _int_cols else v
            result.append(row)
        return result


def _to_int(v) -> Optional[int]:
    return int(v) if v is not None else None
