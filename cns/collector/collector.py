"""
Kolekcjoner danych – orkiestruje harmonogram odpytywania API.

Zmiany względem v0.2:
- save_disruptions() i save_schedules() przyjmują surowy JSON z API
  (nie modele dataclass) – storage sam parsuje to co potrzebuje
- Storage protocol rozszerzony o save_schedules() i upsert_stations/carriers
"""

import logging
import time
from datetime import datetime, date
from typing import Optional, Protocol, runtime_checkable

from cns.collector.client import PKPClient, RateLimitError
from cns.collector.parser import (
    parse_carriers, parse_operations, parse_stations,
)
from cns.collector.weather_client import WeatherClient
from cns.models.records import OperationsSnapshot

logger = logging.getLogger(__name__)


@runtime_checkable
class Storage(Protocol):
    def save_snapshot(self, snapshot: OperationsSnapshot) -> None: ...
    def save_disruptions(self, raw: dict) -> None: ...
    def save_schedules(self, raw: dict) -> None: ...
    def save_raw(self, name: str, data: dict) -> None: ...


class JsonFileStorage:
    """Prosta implementacja do plików JSON – backup lub środowisko dev."""

    def __init__(self, output_dir: str = "./data"):
        import pathlib
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _write(self, path, data) -> None:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def save_snapshot(self, snapshot: OperationsSnapshot) -> None:
        ts = snapshot.fetched_at.strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"operations_{ts}.json"
        self._write(path, snapshot.raw)
        logger.info("Zapisano snapshot → %s (%d pociągów)", path, snapshot.total_trains)

    def save_disruptions(self, raw: dict) -> None:
        import json
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"disruptions_{ts}.json"
        self._write(path, raw)
        count = len(raw.get("disruptions") or [])
        logger.info("Zapisano utrudnienia → %s (%d)", path, count)

    def save_schedules(self, raw: dict) -> None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"schedules_{ts}.json"
        self._write(path, raw)
        count = len(raw.get("routes") or [])
        logger.info("Zapisano rozkład → %s (%d tras)", path, count)

    def save_raw(self, name: str, data: dict) -> None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"{name}_{ts}.json"
        self._write(path, data)
        logger.info("Zapisano %s → %s", name, path)


class DataCollector:
    def __init__(
        self,
        api_key: str,
        storage: Storage = None,
        operations_interval_min: int = 15,
        disruptions_interval_min: int = 60,
        weather_interval_min: int = 60,
        dry_run: bool = False,
    ):
        self.client = PKPClient(api_key)
        self.weather_client = WeatherClient()
        self.storage = storage or JsonFileStorage()
        self.ops_interval = operations_interval_min * 60
        self.dis_interval = disruptions_interval_min * 60
        self.weather_interval = weather_interval_min * 60
        self.dry_run = dry_run

        self._last_operations: Optional[float] = None
        self._last_disruptions: Optional[float] = None
        self._last_weather: Optional[float] = None
        self._last_schedules_date: Optional[date] = None
        self._last_operations_version: Optional[str] = None

    def run(self) -> None:
        logger.info("🚆 TorAlert DataCollector start (dry_run=%s)", self.dry_run)
        self._bootstrap()
        while True:
            try:
                self._tick()
            except RateLimitError as e:
                wait = self._rate_limit_wait(e)
                logger.warning("⛔ Rate limit! Czekam %ds... %s", wait, e)
                time.sleep(wait)
            except Exception as e:
                logger.error("Nieoczekiwany błąd w pętli: %s", e, exc_info=True)
                time.sleep(30)
            time.sleep(10)

    def _rate_limit_wait(self, e: RateLimitError) -> int:
        """Oblicz ile sekund czekać po rate limit.
        Używa nagłówka Retry-After jeśli dostępny, inaczej czeka do końca godziny."""
        if e.retry_after:
            return e.retry_after + 5  # mały bufor

        # Brak nagłówka – poczekaj do początku następnej godziny + 30s bufora
        now = datetime.now()
        seconds_to_next_hour = (60 - now.minute) * 60 - now.second + 30
        logger.warning(
            "Brak nagłówka Retry-After – czekam do kolejnej godziny (%ds)",
            seconds_to_next_hour
        )
        return seconds_to_next_hour

    def collect_once(self) -> None:
        self._bootstrap()
        self._fetch_operations(force=True)
        self._fetch_disruptions(force=True)
        self._fetch_weather()
        self._fetch_schedules_if_needed()

    def _bootstrap(self) -> None:
        """Przy starcie synchronizuj słowniki stacji i przewoźników."""
        logger.info("Synchronizuję słowniki...")
        try:
            carriers_raw = self.client.get_carriers()
            carriers = parse_carriers(carriers_raw)
            if not self.dry_run:
                # Jeśli storage to PostgresStorage – upsertuje do bazy
                if hasattr(self.storage, 'upsert_carriers'):
                    self.storage.upsert_carriers(carriers)
                else:
                    self.storage.save_raw("carriers", carriers_raw)
            logger.info("Przewoźnicy: %s", [c.code for c in carriers])

            stations_raw = self.client.get_stations(page_size=5000)
            stations = parse_stations(stations_raw)
            if not self.dry_run:
                if hasattr(self.storage, 'upsert_stations'):
                    self.storage.upsert_stations(stations)
                else:
                    self.storage.save_raw("stations", stations_raw)
            logger.info("Pobrano %d stacji", len(stations))

        except Exception as e:
            logger.error("Bootstrap nieudany: %s. Kontynuuję mimo to.", e)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._last_operations is None or (now - self._last_operations) >= self.ops_interval:
            self._fetch_operations()
            self._last_operations = now
        if self._last_disruptions is None or (now - self._last_disruptions) >= self.dis_interval:
            self._fetch_disruptions()
            self._last_disruptions = now
        if self._last_weather is None or (now - self._last_weather) >= self.weather_interval:
            self._fetch_weather()
            self._last_weather = now
        self._fetch_schedules_if_needed()

    def _fetch_operations(self, force: bool = False) -> None:
        logger.debug("Sprawdzam wersję danych operacyjnych...")
        try:
            version_data = self.client.get_data_version()
            current_version = (
                version_data.get("operationsVersion")
                or version_data.get("executionVersion")
                or version_data.get("version")
            )

            if not force and current_version and current_version == self._last_operations_version:
                logger.debug("Wersja niezmieniona (%s) – pomijam", current_version)
                return

            logger.info("Pobieram /operations (wersja: %s)...", current_version)
            fetched_at = datetime.utcnow()

            raw = self.client.get_operations(
                with_planned=True,
                full_routes=False,
                page_size=10000,
            )

            snapshot = parse_operations(raw, fetched_at, data_version=current_version)
            self._last_operations_version = current_version

            if not self.dry_run:
                self.storage.save_snapshot(snapshot)
            else:
                logger.info("[DRY RUN] Snapshot: %d pociągów, %d przystanków",
                            snapshot.total_trains, snapshot.total_stops)

            self._log_delay_summary(snapshot)

        except RateLimitError:
            raise
        except Exception as e:
            logger.error("Błąd pobierania /operations: %s", e, exc_info=True)

    def _fetch_disruptions(self, force: bool = False) -> None:
        logger.info("Pobieram /disruptions...")
        try:
            raw = self.client.get_disruptions()
            count = len(raw.get("disruptions") or [])
            if not self.dry_run:
                self.storage.save_disruptions(raw)
            else:
                logger.info("[DRY RUN] Utrudnienia: %d", count)
        except RateLimitError:
            raise
        except Exception as e:
            logger.error("Błąd pobierania /disruptions: %s", e, exc_info=True)

    def _fetch_weather(self) -> None:
        logger.info("Pobieram dane pogodowe...")
        if not hasattr(self.storage, "get_weather_stations"):
            logger.debug("Storage nie obsługuje pogody – pomijam")
            return
        try:
            stations = self.storage.get_weather_stations(limit=30)
            if not stations:
                logger.warning("Brak stacji z koordynatami – pomijam pobieranie pogody")
                return

            all_obs: list[dict] = []
            for station_id, lat, lon in stations:
                try:
                    obs = self.weather_client.get_forecast_48h(
                        str(station_id), float(lat), float(lon)
                    )
                    all_obs.extend(obs)
                except Exception as e:
                    logger.warning("Błąd pogody dla stacji %s: %s", station_id, e)

            logger.info(
                "Pobrano %d obserwacji pogodowych dla %d stacji",
                len(all_obs), len(stations),
            )
            if not self.dry_run and all_obs and hasattr(self.storage, "save_weather_observations"):
                self.storage.save_weather_observations(all_obs)

        except Exception as e:
            logger.error("Błąd pobierania pogody: %s", e, exc_info=True)

    def _fetch_schedules_if_needed(self) -> None:
        today = date.today()
        if self._last_schedules_date == today:
            return
        if datetime.now().hour < 4:
            return
        logger.info("Pobieram rozkład planowy na %s...", today)
        try:
            raw = self.client.get_schedules(date_from=today, date_to=today)
            if not self.dry_run:
                self.storage.save_schedules(raw)
            else:
                count = len(raw.get("routes") or [])
                logger.info("[DRY RUN] Rozkład: %d tras", count)
            self._last_schedules_date = today
        except Exception as e:
            logger.error("Błąd pobierania /schedules: %s", e)

    def _log_delay_summary(self, snapshot: OperationsSnapshot) -> None:
        trains = snapshot.trains
        if not trains:
            logger.info("Snapshot pusty.")
            return

        counts = snapshot.status_counts()
        active = snapshot.active_trains

        all_delays = [
            (t, s)
            for t in active
            for s in t.stops
            if s.delay_departure_minutes is not None and s.delay_departure_minutes > 0
        ]

        if all_delays:
            avg = sum(s.delay_departure_minutes for _, s in all_delays) / len(all_delays)
            max_train, max_stop = max(all_delays, key=lambda x: x[1].delay_departure_minutes)
            max_delay = max_stop.delay_departure_minutes
            max_station = max_stop.station_name or max_stop.station_id
        else:
            avg = max_delay = 0
            max_train = max_stop = None
            max_station = "-"

        logger.info(
            "📊 %s | wszystkich: %d (S:%d P:%d C:%d X:%d Q:%d) | "
            "aktywnych z opóźnieniem: %d | śr: %.1f min | max: %d min (%s)",
            snapshot.fetched_at.strftime("%H:%M:%S"),
            len(trains),
            counts.get("S", 0), counts.get("P", 0), counts.get("C", 0),
            counts.get("X", 0), counts.get("Q", 0),
            len(all_delays), avg, max_delay, max_station,
        )

        if self.client.rate_limit_hourly_remaining is not None:
            logger.info(
                "🔑 Limit API: %d/h, %d/dzień pozostało",
                self.client.rate_limit_hourly_remaining,
                self.client.rate_limit_daily_remaining or 0,
            )