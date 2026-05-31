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
| GET | `/health/collector` | collector_health | Stan kolektora: status OK/WARNING/CRITICAL, pokrycie 24h, luki. 503 gdy brak danych. |
| GET | `/delays/stations/map` | JOIN v_station_delay_stats+stations | Stacje z koordynatami GPS i metrykami opóźnień – do mapy. Zwraca tylko stacje z lat/lon. |
| GET | `/predict` | XGBoostDelayPredictor | **Główny endpoint predykcji.** Params: `station_id`, `planned_departure`, `day_type?`, `prev_stop_delay_min?` (domyślnie 0), `planned_sequence?` (domyślnie 1). Zwraca predykcję + CI + SHAP explanation. |
| GET | `/predict/baseline` | BaselineModel | Predykcja benchmark (historyczna mediana). Params: `station_id`, `planned_departure` (ISO 8601), `day_type?` |

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

-- Zmaterializowany (Faza 2.1):
mv_training_features   -- widok ML: station_stops + weather + calendar + LAG
                       -- UNIQUE INDEX (id), odświeżany CONCURRENTLY po każdym snapshot
                       -- Tworzony WITH NO DATA, wypełniany przez refresh_features()
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

### `collector/health.py` — HealthChecker (Faza 5.1)

Monitoring procesu kolekcjonowania danych. Wykrywa luki i zapisuje status do `collector_health`.

**Progi alertów:**

| Status | Warunek | Opis |
|--------|---------|------|
| `CRITICAL` | `minutes_since_snapshot >= 30` lub brak snapshotów | Kolektor prawdopodobnie nie działa |
| `WARNING` | `snapshots_last_24h < int(96 * 0.80) = 76` | Pokrycie <80% – możliwe restartowanie lub rate-limit |
| `OK` | Oba warunki spełnione | Kolektor działa poprawnie |

**Luka (gap):** przerwa między kolejnymi snapshotami > 20 minut.
Oczekiwany interwał: co 15 minut → luka to co najmniej jeden pominięty cykl.

**Architektura:**
```
compute_health_status(snapshots: list[datetime]) → HealthStatus  ← czysta funkcja (testowalana bez DB)
HealthChecker._fetch_recent_snapshots()  → zapytanie do operations_snapshots
HealthChecker.check()                   → fetch + compute + log
HealthChecker.save_check(status)        → INSERT do collector_health
```

**Harmonogram:** wywoływane przez `DataCollector._run_health_check()` co 5 minut w `_tick()`.
Dostępne tylko gdy `storage` jest `PostgresStorage` (guard: `hasattr(storage, "database_url")`).

**Endpoint `GET /health/collector`:**
```json
{
  "status": "OK",
  "last_snapshot_at": "2026-05-31T14:30:00+00:00",
  "minutes_since_last_snapshot": 8,
  "snapshots_last_24h": 93,
  "expected_24h": 96,
  "coverage_pct": 96.9,
  "gaps_last_24h": [
    {"from_time": "2026-05-31T02:00:00+00:00", "to_time": "2026-05-31T02:45:00+00:00", "gap_minutes": 45}
  ],
  "checked_at": "2026-05-31T14:38:00+00:00"
}
```
Status 503 gdy brak wpisów w `collector_health` (kolektor nie uruchomiony).

### `ml/xgb_model.py` — XGBoostDelayPredictor (Faza 3.2)

Gradient Boosting na widoku `mv_training_features`. Zastępuje baseline jako model produkcyjny.

**Cechy (17 łącznie):**
```
Numeryczne (15):
  hour_of_day          – godzina planowanego odjazdu [0-23]
  day_of_week          – dzień tygodnia PostgreSQL DOW [0=Sun..6=Sat]
  month                – miesiąc [1-12]
  planned_sequence     – numer przystanku na trasie
  prev_stop_delay_min  ← NAJWAŻNIEJSZY: propagacja opóźnienia z poprzedniego przystanku
  temperature_c / precipitation_mm / wind_speed_kmh / snowfall_cm / visibility_m
  is_snowing / is_heavy_rain / is_strong_wind / is_frost / is_dense_fog

Kategoryczne → target encoding (2):
  station_id           – stacja (zakodowana jako średni delay)
  day_type             – typ dnia (WORKING/WEEKEND/HOLIDAY/…)
```

**Architektura i hiperparametry:**
```python
XGBRegressor(n_estimators=500, learning_rate=0.05, max_depth=6,
             subsample=0.8, colsample_bytree=0.8,
             early_stopping_rounds=20, eval_metric="mae", tree_method="hist")
```

**Target encoding:**
Obliczany wyłącznie na zbiorze treningowym (przed splitem). Target encoding z leakage
jest częstą przyczyną zbyt optymistycznych metryk – tu stosujemy właściwe podejście.

**Przedziały ufności:**
Obliczane z percentyli residuów walidacyjnych `(actual − predicted)`:
- `ci_low  = pred + p15(residuals)` – dolna granica 70% CI
- `p75     = pred + p75(residuals)` – górny kwartyl
- `ci_high = pred + p85(residuals)` – górna granica 70% CI

**SHAP wyjaśnienia:**
`shap.TreeExplainer` na XGBRegressor → top-5 cech z wpływem w minutach.
Wartości SHAP sumują się do predykcji (additive feature attribution).

**Metryki referencyjne (TBD — uruchom train_xgb po zebraniu ≥30 dni danych):**
```
Baseline MAE: TBD min
XGB MAE:      TBD min  (cel: ≥15% poprawa vs baseline)
XGB RMSE:     TBD min
Coverage L1:  TBD %
```

**Feature importance top-10 (TBD):**
Uruchom `python -m cns.ml.train_xgb` aby wygenerować.
Oczekiwana kolejność: prev_stop_delay_min, station_id, hour_of_day, day_of_week, ...

**Gate jakości (train_xgb.py):**
```
val_MAE <= baseline_MAE * 0.85
```
Jeśli warunek nie jest spełniony → model NIE jest zapisywany (sys.exit(2)).
Zapobiega regresji modelu produkcyjnego.

**Proces trenowania:**
```bash
poetry run python -m cns.ml.train_xgb
```
Dane: 180 dni z `mv_training_features` (wymaga REFRESH MATERIALIZED VIEW).
Split chronologiczny 80/20 (nie losowy) – zapobiega data leakage czasowego.

### `ml/baseline_model.py` — BaselineModel (Faza 3.1)

Predykcja opóźnienia jako historyczna mediana per `(station_id, hour_bucket, day_type)`.
Służy jako benchmark (dolna granica MSE) dla modeli ML wyższego rzędu.

**Hierarchia fallback (4 poziomy):**
```
L1  (station_id, hour_bucket, day_type)  fallback=False  – dokładne dopasowanie
L2  (station_id, hour_bucket)            fallback=True   – bez day_type
L3  (station_id,)                        fallback=True   – tylko stacja
L4  global                               fallback=True   – absolutny fallback
```
`hour_bucket = hour // 2` → 12 bucketów dziennie (redukuje szum małych próbek).

**Statystyki na każdym poziomie:**
| Pole | Opis |
|------|------|
| `mean_delay` | Średnia opóźnienia [min] |
| `median_delay` | Mediana – używana jako predykcja |
| `p75_delay` | 75. percentyl (upper-middle estimate) |
| `p90_delay` | 90. percentyl (worst case) |
| `sample_count` | Liczba próbek w tym buckecie |
| `fallback` | True gdy użyto L2/L3/L4 zamiast L1 |

**Trenowanie i metryki referencyjne:**
```bash
poetry run python -m cns.ml.train_baseline
```
Dane: `mv_training_features` za 90 dni, split 72/18 (po dacie, nie losowo).
Metryki: MAE, RMSE, Coverage% (% predykcji z L1).

> **Benchmark (TBD):** Uruchom `train_baseline` po zebraniu ≥30 dni danych.
> Oczekiwane wartości: MAE ~3–8 min, RMSE ~8–15 min, Coverage 40–70%.

**Serializacja:** `joblib.dump/load` → `models/baseline_v{YYYYMMDD}.pkl`.
Ładowanie przy starcie FastAPI przez `lifespan` event.

### Web Dashboard – Widoki (Faza 4.2)

#### Widok `/predict` – Widget predykcji

**Formularz:**
- Autocomplete stacji przez natywny `<datalist>` (fetch `/delays/stations/top?limit=200` przy mountowaniu)
- Mapowanie `station_name → station_id` przez lookup po tablicy stations
- `<input type="datetime-local">` dla odjazdu
- Number input dla `prev_stop_delay_min` (default 0)

**Wynik predykcji:**
- Duża liczba kolorem (zielony<5/żółty5-15/czerwony>15 min)
- `DelayBadge` z odpowiednim progiem
- Paski percentylowe:
  - 50% szansa na mniej niż `predicted_delay_min` min
  - 75% szansa na mniej niż `p75_delay_min` min
  - Pasek CI 70%: `[ci_low, ci_high]` narysowany jako `<div>` na siatce
- SHAP waterfall (top-5):
  - emoji + polska nazwa cechy (tabela `FEATURE_META`)
  - pasek proporcjonalny do wartości absolutnej impact
  - kolor: pomarańczowy = zwiększa opóźnienie, niebieski = zmniejsza
  - wartość wejściowa cechy w nawiasie

**Historia predykcji (localStorage):**
- Klucz: `cns_predict_history` (JSON, max 5 elementów)
- Zawartość: station_id, station_name, departure, prev_delay, predicted_min, model, timestamp
- Wyświetlane jako mini-karty z DelayBadge i godziną predykcji

**Fallback w /predict:**
Jeśli XGBoost nie załadowany → API zwraca predykcję baseline (model `"baseline_fallback"`).
Jeśli oba modele niedostępne → 503 z komunikatem "Model w trakcie ładowania".

---

**Endpoint `GET /predict` — szczegóły:**

```
GET /predict?station_id=33506&planned_departure=2026-05-31T10:00:00&prev_stop_delay_min=5
```

**Parametry:**
| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `station_id` | str | wymagany | ID stacji PKP (np. 33506) |
| `planned_departure` | str | wymagany | ISO 8601: `2026-05-31T10:00:00` |
| `day_type` | str | auto-detect | WORKING/WEEKEND/HOLIDAY/… (CalendarService) |
| `prev_stop_delay_min` | float | 0 | Opóźnienie z poprzedniego przystanku [min] |
| `planned_sequence` | int | 1 | Numer przystanku na trasie |

**Response 200:**
```json
{
  "station_id": "33506",
  "station_name": "Warszawa Centralna",
  "predicted_delay_min": 12.3,
  "p75_delay_min": 18.1,
  "confidence_interval": [6.2, 21.4],
  "model": "xgboost",
  "model_date": "2026-05-31",
  "explanation": [
    {"feature": "prev_stop_delay_min", "impact": 8.3, "value": 5},
    {"feature": "station_id",          "impact": 2.1, "value": "33506"},
    {"feature": "hour_of_day",         "impact": 1.4, "value": 10},
    {"feature": "is_heavy_rain",       "impact": 0.8, "value": false},
    {"feature": "day_of_week",         "impact": -0.3, "value": 6}
  ]
}
```

**Response 503** (model niedostępny):
```json
{"detail": "Model w trakcie ładowania. Uruchom: python -m cns.ml.train_xgb"}
```

**Logika SHAP:**
`shap.TreeExplainer` zwraca wektor wartości SHAP dla każdej cechy. Wartości sumują się do predykcji minus oczekiwana wartość modelu (baseline SHAP). Cecha z `impact > 0` zwiększa przewidywane opóźnienie, `impact < 0` zmniejsza. Frontend sortuje po `|impact|` malejąco i pokazuje top-5 z emoji i polską nazwą.

#### Widok `/delays` – Tablica opóźnień RT

Dane: `GET /delays/active` · odświeżanie co 60 s · maks. 200 wierszy.

**Funkcje:**
- Filtr tekstowy na nazwę stacji (input z lupą, kasowanie jednym kliknięciem)
- Sortowanie po każdej kolumnie kliknięciem nagłówka (domyślnie: opóźnienie ↓)
- Licznik `Aktualnie opóźnionych: N` (filtrowany po kolumnie station_name przez `@tanstack/react-table`)
- Odliczanie do kolejnego odświeżenia (badge z sekundami, pomarańczowy gdy ≤10s)
- Przycisk ręcznego odświeżenia ze spinerkiem

**Konwencja kolorowania wierszy:**

| Opóźnienie | Kolor tła | Kolor badge |
|-----------|-----------|-------------|
| < 5 min | `bg-green-50/70` | zielony |
| 5–15 min | `bg-yellow-50/70` | żółty |
| > 15 min | `bg-red-50/70` | czerwony |
| Odwołany | `bg-zinc-50` | szary |

**Komponenty:**
- `DelayBadge` — kolorowy badge z wartością; progi: <5/5–15/>15 min
- `StatChip` — chip ze statystykami (liczba pociągów, opóźnionych, maks.)
- `@tanstack/react-table` z `getFilteredRowModel` + `getSortedRowModel`

#### Widok `/map` – Mapa Polski z MapLibre GL

Dane: `GET /delays/stations/map` · jednorazowe pobranie przy wejściu.

**Mapa:**
- Biblioteka: `maplibre-gl` v5 (raw API przez `useRef`, lazy `import()`)
- Styl: `https://demotiles.maplibre.org/style.json` (bezpłatny, bez klucza API)
- Centrum: `[lng=19.1, lat=52.0, zoom=6]` (Polska)
- Kontrolka nawigacji: `NavigationControl` (top-right)

**Stacje – layer `circle`:**
- Rozmiar proporcjonalny do `avg_delay_min` (5–20px), interpolacja liniowa
- Kolor wg skali:

| Zakres | Kolor hex |
|--------|-----------|
| 0–3 min | `#22c55e` (zielony) |
| 3–8 min | `#eab308` (żółty) |
| 8–15 min | `#f97316` (pomarańczowy) |
| >15 min | `#ef4444` (czerwony) |
| brak danych | `#94a3b8` (szary) |

**Tooltip (hover):**
- Absolutnie pozycjonowany `<div>` nad kontenerem mapy
- Treść: nazwa stacji, śr. opóźnienie, % opóźnionych, liczba pomiarów
- Logika: `map.on("mousemove", "stations-circle", ...)` → state `tooltip: { x, y, props }`

**Architektura React:**
- Inicjalizacja mapy: `useEffect([], [])` — lazy `import("maplibre-gl")`
- Dodanie danych: `useEffect([stations])` — po załadowaniu stacji z API
- Cleanup: `map.remove()` przy odmontowaniu komponentu

**CSS MapLibre:** dodany w `globals.css` przez `@import "maplibre-gl/dist/maplibre-gl.css"`

### Web Dashboard (Faza 4.1)

Next.js 16 App Router + React 19 + Tailwind CSS v4. Katalog: `dashboard/`.

**Uruchomienie lokalnie:**
```bash
cd dashboard
npm run dev          # http://localhost:3000
# lub produkcja:
npm run build && npm start
```

**Struktura katalogów:**
```
dashboard/
├── src/
│   ├── app/
│   │   ├── layout.tsx           ← Root layout: NavBar + footer
│   │   ├── page.tsx             ← redirect → /delays
│   │   ├── error.tsx            ← Error boundary (unstable_retry – Next.js 16)
│   │   ├── loading.tsx          ← Skeleton loader (Suspense fallback)
│   │   ├── globals.css          ← Tailwind v4 (@import "tailwindcss")
│   │   ├── delays/page.tsx      ← Tablica opóźnień RT (polling 30s, @tanstack/react-table)
│   │   ├── map/page.tsx         ← Stacje z koordynatami (lista + detail panel)
│   │   └── predict/page.tsx     ← Widget predykcji XGBoost + baseline
│   ├── components/
│   │   └── NavBar.tsx           ← Nawigacja (usePathname, aktywny link)
│   └── lib/
│       └── api.ts               ← fetch wrapper, typy TS, wszystkie endpointy
├── Dockerfile                   ← Multi-stage: node:24-alpine build + runner
├── next.config.ts               ← output: 'standalone' (dla Dockerfile)
└── .env.local                   ← NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Zależności frontendowe:**
- `maplibre-gl` v5 – mapa wektorowa (raw API, lazy import, bez klucza API)
- `react-map-gl` v8 – wrapper React do MapLibre (zainstalowany, do dalszego użycia)
- `@tanstack/react-table` v8 – sortowanie, filtrowanie kolumn
- `recharts` v3 – wykresy (gotowe do widgetów w /delays i /predict)
- `lucide-react` – ikony SVG
- `date-fns` v4 – formatowanie dat/czasu po polsku

**Zmienne środowiskowe:**
```
NEXT_PUBLIC_API_URL=http://localhost:8000   # lokalny dev
NEXT_PUBLIC_API_URL=http://fastapi:8000    # Docker Compose
```

**Docker Compose:**
Serwis `dashboard` w `docker-compose.yml` – build z `dashboard/Dockerfile`,
port 3000, `depends_on: [fastapi]`.

### `mv_training_features` — Feature Store (Faza 2.1)

Widok zmaterializowany łączący wszystkie źródła danych dla modelu ML.
Definicja: `migrations/004_features.sql`. Odświeżany przez `PostgresStorage.refresh_features()`.

**Kolumny i ich opis:**

| Kolumna | Źródło | Opis |
|---------|--------|------|
| `id` | station_stops.id | PK widoku (wymagany przez CONCURRENTLY) |
| `station_id` | station_stops | INTEGER – identyfikator stacji |
| `station_name` | stations.name | Nazwa stacji (LEFT JOIN) |
| `delay_departure_min` | station_stops | **Target**: opóźnienie odjazdu [min], NULL odfiltrowany |
| `delay_arrival_min` | station_stops | Target alternatywny: opóźnienie przyjazdu [min] |
| `operating_date` | planned_departure::date | Data kursowania pociągu |
| `hour_of_day` | EXTRACT(HOUR …) | Godzina planowanego odjazdu [0-23] |
| `day_of_week` | EXTRACT(DOW …) | Dzień tygodnia [0=Sun … 6=Sat] |
| `month` | EXTRACT(MONTH …) | Miesiąc [1-12] |
| `day_type` | calendar_events (zone IS NULL) | Typ dnia ogólnopolski (HOLIDAY/WEEKEND/…) |
| `day_type_zone_b` | calendar_events (zone='B') | Typ dnia strefa B (mazowieckie, śląskie, …) |
| `prev_stop_delay_min` | LAG() | Opóźnienie poprzedniego przystanku; NULL dla pierwszego |
| `planned_sequence` | station_stops | Numer przystanku na trasie |
| `sequence_delta` | actual - planned | Zmiana kolejności przystanków |
| `temperature_c` | weather_observations | Temperatura [°C] |
| `precipitation_mm` | weather_observations | Opady [mm] |
| `wind_speed_kmh` | weather_observations | Prędkość wiatru [km/h] |
| `snowfall_cm` | weather_observations | Opady śniegu [cm] |
| `visibility_m` | weather_observations | Widzialność [m] |
| `cloud_cover_pct` | weather_observations | Zachmurzenie [%] |
| `weather_code` | weather_observations | Kod WMO |
| `is_snowing` | snowfall_cm > 1 | Opady śniegu >1 cm |
| `is_heavy_rain` | precipitation_mm > 5 | Intensywny deszcz >5 mm |
| `is_strong_wind` | wind_speed_kmh > 70 | Silny wiatr >70 km/h |
| `is_frost` | temperature_c < -10 | Silny mróz < -10°C |
| `is_dense_fog` | visibility_m < 200 | Gęsta mgła <200 m |
| `train_status` | train_operations | Filtr: tylko C (zakończone) i P (w trasie) |
| `snapshot_time` | operations_snapshots.fetched_at | Czas pobrania snapshotu |

**Kluczowe decyzje projektowe:**

- `WITH NO DATA` przy CREATE – migracja bezpieczna na zaludnionej bazie
- LATERAL JOIN na `weather_observations`: `observed_at <= planned_departure AND is_forecast = FALSE` – bierzemy rzeczywistą obserwację, nie prognozę
- `station_stops.station_id` = INTEGER, `weather_observations.station_id` = VARCHAR(20) → rzutowanie `ss.station_id::TEXT` w LATERAL
- `LAG()` daje NULL dla pierwszego przystanku każdego pociągu (propagacja opóźnień)

**Strategia odświeżania:**

```
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_training_features
```

- `CONCURRENTLY` = czytelnicy nie są blokowani (brak ExclusiveLock)
- Wymaga `autocommit=True` na połączeniu (psycopg3 domyślnie ma transakcję)
- Implementacja: `_conn_autocommit()` w postgres.py → `refresh_features()` → wątek daemon
- Czas odświeżania: ~10-30s na typowy zbiór 1-2M wierszy (zależy od indeksów)
- Częstotliwość: po każdym `save_snapshot()` (co 15 min) → widok świeży dla ML

```python
# Dispatcher w DataCollector (nie blokuje pętli kolektora):
def _refresh_features_async(self) -> None:
    t = threading.Thread(target=self._do_refresh_features, daemon=True)
    t.start()
```

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
