# cyrk_na_szynach v2 — Strategia Danych Historycznych do ML

> Dokument strategiczny. Cel: zebrać i przygotować ≥12 miesięcy danych treningowych
> dla modelu predykcji opóźnień PKP PLK.
> Wersja: 2026-06-01 | Stan: zbieranie RT działa, brakuje danych historycznych.

---

## Problem i cel

Model XGBoost (Faza 3.2) wymaga danych z pełnego roku kalendarzowego, żeby nauczyć się
wzorców sezonowych: wakacje letnie, ferie zimowe, Boże Narodzenie, majówka, nawałnice
letnie, mrozy zimowe. Z 1–3 miesięcy RT możemy wytrenować coś działającego, ale bez
rocznego cyklu model nie uogólnia na okresy, których nie widział.

**Cel:** 365 dni pełnych danych w `mv_training_features` — gdzie „pełnych" znaczy:
`station_stops` z opóźnieniami + dopasowana pogoda + typ dnia w kalendarzu.

**Obecny stan (szacunkowy):**

| Źródło danych | Dostępny zakres | Pokrycie |
|---------------|-----------------|----------|
| `station_stops` (RT) | od uruchomienia collectora | ~2-5 mies. |
| `weather_observations` | od uruchomienia WeatherClient | ~2-5 mies. |
| `calendar_events` | 2024–2030 (CalendarService) | ✅ pełne |
| **Brakuje:** `station_stops` historyczne | 2025-06-01 → start collectora | ❌ |
| **Brakuje:** `weather_observations` historyczne | j.w. | ❌ |

---

## Trzy ścieżki uzupełnienia danych

```
ŚCIEŻKA A — Backfill pogody (Open-Meteo Archive)
  → szybka, bezpłatna, 365 dni w jednej sesji, HIGH ROI
  → uzupełnia weather_observations za cały brakujący okres

ŚCIEŻKA B — Scraping historycznych opóźnień (kolejopedia.pl / PKP)
  → pracochłonna, ale daje realny station_stops z przeszłości
  → wymaga mapowania zewnętrznych danych na nasz schemat
  → najwyższy priorytet, bo bez tego brakuje głównych featur

ŚCIEŻKA C — Ciągłe zbieranie RT (działa już od Fazy 1)
  → co 15 min, bez dodatkowej pracy
  → za 6–9 mies. od dziś mamy wystarczający zbiór treningowy
  → NIE czekamy na tę ścieżkę — realizujemy A+B równolegle
```

---

## CZĘŚĆ A — Plan implementacji

### FAZA H.0 — Audyt bieżących danych

**Status:** ❌ Do zrobienia (PIERWSZE ZADANIE — zanim cokolwiek backfillujemy)
**Pliki wyjściowe:** `scripts/data_audit.sql`, wydruk z kluczowymi metrykami

```
PROMPT — wklej do sesji `claude`:

Napisz skrypt audytu danych dla cyrk_na_szynach. Cel: zrozumieć dokładnie
co mamy przed backfillem.

ZADANIE 1 — scripts/data_audit.sql:

Zapytania diagnostyczne (każde z komentarzem wyjaśniającym co mierzy):

-- 1. Zakres dat i pokrycie station_stops
SELECT
  MIN(planned_departure::date) AS first_date,
  MAX(planned_departure::date) AS last_date,
  COUNT(DISTINCT planned_departure::date) AS days_with_data,
  (MAX(planned_departure::date) - MIN(planned_departure::date) + 1) AS total_days,
  ROUND(COUNT(DISTINCT planned_departure::date)::numeric /
    NULLIF(MAX(planned_departure::date) - MIN(planned_departure::date) + 1, 0) * 100, 1)
    AS coverage_pct
FROM station_stops
WHERE planned_departure IS NOT NULL;

-- 2. Dni z lukami (brak snapshotów powyżej 2h)
WITH daily AS (
  SELECT fetched_at::date AS day, COUNT(*) AS snapshots
  FROM operations_snapshots GROUP BY 1
)
SELECT day, snapshots,
  CASE WHEN snapshots < 48 THEN 'PARTIAL' ELSE 'FULL' END AS status
FROM daily
WHERE snapshots < 96
ORDER BY day;

-- 3. Pokrycie pogodą (% station_stops z dopasowaną obserwacją weather)
SELECT
  COUNT(*) AS total_stops,
  COUNT(wo.id) AS stops_with_weather,
  ROUND(COUNT(wo.id)::numeric / COUNT(*) * 100, 1) AS weather_coverage_pct
FROM station_stops ss
JOIN train_operations to_ ON ss.train_op_id = to_.id
JOIN operations_snapshots snap ON to_.snapshot_id = snap.id
LEFT JOIN LATERAL (
  SELECT id FROM weather_observations wo2
  WHERE wo2.station_id = ss.station_id::TEXT
    AND wo2.observed_at <= ss.planned_departure
    AND wo2.is_forecast = FALSE
  ORDER BY wo2.observed_at DESC LIMIT 1
) wo ON TRUE
WHERE ss.planned_departure > NOW() - INTERVAL '30 days';

-- 4. Rozkład opóźnień (percentyle) — jakość danych
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE delay_departure_min IS NOT NULL) AS with_delay,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delay_departure_min) AS p50,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY delay_departure_min) AS p75,
  PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY delay_departure_min) AS p90,
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY delay_departure_min) AS p99,
  MAX(delay_departure_min) AS max_delay
FROM station_stops
WHERE delay_departure_min IS NOT NULL;

-- 5. Top stacje pod kątem liczby rekordów (ważne: stacje bez station_id = NULL)
SELECT
  COALESCE(s.name, 'UNKNOWN (' || ss.station_id::text || ')') AS station,
  COUNT(*) AS records,
  ROUND(AVG(ss.delay_departure_min), 1) AS avg_delay,
  COUNT(*) FILTER (WHERE ss.station_id IS NULL) AS null_station_id
FROM station_stops ss
LEFT JOIN stations s ON ss.station_id = s.station_id
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

-- 6. Podsumowanie mv_training_features (ile wierszy, zakres dat)
SELECT
  COUNT(*) AS feature_rows,
  MIN(operating_date) AS first_date,
  MAX(operating_date) AS last_date,
  COUNT(*) FILTER (WHERE temperature_c IS NOT NULL) AS with_weather,
  COUNT(*) FILTER (WHERE day_type IS NOT NULL) AS with_calendar,
  COUNT(*) FILTER (WHERE prev_stop_delay_min IS NOT NULL) AS with_prev_delay
FROM mv_training_features;

ZADANIE 2 — scripts/run_audit.sh:
#!/bin/bash
# Uruchom: bash scripts/run_audit.sh
docker exec -i cyrk-na-szynach-db \
  psql -U cyrk_na_szynach -d cyrk_na_szynach \
  < scripts/data_audit.sql

ZADANIE 3 — cns/ml/data_quality.py:
# Moduł oceny jakości danych do trenowania
# Uruchomienie: poetry run python -m cns.ml.data_quality

def audit_training_data(db_url: str) -> dict:
    """
    Zwraca słownik z metrykami jakości:
    {
      "date_range": {"from": date, "to": date, "days": int, "coverage_pct": float},
      "total_rows": int,
      "weather_coverage_pct": float,
      "calendar_coverage_pct": float,
      "prev_delay_coverage_pct": float,
      "delay_distribution": {"p50": float, "p75": float, "p90": float},
      "recommendation": str  # np. "READY" / "NEEDS_WEATHER_BACKFILL" / "NOT_ENOUGH_DATA"
    }
    """
    ...

def print_audit_report(metrics: dict) -> None:
    """Wydrukuj czytelny raport do konsoli."""
    ...

if __name__ == "__main__":
    import os
    metrics = audit_training_data(os.environ["DATABASE_URL"])
    print_audit_report(metrics)

# Progi dla rekomendacji:
# READY:                  ≥180 dni, weather ≥70%, calendar ≥95%, prev_delay ≥60%
# NEEDS_WEATHER_BACKFILL: weather < 50%  → uruchom Fazę H.1
# NOT_ENOUGH_DATA:        < 90 dni       → czekaj LUB uruchom Fazę H.3
# PARTIAL:                pozostałe przypadki

Dodaj komendę do __main__.py:
  poetry run cns data-audit   → wywołuje data_quality.audit_training_data()

Po implementacji wykonaj OBOWIĄZKOWO:
1. Uruchom audit i zanotuj wyniki w DEVELOPMENT.md (sekcja "Stan danych treningowych")
2. Wpisz konkretne liczby: dni, pokrycie pogody, liczba wierszy w mv_training_features
3. Na podstawie wyników zdecyduj, które fazy H.1–H.3 są najpilniejsze
```

---

### FAZA H.1 — Backfill danych pogodowych

**Status:** ❌ Do zrobienia
**Priorytet:** WYSOKI (szybki i darmowy — zrób JAKO PIERWSZE po audycie)
**Pliki wyjściowe:** `cns/collector/weather_backfill.py`, rozszerzenie `__main__.py`

**Dlaczego to jest łatwe:** Open-Meteo udostępnia bezpłatne archiwum historyczne
(`archive-api.open-meteo.com/v1/archive`) bez klucza API, z godzinowymi danymi
od 1940 r. Dla 30 stacji × 365 dni potrzeba ~30 zapytań HTTP — do zrobienia w minutę.

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj backfill pogody historycznej dla cyrk_na_szynach.

Kontekst: istnieje weather_client.py (Faza 1.1) pobierający prognozy z
api.open-meteo.com. Teraz potrzebujemy osobnego modułu do pobierania
danych archiwalnych z archive-api.open-meteo.com/v1/archive.

ZADANIE 1 — cns/collector/weather_backfill.py:

Endpoint archiwum Open-Meteo:
  GET https://archive-api.open-meteo.com/v1/archive
  Params: latitude, longitude, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD),
          hourly=temperature_2m,precipitation,wind_speed_10m,
                 snowfall,visibility,cloud_cover,weather_code
  Brak klucza API. Rate limit: ~10k/dzień (w praktyce brak dla rozsądnego użycia).
  Odpowiedź: {"hourly": {"time": [...], "temperature_2m": [...], ...}}

class WeatherBackfill:
    """Pobiera i zapisuje historyczne dane pogodowe z Open-Meteo Archive API."""

    BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
    HOURLY_PARAMS = "temperature_2m,precipitation,wind_speed_10m,snowfall,visibility,cloud_cover,weather_code"

    def __init__(self, storage: PostgresStorage):
        self._storage = storage

    def backfill_station(
        self,
        station_id: str,
        lat: float,
        lon: float,
        date_from: date,
        date_to: date,
    ) -> int:
        """
        Pobiera dane godzinowe dla stacji za cały zakres dat.
        Zwraca liczbę zapisanych rekordów.
        Pomija rekordy które już istnieją w bazie (ON CONFLICT DO NOTHING).
        """
        ...

    def backfill_all_stations(
        self,
        date_from: date,
        date_to: date,
        batch_delay_sec: float = 1.0,
    ) -> dict[str, int]:
        """
        Pobiera dane dla wszystkich stacji z DB (SELECT station_id, lat, lon FROM stations
        WHERE latitude IS NOT NULL LIMIT 30).
        Loguje postęp: "Stacja X/30: {name} → {n} rekordów"
        batch_delay_sec: opóźnienie między stacjami (grzeczność wobec API)
        Zwraca: {"station_id": records_inserted, ...}
        """
        ...

Parsowanie odpowiedzi archiwum (identyczne pola co weather_client.py):
- time[] → observed_at (TIMESTAMPTZ, UTC)
- is_forecast = FALSE (to historyczne dane, nie prognozy)
- Użyj ON CONFLICT (station_id, observed_at, is_forecast) DO NOTHING
  → bezpieczny re-run, pomija duplikaty

ZADANIE 2 — rozszerzenie cns/__main__.py:
  Nowa komenda CLI: poetry run cns backfill-weather

  @click.command("backfill-weather")
  @click.option("--date-from", required=True, help="Format: YYYY-MM-DD")
  @click.option("--date-to", required=True, help="Format: YYYY-MM-DD")
  @click.option("--station-id", default=None, help="Pojedyncza stacja (opcjonalne)")
  @click.option("--delay", default=1.0, help="Opóźnienie między stacjami [s]")
  def backfill_weather_cmd(date_from, date_to, station_id, delay):
      """Backfill historycznych danych pogodowych z Open-Meteo Archive."""
      ...

  Wywołanie:
    poetry run cns backfill-weather \
      --date-from 2025-06-01 \
      --date-to 2026-05-31

ZADANIE 3 — test cns/tests/test_weather_backfill.py:
  - Mock requests.get → przykładowa odpowiedź archiwum (fixture z 48 godzinami danych)
  - Sprawdź że parsuje time[] + pola wartości → lista dict
  - Sprawdź że is_forecast=FALSE dla wszystkich rekordów
  - Sprawdź że ON CONFLICT nie rzuca wyjątku przy re-run
  - Sprawdź że batch_delay_sec jest respektowany (mock time.sleep)

UWAGA KRYTYCZNA: Archive API zwraca visibility w metrach (identycznie jak forecast).
Pola są identyczne jak w get_forecast_48h() — możesz użyć tego samego kodu parsowania.

Szacowany czas wykonania backfillu za 365 dni × 30 stacji:
  ~30 zapytań × ~1s opóźnienie = ~30-60 sekund. Loguj postęp.

Po implementacji wykonaj OBOWIĄZKOWO:
1. Uruchom backfill za cały brakujący okres (wynik z Fazy H.0)
2. Uruchom data_audit ponownie — sprawdź czy weather_coverage_pct wzrósł
3. Dodaj sekcję "Weather Backfill" do DEVELOPMENT.md
4. Dodaj komendę backfill-weather do CLAUDE.md → sekcja Komendy
```

---

### FAZA H.2 — Uzupełnienie kalendarza (sprawdzenie)

**Status:** ❌ Weryfikacja (CalendarService istnieje, ale sprawdź pokrycie)
**Priorytet:** NISKI (prawdopodobnie już OK — zrób tylko jeśli audyt wykazał lukę)

```
PROMPT — wklej do sesji `claude`:

Sprawdź i uzupełnij dane kalendarzowe w cyrk_na_szynach.

ZADANIE 1 — Weryfikacja pokrycia:
Uruchom w psql:
  SELECT MIN(event_date), MAX(event_date), COUNT(*) FROM calendar_events;
  -- Oczekiwane: 2024-01-01 do 2030-12-31

ZADANIE 2 — Jeśli luki: wywołaj ręcznie przez skrypt:
  poetry run python -c "
  from cns.collector.calendar_service import CalendarService
  from cns.storage.postgres import PostgresStorage
  import os
  cal = CalendarService()
  storage = PostgresStorage(os.environ['DATABASE_URL'])
  events = cal.generate_events(2024, 2030)
  storage.save_calendar_events(events)
  print(f'Zapisano {len(events)} eventów')
  "

ZADANIE 3 — Dodaj do skryptu data_audit.py sprawdzenie:
  -- Czy każdy dzień z station_stops ma wpis w calendar_events?
  SELECT COUNT(DISTINCT ss.planned_departure::date) AS days_without_calendar
  FROM station_stops ss
  LEFT JOIN calendar_events ce ON ce.event_date = ss.planned_departure::date AND ce.zone IS NULL
  WHERE ce.id IS NULL AND ss.planned_departure IS NOT NULL;
  -- Oczekiwane: 0
```

---

### FAZA H.3 — Pozyskanie historycznych danych opóźnień

**Status:** ❌ Do zrobienia (NAJTRUDNIEJSZA i NAJWAŻNIEJSZA faza)
**Priorytet:** KRYTYCZNY
**Pliki wyjściowe:** `cns/collector/historical_scraper.py`, `migrations/015_historical_data.sql`

#### Analiza dostępnych źródeł

| Źródło | Typ danych | Zakres historyczny | Trudność integracji | Legalność |
|--------|-----------|-------------------|--------------------|-----------| 
| **kolejopedia.pl** | per pociąg, per przystanek, czas plan./rzecz. | kilka lat | ŚREDNIA (scraping HTML) | szara strefa |
| **PKP PLK RT API** (obecne) | j.w. | TYLKO RT | GOTOWE | ✅ legalne |
| **portalpasazera.pl** | czas odjazdu/przyjazdu | ograniczony | ŚREDNIA | szara strefa |
| **UTK raporty** | agregaty per stacja/linia | kilka lat | WYSOKA (PDF) | ✅ legalne |
| **GTFS archiwum** | rozkład planowy (bez opóźnień) | — | NISKA | ✅ legalne |
| **Synteza statystyczna** | pseudo-historyczne | dowolny | NISKA | ✅ legalne |

**Rekomendowana strategia:**
1. **kolejopedia.pl** — główne źródło historycznych danych per pociąg
2. **Synteza statystyczna** — bootstrap modelu gdy brakuje danych za konkretne okresy

---

#### Zadanie H.3.1 — Scraper kolejopedia.pl

**Cel:** Pobrać historyczne czasy odjazdów/przyjazdów pociągów dla uzupełnienia `station_stops`.

**WAŻNE:** Scraping respektuje robots.txt i używa opóźnień. Dane te są publicznie
dostępne i służą wyłącznie celom analitycznym niekomercyjnym.

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj scraper historycznych danych opóźnień dla cyrk_na_szynach.

KONTEKST TECHNICZNY kolejopedia.pl:
- Serwis śledzi pociągi PKP PLK w czasie rzeczywistym i archiwizuje historię
- URL per pociąg per dzień: https://kolejopedia.pl/pociag/{NUMER_POCIAGU}/{DATA}/
  gdzie DATA = YYYY-MM-DD, NUMER_POCIAGU = national_number z tabeli schedules
- Strona zwraca tabelę HTML: stacja | planowy przyjazd | rzeczywisty przyjazd |
  planowy odjazd | rzeczywisty odjazd | opóźnienie
- Sprawdź strukture przez: curl -s "https://kolejopedia.pl/pociag/IC12345/2026-01-15/"

ZADANIE 1 — migrations/015_historical_data.sql:

-- Śledzenie importów historycznych (idempotentność)
CREATE TABLE historical_import_log (
  id            SERIAL PRIMARY KEY,
  source        VARCHAR(50) NOT NULL,   -- 'kolejopedia', 'manual', 'synthetic'
  train_number  VARCHAR(20),            -- numer pociągu (national_number)
  operating_date DATE NOT NULL,
  records_fetched  INTEGER DEFAULT 0,
  records_inserted INTEGER DEFAULT 0,
  status        VARCHAR(20) NOT NULL    -- 'OK', 'NOT_FOUND', 'ERROR', 'SKIPPED'
    CHECK (status IN ('OK', 'NOT_FOUND', 'ERROR', 'SKIPPED')),
  fetched_at    TIMESTAMPTZ DEFAULT NOW(),
  error_detail  TEXT
);

CREATE INDEX ON historical_import_log (source, operating_date);
CREATE INDEX ON historical_import_log (status, fetched_at);

-- Oznaczenie źródła danych w station_stops (nowa kolumna)
-- Wykonaj TYLKO jeśli kolumna nie istnieje:
ALTER TABLE station_stops
  ADD COLUMN IF NOT EXISTS data_source VARCHAR(20) DEFAULT 'rt_api';
  -- Wartości: 'rt_api' (live z PKP API), 'kolejopedia', 'synthetic'

COMMENT ON COLUMN station_stops.data_source IS
  'Źródło danych: rt_api=kolektor RT, kolejopedia=scraping historyczny, synthetic=dane syntetyczne';

CREATE INDEX IF NOT EXISTS idx_station_stops_source
  ON station_stops (data_source, planned_departure::date);

ZADANIE 2 — cns/collector/historical_scraper.py:

import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import date
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class ScrapedStop:
    """Jeden przystanek z historii pociągu."""
    station_name: str
    planned_arrival: Optional[str]    # "HH:MM" lub None
    actual_arrival: Optional[str]
    planned_departure: Optional[str]
    actual_departure: Optional[str]
    delay_min: Optional[int]          # podane przez kolejopedia jako gotowa wartość

class KolejopediaScraper:
    BASE_URL = "https://kolejopedia.pl"
    REQUEST_DELAY_SEC = 2.0    # grzeczność: min 2s między zapytaniami

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "cyrk_na_szynach/1.0 research project (cezary.czernecki@gmail.com)"
        })

    def get_train_stops(
        self,
        train_number: str,
        operating_date: date,
    ) -> Optional[list[ScrapedStop]]:
        """
        Pobiera historię przystanków dla pociągu w podanym dniu.
        Zwraca None jeśli pociąg nie znaleziony (404 lub pusta tabela).
        Rzuca requests.RequestException przy błędach sieciowych.
        """
        url = f"{self.BASE_URL}/pociag/{train_number}/{operating_date.isoformat()}/"
        time.sleep(self.REQUEST_DELAY_SEC)
        response = self._session.get(url, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._parse_stops_table(response.text)

    def _parse_stops_table(self, html: str) -> list[ScrapedStop]:
        """
        Parsuje tabelę HTML z przystankami.
        UWAGA: Dostosuj selektory CSS po zbadaniu rzeczywistej struktury HTML.
        Użyj BeautifulSoup z parserem 'html.parser' (bez zewnętrznych zależności).
        """
        soup = BeautifulSoup(html, "html.parser")
        # TODO: zbadaj strukturę przez: curl URL | python -m html.parser
        # Szukaj tabeli z klasą zawierającą "stops" lub "stations"
        stops = []
        # ... implementacja po zbadaniu HTML ...
        return stops

ZADANIE 3 — cns/collector/historical_importer.py:

class HistoricalImporter:
    """
    Orkiestrator importu: pobiera listę pociągów z naszej bazy,
    scraping kolejopedia, mapowanie na station_stops, zapis.
    """

    def __init__(self, storage: PostgresStorage, scraper: KolejopediaScraper):
        ...

    def import_date_range(
        self,
        date_from: date,
        date_to: date,
        limit_per_day: int = 50,     # max pociągów per dzień (kontrola tempa)
        skip_existing: bool = True,  # pomiń już zaimportowane (historical_import_log)
    ) -> dict:
        """
        Dla każdego dnia w zakresie:
          1. Pobierz train_number z (schedules JOIN schedules.national_number)
             dla operating_date = dzień
          2. Dla każdego pociągu: scraper.get_train_stops()
          3. Zmapuj ScrapedStop → station_stops format
          4. Wstaw do station_stops z data_source='kolejopedia'
          5. Zapisz log w historical_import_log
        Zwraca: {"days_processed": int, "trains_ok": int, "trains_not_found": int,
                 "errors": int, "records_inserted": int}
        """
        ...

    def _map_scraped_to_db_rows(
        self,
        scraped: list[ScrapedStop],
        train_op_id: int,
        operating_date: date,
    ) -> list[dict]:
        """
        Mapowanie ScrapedStop → format kompatybilny z station_stops.
        KRYTYCZNE: opóźnienia liczymy sami (actual - planned) tak jak w parserze RT.
        Jeśli kolejopedia podaje gotowe opóźnienie — użyj jako weryfikacja, nie jako źródło.
        Stacje dopasowujemy po nazwie: LOWER(TRIM(station_name)) → stations.name.
        Niezdopasowane stacje: station_id = NULL (tak samo jak w RT).
        """
        ...

ZADANIE 4 — komenda CLI:
  poetry run cns import-historical \
    --date-from 2025-06-01 \
    --date-to 2026-01-01 \
    --limit-per-day 30

  Dodaj do __main__.py z opcjami: --date-from, --date-to, --limit-per-day, --dry-run

ZADANIE 5 — cns/tests/test_historical_scraper.py:
  - Fixture: mockowa odpowiedź HTML z tabelą przystanków
  - Test parsowania: 5 przystanków, czas plan/rzecz, opóźnienie
  - Test NOT_FOUND: 404 → None
  - Test idempotentności: drugi import tej samej daty → SKIPPED (skip_existing=True)
  - Test mapowania nazw stacji: "Warszawa Centralna" → station_id 33506

Nowa zależność do pyproject.toml:
  beautifulsoup4 = ">=4.12"

UWAGA PRZED IMPLEMENTACJĄ: Zbadaj faktyczną strukturę HTML kolejopedia.pl:
  curl -s "https://kolejopedia.pl/pociag/{NUMER}/{DATA}/" > /tmp/kolejopedia_sample.html
  # Następnie przeanalizuj tabele: python -c "from bs4 import BeautifulSoup; ..."
  # I zaktualizuj _parse_stops_table() pod rzeczywistą strukturę.

Po implementacji wykonaj OBOWIĄZKOWO:
1. Uruchom z --dry-run na 1 dniu — sprawdź czy parsowanie działa
2. Uruchom pełny import za 30 dni testowo — oceń % NOT_FOUND vs OK
3. Dodaj sekcję "Historical Importer" do DEVELOPMENT.md
4. Wpisz uzysk: ile % pociągów znaleziono na kolejopedia (oczekiwane: 70-90%)
```

---

#### Zadanie H.3.2 — Synteza danych do bootstrap modelu

**Status:** ❌ Opcjonalne (uruchom TYLKO jeśli H.3.1 dał <60% pokrycia historycznego)
**Cel:** Wygenerować statystycznie spójne dane treningowe za okresy bez scraped data.

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj generator syntetycznych danych treningowych dla cyrk_na_szynach.

KONTEKST: Mamy dane RT za ostatnie 2-5 miesięcy. Chcemy zsyntetyzować dane za
brakujące okresy, opierając się na znanych wzorcach sezonowych PKP PLK.

FILOZOFIA: Dane syntetyczne NIE zastępują prawdziwych danych — uzupełniają luki
w specyficznych wzorcach (np. brak danych ze śnieżnej zimy, gdy kolektor startował w marcu).
Oznacz je wyraźnie data_source='synthetic' — model może się z nich uczyć wzorców,
ale walidację robimy WYŁĄCZNIE na danych RT lub kolejopedia.

cns/ml/synthetic_data.py:

PARAMETRY ROZKŁADU (szacowane z PKP raportów rocznych UTK i wiedzy domenowej):

SEASONAL_MULTIPLIERS = {
    1: 1.4,  # styczeń: mrozy, śnieg → +40% opóźnień
    2: 1.3,  # luty: podobnie
    3: 1.1,  # marzec: przejściowy
    4: 1.0,  # kwiecień: baseline
    5: 1.05, # maj: majówka → lekko więcej
    6: 1.0,
    7: 1.1,  # lipiec: wakacje → tłok na trasach
    8: 1.1,
    9: 1.0,
    10: 1.05,
    11: 1.2,  # listopad: pierwsze mrozy, mgły
    12: 1.5,  # grudzień: Boże Narodzenie + zima
}

HOUR_MULTIPLIERS = {
    # peak godziny: poranny i popołudniowy
    6: 1.2, 7: 1.5, 8: 1.4,
    15: 1.3, 16: 1.5, 17: 1.4, 18: 1.3,
}

def generate_synthetic_station_stops(
    db_url: str,
    date_from: date,
    date_to: date,
    sample_fraction: float = 0.3,  # 30% wielkości prawdziwych danych (nie zaburzaj rozkładu)
) -> int:
    """
    Generuje syntetyczne wiersze station_stops.
    Algorytm:
      1. Pobierz rozkłady rzeczywistych opóźnień z istniejących danych
         (per station_id, hour_bucket, month)
      2. Dla brakujących dni: losuj z tych rozkładów z korekcją sezonową
      3. Wstaw z data_source='synthetic'
    Zwraca liczbę wstawionych rekordów.
    """
    ...

WAŻNE dla train/test split:
  Dane syntetyczne NIGDY nie trafiają do zbioru walidacyjnego ani testowego.
  W train_xgb.py: filtruj WHERE data_source != 'synthetic' dla val i test.

Po implementacji wykonaj OBOWIĄZKOWO:
1. Wygeneruj dane za 1 miesiąc testowo
2. Porównaj rozkład opóźnień: syntetyczne vs RT (histogram)
3. Opisz ograniczenia podejścia w DEVELOPMENT.md
```

---

### FAZA H.4 — Pipeline jakości danych i monitoring

**Status:** ❌ Do zrobienia (po ukończeniu H.1 i H.3)
**Pliki wyjściowe:** rozszerzenie `data_quality.py`, nowe widoki SQL

```
PROMPT — wklej do sesji `claude`:

Dodaj monitoring jakości danych treningowych do cyrk_na_szynach.

Cel: automatycznie wykrywaj problemy z danymi historycznymi, które obniżą
jakość modelu ML.

ZADANIE 1 — migrations/016_data_quality.sql:

CREATE MATERIALIZED VIEW mv_data_quality_daily AS
SELECT
  ss.planned_departure::date AS day,
  COUNT(*) AS total_stops,
  COUNT(*) FILTER (WHERE ss.delay_departure_min IS NOT NULL) AS stops_with_delay,
  COUNT(*) FILTER (WHERE wo.id IS NOT NULL) AS stops_with_weather,
  COUNT(*) FILTER (WHERE ce.id IS NOT NULL) AS stops_with_calendar,
  COUNT(*) FILTER (WHERE ss.data_source = 'rt_api') AS from_rt,
  COUNT(*) FILTER (WHERE ss.data_source = 'kolejopedia') AS from_scraping,
  COUNT(*) FILTER (WHERE ss.data_source = 'synthetic') AS from_synthetic,
  ROUND(
    COUNT(*) FILTER (WHERE wo.id IS NOT NULL)::numeric / NULLIF(COUNT(*), 0) * 100,
  1) AS weather_pct,
  COUNT(DISTINCT snap.id) AS snapshots_count  -- NULL dla historycznych
FROM station_stops ss
JOIN train_operations to_ ON ss.train_op_id = to_.id
JOIN operations_snapshots snap ON to_.snapshot_id = snap.id
LEFT JOIN LATERAL (
  SELECT id FROM weather_observations wo2
  WHERE wo2.station_id = ss.station_id::TEXT
    AND wo2.observed_at <= ss.planned_departure
    AND wo2.is_forecast = FALSE
  ORDER BY wo2.observed_at DESC LIMIT 1
) wo ON TRUE
LEFT JOIN calendar_events ce
  ON ce.event_date = ss.planned_departure::date AND ce.zone IS NULL
GROUP BY 1
ORDER BY 1;

CREATE UNIQUE INDEX ON mv_data_quality_daily (day);

-- Odświeżaj raz dziennie (dodaj do DataCollector lub crona)

ZADANIE 2 — cns/ml/data_quality.py — rozszerz o:

def get_training_readiness(db_url: str) -> TrainingReadiness:
    """
    Analiza gotowości danych do trenowania modelu.
    Sprawdza mv_data_quality_daily za ostatnie 180 dni.
    """

@dataclass
class TrainingReadiness:
    total_days: int
    days_with_good_data: int     # weather_pct >= 50% AND stops_with_delay >= 100
    seasonal_coverage: dict      # {"WINTER": bool, "SPRING": bool, ...}
    data_sources: dict           # {"rt_api": days, "kolejopedia": days, "synthetic": days}
    recommendation: str          # "READY_FOR_TRAINING" / "MORE_DATA_NEEDED" / "RETRAIN_RECOMMENDED"
    blockers: list[str]          # lista konkretnych problemów

ZADANIE 3 — endpoint FastAPI:

GET /health/data
Response:
{
  "total_days_available": 245,
  "days_with_good_data": 198,
  "seasonal_coverage": {
    "WINTER_MONTHS": true,
    "SUMMER_MONTHS": false,
    "HOLIDAYS": true,
    "LONG_WEEKENDS": true
  },
  "data_sources": {"rt_api": 150, "kolejopedia": 95, "synthetic": 0},
  "last_refresh": "2026-06-01T06:00:00Z",
  "recommendation": "MORE_DATA_NEEDED",
  "blockers": ["Brak danych letnich (lipiec-sierpień) — kolektor startował we wrześniu"]
}

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj /health/data do tabeli endpointów w DEVELOPMENT.md
2. Dodaj widok mv_data_quality_daily do schematu bazy
3. Podłącz odświeżanie widoku do harmonogramu DataCollector (raz dziennie ok. 04:00)
```

---

### FAZA H.5 — Strategia trenowania na danych historycznych

**Status:** ❌ Do zrobienia (po zebraniu ≥180 dni danych)
**Pliki wyjściowe:** aktualizacja `train_xgb.py`, `train_baseline.py`

```
PROMPT — wklej do sesji `claude`:

Zaktualizuj pipeline treningowy cyrk_na_szynach pod dane historyczne (mieszane źródła).

KONTEKST: Mamy teraz dane z trzech źródeł:
  - rt_api: dane RT (najwyższa jakość, nasze złoto)
  - kolejopedia: scraped (dobra jakość, ale możliwe braki lub błędy parsowania)
  - synthetic: dane syntetyczne (tylko do trenowania, nie do walidacji)

ZADANIE 1 — Zaktualizuj train_xgb.py i train_baseline.py:

Strategia podziału danych (TIME-BASED, nie losowy):

def split_train_val_test(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Podział chronologiczny — jedyna poprawna strategia dla szeregów czasowych.

    ZASADA: walidacja i test = wyłącznie rt_api (prawdziwe dane, nie synthetical/scraped).

    Schemat podziału dla 365 dni danych:
      TRAIN (80%): dni 0–292, wszystkie źródła (rt_api + kolejopedia + synthetic)
      VAL   (10%): dni 293–328, TYLKO rt_api + kolejopedia (bez synthetic)
      TEST  (10%): dni 329–365, TYLKO rt_api (złoto — nigdy nie zaglądamy przed finalną ewaluacją)

    Dlaczego chronologicznie:
      - Zapobiega data leakage (przyszłość nie uczy przeszłości)
      - Waliduje generalizację na niewidoczne okresy
      - Realistyczny scenariusz produkcyjny (model zawsze przewiduje "przyszłość")
    """
    cutoff_val = df["operating_date"].quantile(0.80)
    cutoff_test = df["operating_date"].quantile(0.90)

    train = df[df["operating_date"] < cutoff_val]
    val   = df[
        (df["operating_date"] >= cutoff_val) &
        (df["operating_date"] < cutoff_test) &
        (df["data_source"].isin(["rt_api", "kolejopedia"]))
    ]
    test  = df[
        (df["operating_date"] >= cutoff_test) &
        (df["data_source"] == "rt_api")
    ]
    return train, val, test

ZADANIE 2 — Feature importance dla danych historycznych:

Sprawdź czy dane historyczne zmieniają ranking cech:
  - Dla danych TYLKO rt_api (ostatnie 3 mies.): uruchom model A
  - Dla pełnych danych (rt_api + kolejopedia, 12 mies.): uruchom model B
  - Porównaj feature importance i MAE

Oczekiwany efekt: model B powinien być lepszy zimą/latem bo widział te wzorce.

ZADANIE 3 — Sezonowa walidacja krzyżowa (opcjonalna, ale zalecana):

Po zebraniu ≥365 dni:
def seasonal_cv(df: pd.DataFrame, n_splits: int = 4) -> list[dict]:
    """
    Cross-walidacja czasowa (TimeSeriesSplit z 4 foldami).
    Raportuj MAE osobno dla: zima (XII-II), wiosna (III-V), lato (VI-VIII), jesień (IX-XI).
    """

ZADANIE 4 — Dokumentacja wyników:
Po każdym trenowaniu zapisz do models/training_report_{DATE}.json:
{
  "trained_at": "2026-06-01T12:00:00",
  "data_sources": {"rt_api": 45000, "kolejopedia": 120000, "synthetic": 0},
  "date_range": {"from": "2025-06-01", "to": "2026-05-31"},
  "train_val_test_sizes": [165000, 20000, 15000],
  "metrics": {
    "baseline_mae": 5.2,
    "xgb_val_mae": 3.8,
    "xgb_test_mae": 4.1,
    "improvement_over_baseline_pct": 27.0
  },
  "feature_importance_top10": [...],
  "seasonal_mae": {"winter": 5.1, "spring": 3.2, "summer": 3.5, "autumn": 3.9}
}

Po implementacji wykonaj OBOWIĄZKOWO:
1. Zaktualizuj sekcję "ML — metryki referencyjne" w DEVELOPMENT.md
2. Wpisz konkretne metryki z trenowania na pełnych danych historycznych
3. Opisz jak sezonowość wpłynęła na model (porównaj model A vs B)
```

---

### FAZA H.6 — Zarządzanie danymi długoterminowo

**Status:** ❌ Do zrobienia (gdy baza osiągnie >6 mies. danych)
**Priorytet:** NISKI — nie blokuje trenowania, ale ważne dla wydajności produkcyjnej

```
PROMPT — wklej do sesji `claude`:

Zoptymalizuj przechowywanie danych historycznych w cyrk_na_szynach.

KONTEKST WZROSTU:
  station_stops: ~650k rekordów/dzień × 365 = ~237M rekordów/rok (~30–50 GB/rok)
  operations_snapshots: ~96/dzień × 365 = ~35k rekordów/rok (mały)
  train_operations: ~38k/dzień × 365 = ~14M rekordów/rok

  Po 12 miesiącach station_stops będzie dominować. Bez optymalizacji zapytania
  na mv_training_features zaczną zwalniać.

ZADANIE 1 — Partycjonowanie station_stops (range by miesiąc):

migrations/017_partition_station_stops.sql:

-- UWAGA: to jest nieodwracalna migracja na dużej tabeli.
-- PRZED WYKONANIEM: pg_dump -t station_stops > backup.sql
-- Wykonaj w maintenance window (kolektor zatrzymany)

-- Krok 1: Zmień nazwę starej tabeli
ALTER TABLE station_stops RENAME TO station_stops_old;

-- Krok 2: Utwórz nową partycjonowaną tabelę (taka sama struktura)
CREATE TABLE station_stops (
    LIKE station_stops_old INCLUDING ALL
) PARTITION BY RANGE (planned_departure);

-- Krok 3: Utwórz partycje per kwartał (nie per miesiąc — mniej zarządzania)
CREATE TABLE station_stops_2025_q3 PARTITION OF station_stops
    FOR VALUES FROM ('2025-07-01') TO ('2025-10-01');
CREATE TABLE station_stops_2025_q4 PARTITION OF station_stops
    FOR VALUES FROM ('2025-10-01') TO ('2026-01-01');
CREATE TABLE station_stops_2026_q1 PARTITION OF station_stops
    FOR VALUES FROM ('2026-01-01') TO ('2026-04-01');
-- ... itd.
CREATE TABLE station_stops_default PARTITION OF station_stops DEFAULT;

-- Krok 4: Skopiuj dane
INSERT INTO station_stops SELECT * FROM station_stops_old;

-- Krok 5: DROP stara tabela (dopiero po weryfikacji!)
-- DROP TABLE station_stops_old;

ZADANIE 2 — Archiwizacja snapshotów:

-- Po 90 dniach: snapshoty niepotrzebne do trenowania (mamy station_stops)
-- Możemy skasować surowe snapshoty, zachowując tylko station_stops

-- Sprawdź: ile miejsca zajmują snapshoty?
SELECT pg_size_pretty(pg_table_size('operations_snapshots')) AS snapshots_size,
       pg_size_pretty(pg_table_size('station_stops')) AS stops_size;

-- Jeśli snapshots > 5GB: dodaj cron w DataCollector do usuwania >90 dni
-- Zachowaj: station_stops (złoto) — usuwaj: operations_snapshots i train_operations >90 dni

ZADANIE 3 — Optymalizacja indeksów dla zapytań ML:

-- Główne zapytanie mv_training_features filtruje po: operating_date, train_status
-- Dodaj partial index dla typowego zakresu treningowego

CREATE INDEX CONCURRENTLY idx_ss_ml_range
  ON station_stops (planned_departure, station_id, train_op_id)
  WHERE delay_departure_min IS NOT NULL;

-- Analiza EXPLAIN ANALYZE:
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM mv_training_features
WHERE operating_date BETWEEN '2025-10-01' AND '2026-03-31'
LIMIT 10000;
-- Oczekiwane: Index Scan, nie Seq Scan

ZADANIE 4 — pg_partman (opcjonalne, gdy jest >12 partycji):
  Zainstaluj pg_partman w Docker image PostgreSQL dla automatycznego tworzenia partycji.
  Alternatywnie: skrypt cron tworzący partycję na następny kwartał 1. dnia każdego kwartału.

Po implementacji wykonaj OBOWIĄZKOWO:
1. Zmierz czas EXPLAIN ANALYZE przed i po partycjonowaniu
2. Zaktualizuj schemat bazy w DEVELOPMENT.md (zaznacz partycjonowanie)
3. Dodaj procedurę maintenance do DEVELOPMENT.md (co usuwamy, kiedy)
```

---

## CZĘŚĆ B — Harmonogram i priorytety

### Fazy w kolejności pilności

| Faza | Zadanie | Czas estymowany | Priorytet | Blokuje |
|------|---------|-----------------|-----------|---------|
| H.0 | Audyt danych | 2–4 h | **KRYTYCZNY** | Wszystko |
| H.1 | Backfill pogody | 2–3 h | **WYSOKI** | H.5 (lepsza jakość features) |
| H.2 | Weryfikacja kalendarza | 30 min | NISKI | — |
| H.3.1 | Scraper kolejopedia | 1–2 dni | **WYSOKI** | H.5 (dane historyczne) |
| H.3.2 | Synteza danych (opcjonalna) | 4–6 h | OPCJONALNY | — |
| H.4 | Quality monitoring | 4–6 h | ŚREDNI | Długoterminowe zarządzanie |
| H.5 | Pipeline ML (historyczne) | 4–8 h | WYSOKI | Trenowanie modelu finalnego |
| H.6 | Partycjonowanie DB | 2–4 h | NISKI | Dopiero gdy >6 mies. danych |

### Ścieżka minimalna (cel: działający model w 2 tygodnie)

```
Tydzień 1:
  H.0 → audyt → wiem co mam
  H.1 → backfill pogody → features mają weather dla całego okresu RT
  Opcjonalnie: zbadaj HTML kolejopedia.pl ręcznie (bez implementacji)

Tydzień 2:
  H.3.1 → zaimplementuj scraper (najpierw dla 1 miesiąca testowo)
  H.5 → retrenuj model z pełnymi danymi
  → Mierz: czy MAE się poprawiło vs model z 2 miesiącami RT?
```

### Ścieżka pełna (cel: model sezonowy po 12 miesiącach danych)

```
Miesiące 1–2: H.0 + H.1 + H.2 (gotowe w tydzień)
Miesiące 1–3: H.3.1 (scraping za ostatni rok, partiami po ~30 dni)
Miesiące 3–4: H.4 (monitoring jakości)
Miesiące 3–6: H.5 (trenowanie na mieszanych danych, walidacja sezonowa)
Miesiące 6+:  H.6 (optymalizacja gdy baza rośnie)
Miesiące 9+:  RT collector daje pełne 12 mies. danych — retrenuj model "docelowy"
```

---

## CZĘŚĆ C — Krytyczne ustalenia dla danych historycznych

Analogicznie do sekcji "KRYTYCZNE ustalenia empiryczne" w CLAUDE.md — tu zbieramy
pułapki specyficzne dla danych historycznych. Aktualizuj listę w miarę postępów.

1. **station_stops.data_source MUSI być ustawiony** — bez tego nie wiesz które rekordy
   są prawdziwe przy walidacji modelu. NIGDY nie waliduj na danych `synthetic`.

2. **Podział train/val/test MUSI być chronologiczny** — losowy split na szeregach
   czasowych powoduje data leakage: model "widzi przyszłość" przez LAG() i prev_stop_delay.

3. **Opóźnienia z kolejopedia to gotowe wartości** — ale liczymy je sami z actual - planned
   (tak jak w parserze RT). Wartość z kolejopedia służy tylko jako cross-check, nie jako
   primary source. Rozbieżność >2 min = podejrzany rekord, oznacz flagą.

4. **Dopasowanie stacji po nazwie jest niedoskonałe** — "Warszawa Cent." vs "Warszawa Centralna"
   vs "W-wa Centralna". Zbuduj tablicę aliasów i ręcznie sprawdź top-50 stacji.

5. **Kolejopedia może nie mieć niektórych pociągów** — regionalne kursy PKP Przewozy
   Regionalne są często nieobecne. Spodziewaj się 60–80% pokrycia, nie 100%.

6. **Weather backfill a strefy czasowe** — Open-Meteo archive zwraca UTC. Nasze
   `planned_departure` to czas lokalny (Europe/Warsaw = UTC+1 lub UTC+2 latem). Upewnij
   się że JOIN po czasie jest poprawny: `observed_at AT TIME ZONE 'Europe/Warsaw' <= planned_departure`.

7. **Synteza nie zastąpi letnich danych** — jeśli brakuje całego lata (jul-aug),
   syntetyczne dane będą oparte na wzorcach wiosennych/jesiennych ≠ wakacyjny szczyt
   ruchu. W takim razie lepiej poczekać na RT data niż trenować na syntezie.

8. **LAG() (prev_stop_delay) dla danych kolejopedia** — musimy wyznaczyć właściwą
   kolejność przystanków. Kolejopedia podaje sekwencję przystanków — użyj jej do
   planned_sequence, a LAG() wyliczy się poprawnie w mv_training_features.

9. **historical_import_log jako checkpoint** — zawsze sprawdzaj status='OK' przed
   re-importem. Użyj skip_existing=True domyślnie. Pełny re-import za rok = ~200k
   zapytań HTTP = kilka dni przy 2s delay. Miej checkpointy.

10. **Seasonality leakage przy early stopping XGBoost** — jeśli val set jest tylko z
    jednego sezonu (np. ostatnie 2 miesiące = wiosna), early stopping może zatrzymać
    model zbyt wcześnie dla wzorców zimowych. Rozwiązanie: val set losowy z każdego sezonu
    (wyjątek od zasady chronologicznego podziału — tylko dla early stopping, nie dla raportowania MAE).

---

## CZĘŚĆ D — Szybkie komendy (po implementacji wszystkich faz)

```bash
# Audyt jakości danych treningowych
poetry run cns data-audit

# Backfill historycznej pogody (jednorazowo za brakujący okres)
poetry run cns backfill-weather --date-from 2025-06-01 --date-to 2026-05-31

# Import historyczny kolejopedia (30 dni testowo)
poetry run cns import-historical \
  --date-from 2025-06-01 --date-to 2025-07-01 \
  --limit-per-day 30 --dry-run

# Pełny import za 12 miesięcy (uruchom w tle, zajmie dni)
nohup poetry run cns import-historical \
  --date-from 2025-06-01 --date-to 2026-05-31 \
  --limit-per-day 50 > logs/historical_import.log 2>&1 &

# Sprawdź jakość danych przez API
curl http://localhost:8000/health/data | python -m json.tool

# Retrenuj model z pełnymi danymi historycznymi
poetry run python -m cns.ml.train_xgb

# Sprawdź pokrycie sezonowe
docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach \
  -c "SELECT EXTRACT(MONTH FROM day) AS mth, COUNT(*), AVG(weather_pct)
      FROM mv_data_quality_daily GROUP BY 1 ORDER BY 1;"
```

---

*cyrk_na_szynach v2 | Strategia danych historycznych | 2026-06-01*
