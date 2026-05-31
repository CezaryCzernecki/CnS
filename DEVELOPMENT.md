# cyrk_na_szynach – dokumentacja techniczna

## Architektura systemu

```
[pdp-api.plk-sa.pl]
        │
        ▼
[PKPClient]              ← HTTP, retry 3x, rate-limit headers
        │
        ▼
[DataCollector]          ← harmonogram: ops/15min, dis/60min, sched/1dzień
        │
   ┌────┴────┐
   ▼         ▼
[Parser]  [Storage]      ← Parser: JSON→dataclass | Storage: PostgreSQL/JSON
        │
        ▼
[PostgreSQL]             ← batch insert przez unnest (10k pociągów / 170k przystanków)
  ├── stations
  ├── carriers
  ├── commercial_categories
  ├── schedules
  ├── schedule_stops
  ├── operations_snapshots
  ├── train_operations
  ├── station_stops      ← główna tabela (~650k rekordów/dzień)
  ├── disruptions
  └── disruption_affected_routes

[FastAPI]                ← REST API (port 8000)
  ├── GET /delays/stations/top   ← v_station_delay_stats
  ├── GET /delays/active         ← v_active_delays
  └── GET /stats                 ← zliczenia tabel
```

## Moduły

### `collector/client.py` — PKPClient

Klient HTTP dla API PKP PLK.

**Kluczowe zachowania:**
- Autoryzacja przez nagłówek `X-API-Key`
- Retry 3x dla błędów 5xx (backoff 2s, 4s, 8s)
- Śledzenie limitów z nagłówków `X-RateLimit-Hourly-Remaining` i `X-RateLimit-Daily-Remaining`
- Ostrzeżenie w logu gdy < 10 zapytań godzinowych
- `RateLimitError` z polem `retry_after` (z nagłówka `Retry-After` lub None)

**Endpointy:**
```python
client.get_data_version()                    # GUID wersji – sprawdź przed /operations
client.get_operations(with_planned=True)     # dane RT, pageSize=10000
client.get_disruptions()                     # utrudnienia
client.get_schedules(date_from, date_to)     # rozkład planowy
client.get_stations(page_size=5000)          # słownik stacji
client.get_carriers()                        # słownik przewoźników
```

### `collector/parser.py` — Parser

Transformuje surowy JSON z API na typed dataclasses.

**Ważne:** Parser jest defensywny — używa `.get()` wszędzie, nie crashuje przy brakujących polach.

```python
parse_operations(raw, fetched_at, data_version) → OperationsSnapshot
parse_stations(raw) → list[Station]
parse_carriers(raw) → list[Carrier]
parse_disruptions(raw, collected_at) → list[Disruption]
```

### `models/records.py` — Modele danych

```python
@dataclass
class StationStop:
    station_id: str
    station_name: str          # ze słownika stations{} w odpowiedzi
    planned_sequence: int
    actual_sequence: int
    planned_arrival: Optional[datetime]
    actual_arrival: Optional[datetime]
    planned_departure: Optional[datetime]
    actual_departure: Optional[datetime]

    MAX_REALISTIC_DELAY = 200  # próg filtrowania anomalii (minut)

    @property
    def delay_arrival_minutes(self) -> Optional[int]: ...   # None jeśli anomalia
    @property
    def delay_departure_minutes(self) -> Optional[int]: ... # None jeśli anomalia
    @property
    def is_on_time(self) -> bool: ...

@dataclass
class TrainOperation:
    schedule_id: str           # str (castowany z int z API)
    order_id: str              # str (castowany z int z API)
    operating_date: str
    train_status: str          # S/P/C/X/Q
    train_number: None         # NIEDOSTĘPNE w /operations
    carrier_code: None         # NIEDOSTĘPNE w /operations
    stops: list[StationStop]

@dataclass
class OperationsSnapshot:
    fetched_at: datetime
    data_version_guid: Optional[str]
    total_trains: int
    total_stops: int
    station_names: dict[str, str]
    trains: list[TrainOperation]
```

### `collector/collector.py` — DataCollector

Orkiestrator harmonogramu.

**Harmonogram (plan Basic):**
| Endpoint | Interwał | Zapytań/dzień |
|----------|----------|---------------|
| `/data-version` + `/operations` | co 15 min | ~96 |
| `/disruptions` | co 60 min | ~24 |
| `/schedules` | raz dziennie po 04:00 | ~1 |
| bootstrap (stacje, przewoźnicy) | przy starcie | ~2 |
| Open-Meteo (pogoda, 30 stacji) | co 60 min | ~30 (zewnętrzne API) |

**Cache wersji danych:**
Przed każdym pobraniem `/operations` sprawdza `/data-version`.
Jeśli GUID niezmieniony — pomija pobranie (oszczędność limitów).

**Obsługa rate-limit:**
- HTTP 429 → `RateLimitError`
- Jeśli `Retry-After` w nagłówku → czeka tyle sekund + 5s bufor
- Jeśli brak nagłówka → oblicza sekundy do początku kolejnej godziny + 30s

### `storage/postgres.py` — PostgresStorage

**Wzorzec połączenia:** nowe połączenie per operacja (`with _conn(...) as conn:`).

**Optymalizacja `save_snapshot` (od v1.0):**

Stary kod wykonywał ~20 000 round-tripów na snapshot (SAVEPOINT + INSERT + RELEASE per pociąg).
Nowy kod: 2 round-tripy.

```
Stara implementacja (v0.3):   ~50s dla 10k pociągów × 17 przystanków
  └─ for train in trains:
       SAVEPOINT sp_train          # +1 round trip
       INSERT train RETURNING id   # +1 round trip
       executemany(stop_rows)      # +1 round trip
       RELEASE SAVEPOINT           # +1 round trip
  = 4 × 10 000 = 40 000 round-tripów

Nowa implementacja (v1.0):    cel <10s
  ├─ INSERT snapshot RETURNING id              # 1 round trip
  ├─ INSERT trains via unnest RETURNING id[]   # 1 round trip (wszystkie 10k naraz!)
  └─ executemany(ALL 170k stop rows)           # 1 wywołanie
  = 3 operacje łącznie
```

Kluczowy SQL dla batch insert pociągów:
```sql
INSERT INTO train_operations (snapshot_id, schedule_id, order_id, ...)
SELECT %s, unnest(%s::integer[]), unnest(%s::bigint[]), ...
RETURNING id
```

Walidacja przed otwarciem połączenia: rekordy z niepoprawnymi ID (nie-liczba) są filtrowane
w Pythonie — nie docierają do bazy, nie ma potrzeby SAVEPOINT per rekord.

**Protokół Storage:**
```python
class Storage(Protocol):
    def save_snapshot(self, snapshot: OperationsSnapshot) -> None: ...
    def save_disruptions(self, raw: dict) -> None: ...   # surowy JSON!
    def save_schedules(self, raw: dict) -> None: ...     # surowy JSON!
    def save_raw(self, name: str, data: dict) -> None: ...
```

### `api/app.py` — FastAPI

REST API do odczytu danych z bazy.

**Uruchomienie:**
```bash
poetry install -E api
poetry run cns api-serve [--host HOST] [--port PORT] [--reload]
```

**Endpointy:**

| Metoda | Ścieżka | Źródło | Opis |
|--------|---------|--------|------|
| GET | `/` | — | Health check |
| GET | `/delays/stations/top` | `v_station_delay_stats` | Top N stacji z największymi opóźnieniami (7 dni, min. 10 pomiarów). Param: `?limit=10` |
| GET | `/delays/active` | `v_active_delays` | Aktualnie opóźnione pociągi (status P). Param: `?limit=20` |
| GET | `/stats` | zliczenia | Liczba rekordów w każdej tabeli |

Swagger UI dostępny pod `/docs`, ReDoc pod `/redoc`.

**Modele Pydantic:**
```python
class StationDelayStat(BaseModel):
    station_id: Optional[int]
    station_name: Optional[str]
    total_stops: int
    stops_with_data: int
    delayed_count: int
    avg_delay_min: Optional[float]
    max_delay_min: Optional[int]
    delay_rate_pct: Optional[float]

class ActiveDelay(BaseModel):
    station_id: Optional[int]
    station_name: Optional[str]
    schedule_id: int
    order_id: int
    operating_date: Optional[str]
    planned_departure: Optional[str]
    actual_departure: Optional[str]
    delay_departure_min: Optional[int]
    delay_arrival_min: Optional[int]
    snapshot_time: Optional[str]
```

### `tests/test_postgres.py` — Testy storage

Testy jednostkowe bez żywej bazy danych — używają `unittest.mock` do mockowania psycopg3.

**Pokryte przypadki:**
- `upsert_stations`: pusta lista (brak połączenia), poprawne dane, niepoprawny station_id
- `upsert_carriers`: pusta lista, poprawne dane, przewoźnik bez kodu
- `save_snapshot`: batch insert pociągów (1 execute zamiast N), jeden executemany dla wszystkich przystanków, pominięcie pociągu z niepoprawnym ID, zawartość stop rows (delay_minutes)
- `get_stats`: poprawne klucze, jedno zapytanie SQL

**Wzorzec mockowania:**
```python
@pytest.fixture
def storage():
    with patch.object(PostgresStorage, "_verify_connection"):
        return PostgresStorage("postgresql://test/testdb")

def test_cokolwiek(storage):
    mock_conn, mock_cursor = _make_conn_mock()
    mock_cursor.fetchone.return_value = (42,)   # snapshot_id
    mock_cursor.fetchall.return_value = [(10,), (11,)]  # train_ids

    with patch("cns.storage.postgres._conn", return_value=mock_conn):
        storage.save_snapshot(snapshot)

    mock_cursor.executemany.assert_called_once()
```

## Schemat bazy danych

### Tabele słownikowe
```sql
stations (station_id PK, name, short_name, latitude, longitude, synced_at)
carriers (code PK, name, synced_at)
commercial_categories (symbol PK, name)
```

### Rozkład planowy
```sql
schedules (id, schedule_id, order_id, carrier_code FK, national_number,
           commercial_category, operating_date, fetched_at)
           UNIQUE(schedule_id, order_id, operating_date)

schedule_stops (id, schedule_id FK, station_id FK, order_number,
                arrival_time, departure_time, platform)
                UNIQUE(schedule_id, order_number)
```

### Dane operacyjne
```sql
operations_snapshots (id, data_version, fetched_at, total_trains, total_stops)

train_operations (id, snapshot_id FK, schedule_id, order_id, operating_date,
                  train_status CHECK('S','P','C','X','Q'), collected_at)

station_stops (id, train_op_id FK, station_id,
               planned_sequence, actual_sequence,
               planned_arrival, actual_arrival,
               planned_departure, actual_departure,
               delay_arrival_min,     ← NULL = brak danych lub anomalia >200min
               delay_departure_min)
```

### Utrudnienia
```sql
disruptions (id, disruption_id, message, collected_at, collected_date DATE,
             UNIQUE(disruption_id, collected_date))

disruption_affected_routes (id, disruption_id FK, schedule_id, order_id,
                             operating_date, station_id, sequence_number)
```

### Widoki analityczne
```sql
v_active_delays        -- aktualnie opóźnione pociągi (status P)
v_station_delay_stats  -- statystyki per stacja (ostatnie 7 dni, min. 10 danych)
```

### Kalendarz (Faza 1.2)
```sql
calendar_events (
    id          BIGSERIAL PK,
    event_date  DATE NOT NULL,
    zone        CHAR(1),          -- 'A' | 'B' | 'C' | NULL = cały kraj
    day_type    VARCHAR(30),      -- wartości enum DayType (HOLIDAY, WINTER_BREAK, ...)
    event_name  VARCHAR(100),     -- np. "Wielkanoc", "Ferie zimowe strefa B"
    UNIQUE NULLS NOT DISTINCT (event_date, zone)   -- PostgreSQL 15+
)

Indeks: calendar_events_date_idx ON (event_date)
```

### Dane pogodowe (Faza 1.1)
```sql
weather_observations (
    id              BIGSERIAL PK,
    station_id      VARCHAR(20),            -- FK do stations (soft, bez constraint)
    observed_at     TIMESTAMPTZ NOT NULL,   -- czas obserwacji / godzina prognozy
    is_forecast     BOOLEAN NOT NULL,       -- FALSE = obserwacja, TRUE = prognoza
    temperature_c   NUMERIC(5,2),           -- temperatura [°C]
    precipitation_mm NUMERIC(6,2),          -- opady [mm]
    wind_speed_kmh  NUMERIC(6,2),           -- prędkość wiatru [km/h]
    snowfall_cm     NUMERIC(6,2),           -- opady śniegu [cm]
    visibility_m    INTEGER,                -- widzialność [m]
    cloud_cover_pct SMALLINT,               -- zachmurzenie [%]
    weather_code    SMALLINT,               -- kod WMO
    collected_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (station_id, observed_at, is_forecast)
)

Indeksy:
  weather_observations_station_time_idx  ON (station_id, observed_at DESC)
  weather_observations_forecast_idx      ON (observed_at) WHERE is_forecast = TRUE
```

## Znane ograniczenia i gotcha

1. **trainNumber/carrierCode niedostępne w /operations** — są w /schedules.
   Do połączenia po `(schedule_id, order_id, operating_date)`.

2. **Stacje spoza słownika** — API zwraca stacje których nie ma w
   `/dictionaries/stations`. FK na `station_stops.station_id` jest `ON DELETE SET NULL`.

3. **Kategorie handlowe spoza słownika** — np. `Os/OsP`, `S4/S40`.
   FK na `schedules.commercial_category` jest `ON DELETE SET NULL`.

4. **Anomalie czasowe** — pociągi przesunięte o dobę w rozkładzie dają
   różnicę actual-planned = 1440 min. Filtrowane przez `MAX_REALISTIC_DELAY = 200`.

5. **10000 rekordów limit** — API zwraca max 10000 pociągów na stronę.
   Nie wiadomo czy sieć PKP ma więcej — do zbadania z paginacją.

6. **Dwa osobne limity godzinowe** — carriers używa innego licznika niż
   reszta (widoczne w logach: 1966 vs 99). Do zbadania.

7. **Batch insert a brak SAVEPOINT per rekord** — w nowej implementacji błędne
   rekordy są odfiltrowywane przed otwarciem połączenia (walidacja w Pythonie).
   Jeśli `unnest INSERT` rzuci wyjątek, cały snapshot jest wycofywany (rollback).
   Kompromis: uproszczenie kodu i lepsza wydajność kosztem granularności błędów.

## Wzrost danych (szacunki)

| Tabela | Rekordów/dzień | Rozmiar/miesiąc |
|--------|---------------|-----------------|
| station_stops | ~650 000 | ~2-3 GB |
| train_operations | ~38 400 | ~150 MB |
| operations_snapshots | 96 | ~1 MB |
| disruptions | ~310 | ~5 MB |

### `collector/calendar_service.py` — CalendarService

Klasyfikator typów dni kalendarzowych dla feature engineering modelu ML.
Nie wymaga połączenia z zewnętrznymi API ani bazą danych – czysta logika w Pythonie.

**Hierarchia priorytetów `get_day_type()`:**
```
HOLIDAY > WINTER_BREAK > SUMMER_BREAK > WEEKEND >
LONG_WEEKEND > HOLIDAY_EVE > HOLIDAY_RETURN > WORKING
```

**Święta ustawowe (12 dat/rok):**
- Stałe: 1.01, 6.01, 1.05, 3.05, 15.08, 1.11, 11.11, 25.12, 26.12
- Ruchome (algorytm Butchera/Meeusa): Wielkanoc, Poniedziałek Wielkanocny, Boże Ciało (+60 dni od Wielkanocy)
- Weryfikacja: Wielkanoc 2025=20.04, 2026=05.04, 2027=28.03

**Ferie zimowe – strefy MEN:**

| Strefa | Województwa |
|--------|-------------|
| A | dolnośląskie, opolskie, zachodniopomorskie, wielkopolskie |
| B | kujawsko-pomorskie, lubuskie, łódzkie, małopolskie, świętokrzyskie, pomorskie |
| C | lubelskie, mazowieckie, podkarpackie, podlaskie, śląskie, warmińsko-mazurskie |

Daty hardcoded dla lat 2024–2030 (`_WINTER_BREAKS` dict). Do aktualizacji po podaniu dat MEN na nowy rok.

**LONG_WEEKEND (pomost):** dzień roboczy, gdy obie strony (poprzedni i następny dzień) to dni wolne (święto lub weekend). Przykład: 2 maja 2025 (piątek) między 1.05 (czwartek, święto) a 3.05 (sobota, też święto).

**Metody publiczne:**
```python
cal = CalendarService()
cal.get_day_type(date(2025, 5, 2), zone="B")      # LONG_WEEKEND
cal.is_long_weekend(date(2025, 5, 2))              # True
cal.days_to_next_holiday(date(2025, 1, 1))         # 5 (do Trzech Króli)
cal.days_since_last_holiday(date(2025, 1, 6))      # 5 (od Nowego Roku)
cal.get_season(date(2025, 7, 15))                  # "SUMMER"
cal.generate_events(2025, 2030)                    # list[dict] → calendar_events
```

**Integracja z DataCollector:**
- `_bootstrap_calendar()` – sprawdza `is_calendar_populated()`, jeśli puste generuje 5 lat naprzód
- `_update_calendar_if_needed()` – wywoływane w każdym `_tick()`, uruchamia update 1 stycznia

### `collector/weather_client.py` — WeatherClient

Klient HTTP dla Open-Meteo API (bezpłatne, bez klucza API).
Pobiera warunki pogodowe dla ~30 głównych węzłów PKP co 1h.

**Kluczowe zachowania:**
- Brak klucza API — Open-Meteo jest bezpłatne
- Retry 3x z backoff 2s/4s/8s — identycznie jak PKPClient
- Brak śledzenia rate-limitów (Open-Meteo nie ma nagłówków X-RateLimit)
- `get_current` używa parametru `current=...` (jeden rekord, is_forecast=False)
- `get_forecast_48h` używa `hourly=...&forecast_days=2` (48 rekordów, is_forecast=True)
- Pola całkowitoliczbowe (`visibility_m`, `cloud_cover_pct`, `weather_code`) castowane do `int`
- Wartości `None` z API zachowywane jako `None` (defensywny parser)

**Metody publiczne:**
```python
client.get_current("33506", lat=52.22, lon=21.00)
# → {"station_id": "33506", "observed_at": "2026-05-31T12:00",
#    "is_forecast": False, "temperature_c": 18.5, ...}

client.get_forecast_48h("33506", lat=52.22, lon=21.00)
# → [{"station_id": "33506", "observed_at": "...", "is_forecast": True, ...}, ...]  # 48 rekordów
```

**Integracja z DataCollector:**
- `DataCollector.__init__` tworzy `self.weather_client = WeatherClient()`
- `_fetch_weather()` odpytuje stacje z DB przez `storage.get_weather_stations(limit=30)`
- Wywołuje `get_forecast_48h` dla każdej stacji, zapisuje przez `storage.save_weather_observations`
- Harmonogram: co 60 minut (nowy parametr `weather_interval_min=60`)
- Używa `hasattr` guard — przezroczyste dla `JsonFileStorage`

**Gotcha:**
- Open-Meteo zwraca `visibility` w metrach (nie km)
- `forecast_days=2` gwarantuje dokładnie 48 rekordów godzinowych (2×24)
- Stacje bez `latitude/longitude` w tabeli `stations` są pomijane

## Jak zacząć nowy czat z Claude

1. Wklej zawartość `CONTEXT.md` na początku rozmowy
2. Opisz konkretne zadanie
3. Jeśli zadanie dotyczy konkretnego pliku — wklej jego zawartość

**Przykładowe prompty do nowych czatów:**
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Chcę zbudować dashboard w Streamlit pokazujący aktualne opóźnienia."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Dodaj endpoint FastAPI `/delays/carrier/{code}` z opóźnieniami per przewoźnik."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Napisz testy integracyjne dla API używając httpx.TestClient."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Połącz train_operations z schedules przez scheduleId+orderId."
