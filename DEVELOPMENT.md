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

## Jak zacząć nowy czat z Claude

1. Wklej zawartość `CONTEXT.md` na początku rozmowy
2. Opisz konkretne zadanie
3. Jeśli zadanie dotyczy konkretnego pliku — wklej jego zawartość

**Przykładowe prompty do nowych czatów:**
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Chcę zbudować dashboard w Streamlit pokazujący aktualne opóźnienia."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Dodaj endpoint FastAPI `/delays/carrier/{code}` z opóźnieniami per przewoźnik."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Napisz testy integracyjne dla API używając httpx.TestClient."
- "Mam projekt cyrk_na_szynach [wklej CONTEXT.md]. Połącz train_operations z schedules przez scheduleId+orderId."
