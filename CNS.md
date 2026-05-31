# cyrk_na_szynach — Plan Rozwoju, Prompty i Hosting

> Dokument roboczy projektu. Aktualizuj sekcje ✅ i ❌ na bieżąco.
> Wersja: 2026-05-31 | Stan: zbieranie danych RT działa, dashboard i ML do zbudowania.

---

## Jak używać tego dokumentu

**Narzędzie:** Claude Code CLI (`claude` w katalogu projektu).

Każde zadanie ma gotowy prompt — kopiujesz blok `PROMPT` i wklejasz do sesji `claude`.
Claude Code czyta `CLAUDE.md` automatycznie przy każdym uruchomieniu, więc nie musisz
dodawać kontekstu projektu ręcznie. Upewnij się, że `CLAUDE.md` jest aktualny.

**Workflow per zadanie:**
1. Otwórz terminal w katalogu `cyrk_na_szynach/`
2. Uruchom: `claude`
3. Wklej prompt z poniższego planu
4. Po implementacji: `git add -A && git commit -m "feat: [opis]"`
5. Zamknij GitHub Issue powiązany z zadaniem
6. Zaktualizuj `CLAUDE.md` — sekcja "Co działa ✅" i backlog

**Konwencja dokumentacji:**
Każdy prompt kończy się instrukcją dopisania do `DEVELOPMENT.md`.
Claude Code ma obowiązek aktualizować dokumentację jako ostatni krok każdego zadania.
Nie commituj kodu bez aktualizacji dokumentacji.

---

## CZĘŚĆ A — Plan Deweloperski

Fazy są ułożone w kolejności zależności. Faza 1 nie blokuje Fazy 4 (frontend można
zacząć równolegle z danymi), ale Faza 3 (ML) wymaga Faz 1 i 2.

```
Faza 1: Dane Pogodowe     →  tabela weather_observations
Faza 2: Dane Kontekstowe  →  tabela calendar_events
Faza 3: Feature Store     →  widok mv_training_features (wymaga Faz 1+2)
Faza 4: Baseline Model    →  endpoint /predict/baseline (wymaga Fazy 3)
Faza 5: XGBoost ML        →  endpoint /predict (wymaga Fazy 4)
Faza 6: Web Dashboard     →  Next.js (można równolegle z Fazą 3)
Faza 7: Monitoring        →  endpoint /health/collector
```

---

### FAZA 1 — Dane Pogodowe

#### Zadanie 1.1 — WeatherClient i tabela weather_observations

**Status:** ❌ Do zrobienia
**GitHub Issue:** #1
**Pliki wyjściowe:** `cns/collector/weather_client.py`, `migrations/002_weather.sql`, `tests/test_weather.py`

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj WeatherClient i migrację bazy dla cyrk_na_szynach.

Kontekst projektu: cyrk_na_szynach — kolekcja danych RT PKP PLK.
Stack: Python 3.12, Poetry, psycopg3, PostgreSQL 16.
Wzorzec do naśladowania: cns/collector/client.py (retry, logging, rate-limit headers).
Wzorzec testu: cns/tests/test_postgres.py (unittest.mock, @pytest.fixture).

ZADANIE 1 — cns/collector/weather_client.py:
- API: Open-Meteo https://api.open-meteo.com/v1/forecast (bez klucza API, bezpłatny)
- Parametry hourly: temperature_2m, precipitation, wind_speed_10m,
  snowfall, visibility, cloud_cover, weather_code
- Pobieraj dla 20-30 głównych węzłów PKP (wczytaj z tabeli stations przez
  SQL: SELECT station_id, latitude, longitude FROM stations
       WHERE latitude IS NOT NULL ORDER BY name LIMIT 30)
- Retry 3x z backoff (identycznie jak PKPClient)
- Metody publiczne:
    get_current(station_id, lat, lon) -> dict
    get_forecast_48h(station_id, lat, lon) -> list[dict]

ZADANIE 2 — migrations/002_weather.sql:
CREATE TABLE weather_observations (
  id BIGSERIAL PRIMARY KEY,
  station_id VARCHAR(20),
  observed_at TIMESTAMPTZ NOT NULL,
  is_forecast BOOLEAN NOT NULL DEFAULT FALSE,
  temperature_c NUMERIC(5,2),
  precipitation_mm NUMERIC(6,2),
  wind_speed_kmh NUMERIC(6,2),
  snowfall_cm NUMERIC(6,2),
  visibility_m INTEGER,
  cloud_cover_pct SMALLINT,
  weather_code SMALLINT,
  collected_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (station_id, observed_at, is_forecast)
);
CREATE INDEX ON weather_observations (station_id, observed_at DESC);
CREATE INDEX ON weather_observations (observed_at) WHERE is_forecast = TRUE;

ZADANIE 3 — integracja z cns/collector/collector.py:
- Dodaj WeatherClient jako atrybut DataCollector
- Uruchamiaj co 1h (wzorzec: istniejąca obsługa disruptions co 60 min)
- Loguj liczbę pobranych obserwacji

ZADANIE 4 — cns/tests/test_weather.py:
- mockuj requests analogicznie do test_postgres.py
- testuj parsowanie odpowiedzi (podaj przykładowy JSON z Open-Meteo w fixtures)
- testuj obsługę błędów sieciowych (timeout, 5xx)
- testuj zapis do bazy przez mock psycopg3

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "WeatherClient" do DEVELOPMENT.md (wzorzec: istniejące sekcje modułów)
2. Dodaj schemat tabeli weather_observations do sekcji "Schemat bazy danych"
3. Zaktualizuj CONTEXT.md: przenieś zadanie do "Co działa ✅", usuń z backlogu
4. Dodaj "WeatherClient: co 1h" do tabeli harmonogramu w DEVELOPMENT.md
```

---

#### Zadanie 1.2 — CalendarService i tabela calendar_events

**Status:** ❌ Do zrobienia
**GitHub Issue:** #2
**Pliki wyjściowe:** `cns/collector/calendar_service.py`, `migrations/003_calendar.sql`, `tests/test_calendar.py`

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj CalendarService dla cyrk_na_szynach.

Stack: Python 3.12, Poetry, psycopg3, PostgreSQL 16.
Plik: cns/collector/calendar_service.py

ZADANIE 1 — Enum i typy (cns/models/records.py — dodaj):
class DayType(str, Enum):
    WORKING = "WORKING"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"
    HOLIDAY_EVE = "HOLIDAY_EVE"       # dzień przed świętem
    HOLIDAY_RETURN = "HOLIDAY_RETURN" # dzień po święcie
    WINTER_BREAK = "WINTER_BREAK"     # ferie zimowe
    SUMMER_BREAK = "SUMMER_BREAK"     # wakacje letnie (1 lip–31 sie)
    LONG_WEEKEND = "LONG_WEEKEND"     # "pomost" między świętem a weekendem

ZADANIE 2 — Logika polskiego kalendarza (cns/collector/calendar_service.py):

Święta ustawowe (statyczna lista):
  1 stycznia (Nowy Rok), 6 stycznia (Trzech Króli),
  Wielkanoc niedziela + poniedziałek (dynamiczne — algorytm Butchera/Meeusa),
  1 maja, 3 maja, Boże Ciało (Wielkanoc + 60 dni),
  15 sierpnia, 1 listopada, 11 listopada,
  25 i 26 grudnia

Ferie zimowe (różne tygodnie per strefa):
  Strefa A: dolnośląskie, opolskie, zachodniopomorskie, wielkopolskie
  Strefa B: kujawsko-pomorskie, lubuskie, łódzkie, małopolskie, świętokrzyskie, pomorskie
  Strefa C: lubelskie, mazowieckie, podkarpackie, podlaskie, śląskie, warmińsko-mazurskie
  (Daty per rok hardcode dla lat 2024-2030, potem update)

Metody publiczne:
  get_day_type(date: date, zone: str = "B") -> DayType
  is_long_weekend(date: date) -> bool
  days_to_next_holiday(date: date) -> int
  days_since_last_holiday(date: date) -> int
  get_season(date: date) -> str  # SPRING / SUMMER / AUTUMN / WINTER

ZADANIE 3 — migrations/003_calendar.sql:
CREATE TABLE calendar_events (
  event_date DATE NOT NULL,
  zone CHAR(1),              -- A, B, C lub NULL = cały kraj
  day_type VARCHAR(30) NOT NULL,
  event_name VARCHAR(100),
  PRIMARY KEY (event_date, COALESCE(zone, 'X'))
);
CREATE INDEX ON calendar_events (event_date);

ZADANIE 4 — bootstrap w DataCollector:
- Przy starcie (jednorazowo): wypełnij calendar_events na 5 lat naprzód
- Sprawdzaj czy tabela jest pusta przed insertem
- Uruchamiaj update raz w roku (1 stycznia)

ZADANIE 5 — cns/tests/test_calendar.py:
- Wielkanoc 2025 = 20 kwietnia, 2026 = 5 kwietnia, 2027 = 28 marca — weryfikuj
- Majówka 2025 (1 maja = czwartek → długi weekend 1-4 maja) — weryfikuj LONG_WEEKEND
- Ferie zimowe 2025 strefa A ≠ strefa B — weryfikuj różnicę
- Boże Ciało = Wielkanoc + 60 dni — weryfikuj

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "CalendarService" do DEVELOPMENT.md
2. Dodaj schemat tabeli calendar_events do sekcji "Schemat bazy danych"
3. Dodaj mapowanie stref ferii do dokumentacji (tabela województw)
4. Zaktualizuj CONTEXT.md
```

---

### FAZA 2 — Feature Store

#### Zadanie 2.1 — Widok mv_training_features

**Status:** ❌ Do zrobienia (wymaga: Fazy 1.1 i 1.2)
**GitHub Issue:** #3
**Pliki wyjściowe:** `migrations/004_features.sql`, rozszerzenie `cns/storage/postgres.py`

```
PROMPT — wklej do sesji `claude`:

Stwórz widok feature store do trenowania modelu ML w cyrk_na_szynach.

Kontekst: istniejące tabele to station_stops (~650k/dzień), train_operations,
operations_snapshots, weather_observations (Faza 1.1), calendar_events (Faza 1.2).

ZADANIE 1 — migrations/004_features.sql:

CREATE MATERIALIZED VIEW mv_training_features AS
SELECT
  ss.id,
  ss.station_id,
  st.name AS station_name,
  -- target variable
  ss.delay_departure_min,
  ss.delay_arrival_min,
  -- czas
  ss.planned_departure::date        AS operating_date,
  EXTRACT(HOUR FROM ss.planned_departure)::SMALLINT AS hour_of_day,
  EXTRACT(DOW FROM ss.planned_departure)::SMALLINT  AS day_of_week,
  EXTRACT(MONTH FROM ss.planned_departure)::SMALLINT AS month,
  -- kontekst kalendarzowy (strefa B jako default)
  ce.day_type,
  ce_b.day_type                     AS day_type_zone_b,
  -- opóźnienie propagacyjne: poprzedni przystanek tego pociągu
  LAG(ss.delay_departure_min) OVER (
    PARTITION BY ss.train_op_id ORDER BY ss.planned_sequence
  ) AS prev_stop_delay_min,
  -- numer przystanku na trasie
  ss.planned_sequence,
  ss.actual_sequence - ss.planned_sequence AS sequence_delta,
  -- pogoda: najbliższa obserwacja <= planowany odjazd (nie prognoza)
  wo.temperature_c,
  wo.precipitation_mm,
  wo.wind_speed_kmh,
  wo.snowfall_cm,
  wo.visibility_m,
  wo.cloud_cover_pct,
  wo.weather_code,
  -- flagi pogodowe (feature engineering w SQL)
  (wo.snowfall_cm > 1)::BOOLEAN     AS is_snowing,
  (wo.precipitation_mm > 5)::BOOLEAN AS is_heavy_rain,
  (wo.wind_speed_kmh > 70)::BOOLEAN AS is_strong_wind,
  (wo.temperature_c < -10)::BOOLEAN AS is_frost,
  (wo.visibility_m < 200)::BOOLEAN  AS is_dense_fog,
  -- metadane pociągu
  to_.train_status,
  snap.fetched_at AS snapshot_time
FROM station_stops ss
JOIN train_operations to_      ON ss.train_op_id = to_.id
JOIN operations_snapshots snap ON to_.snapshot_id = snap.id
LEFT JOIN stations st          ON ss.station_id = st.station_id::VARCHAR
LEFT JOIN LATERAL (
  SELECT * FROM weather_observations wo2
  WHERE wo2.station_id = ss.station_id
    AND wo2.observed_at <= ss.planned_departure
    AND wo2.is_forecast = FALSE
  ORDER BY wo2.observed_at DESC
  LIMIT 1
) wo ON TRUE
LEFT JOIN calendar_events ce
  ON ce.event_date = ss.planned_departure::date
  AND ce.zone IS NULL
LEFT JOIN calendar_events ce_b
  ON ce_b.event_date = ss.planned_departure::date
  AND ce_b.zone = 'B'
WHERE ss.delay_departure_min IS NOT NULL
  AND to_.train_status IN ('C', 'P');

CREATE UNIQUE INDEX ON mv_training_features (id);
CREATE INDEX ON mv_training_features (station_id, operating_date);
CREATE INDEX ON mv_training_features (operating_date);

ZADANIE 2 — cns/storage/postgres.py — dodaj metodę:
  def refresh_features(self) -> None:
      """REFRESH MATERIALIZED VIEW CONCURRENTLY mv_training_features"""
      -- wywołaj po każdym save_snapshot()

ZADANIE 3 — wywołanie w DataCollector:
  Po save_snapshot() → storage.refresh_features() (async, nie blokuj kolektora)

Testy (tests/test_features.py):
- sprawdź że JOIN na pogodę zwraca obserwację <= planned_departure (nie późniejszą)
- sprawdź że LAG działa (prev_stop_delay_min jest NULL dla pierwszego przystanku)
- sprawdź flagi binarne (is_snowing gdy snowfall_cm > 1)

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "Feature Store" do DEVELOPMENT.md z listą wszystkich kolumn i ich opisem
2. Dodaj mv_training_features do schematu bazy (sekcja "Widoki analityczne")
3. Opisz strategię odświeżania widoku (CONCURRENTLY, czas wykonania)
4. Zaktualizuj CONTEXT.md
```

---

### FAZA 3 — Model Predykcyjny

#### Zadanie 3.1 — BaselineModel (benchmark)

**Status:** ❌ Do zrobienia (wymaga: Fazy 2.1)
**GitHub Issue:** #4
**Pliki wyjściowe:** `cns/ml/baseline_model.py`, `cns/ml/train_baseline.py`, rozszerzenie `cns/api/app.py`

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj BaselineModel — model historycznych średnich jako benchmark dla ML.

Kontekst: cyrk_na_szynach, widok mv_training_features, FastAPI na porcie 8000.
Stwórz nowy podkatalog: cns/ml/__init__.py

ZADANIE 1 — cns/ml/baseline_model.py:

@dataclass
class BaselinePrediction:
    mean_delay: Optional[float]
    median_delay: Optional[float]
    p75_delay: Optional[float]
    p90_delay: Optional[float]
    sample_count: int
    fallback: bool  # True jeśli użyto globalnej średniej dla stacji

class BaselineModel:
    """
    Predykcja opóźnienia jako historyczna mediana
    per (station_id, hour_bucket, day_type).
    hour_bucket = hour // 2  (12 bucketów dziennie)
    """
    def fit(self, df: pd.DataFrame) -> None: ...
    def predict(self, station_id: str, hour: int, day_type: str) -> BaselinePrediction: ...
    def save(self, path: Path) -> None: ...     # joblib.dump
    @classmethod
    def load(cls, path: Path) -> "BaselineModel": ...

Fallback hierarchy:
  1. (station_id, hour_bucket, day_type) — dokładne
  2. (station_id, hour_bucket) — bez day_type
  3. (station_id,) — tylko stacja
  4. GlobalMedian — ostateczny fallback

ZADANIE 2 — cns/ml/train_baseline.py (skrypt CLI):
  - Pobierz dane z mv_training_features za ostatnie 90 dni
  - Podziel: train = pierwsze 72 dni, val = ostatnie 18 dni (po dacie, nie losowo)
  - Fit BaselineModel na train
  - Oblicz MAE i RMSE na val
  - Zapisz do models/baseline_v{YYYYMMDD}.pkl
  - Wydrukuj: MAE, RMSE, coverage (% predykcji z dokładnego bucketu)
  - Wywołanie: poetry run python -m cns.ml.train_baseline

ZADANIE 3 — nowy endpoint w cns/api/app.py:
  GET /predict/baseline
  Query params: station_id (str), planned_departure (ISO 8601), day_type (str, opcjonalny)
  Response:
  {
    "station_id": "...",
    "station_name": "...",
    "predicted_delay_min": 8.5,
    "p75_delay_min": 14.0,
    "p90_delay_min": 22.0,
    "sample_count": 342,
    "model": "baseline",
    "model_date": "2026-05-31",
    "fallback": false
  }
  Model ładuj przy starcie aplikacji (lifespan event), nie per request.

Testy (tests/test_baseline.py):
  - test fit/predict z 100 syntetycznymi wierszami
  - test fallback gdy brak danych dla stacji → GlobalMedian
  - test endpoint przez httpx.TestClient

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "ML — BaselineModel" do DEVELOPMENT.md
2. Wpisz osiągnięte MAE i RMSE jako wartości referencyjne (benchmark)
3. Zaktualizuj tabelę endpointów API w DEVELOPMENT.md
4. Dodaj komendę train_baseline do CONTEXT.md → sekcja Komendy
```

---

#### Zadanie 3.2 — XGBoostDelayPredictor

**Status:** ❌ Do zrobienia (wymaga: Zadania 3.1)
**GitHub Issue:** #5
**Pliki wyjściowe:** `cns/ml/xgb_model.py`, `cns/ml/train_xgb.py`, aktualizacja `cns/api/app.py`

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj XGBoostDelayPredictor dla cyrk_na_szynach.

Kontekst: istniejący BaselineModel (cns/ml/baseline_model.py) jako benchmark.
Nowe zależności — dodaj do pyproject.toml [tool.poetry.dependencies]:
  xgboost = ">=2.0"
  shap = ">=0.46"
  pandas = ">=2.0"
  scikit-learn = ">=1.4"

ZADANIE 1 — cns/ml/xgb_model.py:

FEATURES = [
    "hour_of_day", "day_of_week", "month", "planned_sequence",
    "prev_stop_delay_min",  # najważniejszy feature — propagacja opóźnienia
    "temperature_c", "precipitation_mm", "wind_speed_kmh",
    "snowfall_cm", "visibility_m",
    "is_snowing", "is_heavy_rain", "is_strong_wind", "is_frost", "is_dense_fog",
]
CATEGORICAL = ["station_id", "day_type"]

class XGBoostDelayPredictor:
    def fit(self, df: pd.DataFrame) -> dict:
        """
        Zwraca: {mae_train, mae_val, rmse_val, feature_importances: dict}
        Podział: po dacie — 80% train, 20% val (nie losowy — unikamy data leakage)
        """
        # Target encoding dla station_id i day_type
        # XGBRegressor(n_estimators=500, learning_rate=0.05, max_depth=6,
        #              subsample=0.8, colsample_bytree=0.8,
        #              early_stopping_rounds=20, eval_metric="mae")
        ...

    def predict(self, features: dict) -> float: ...

    def explain(self, features: dict) -> list[dict]:
        """SHAP values → top 5 features z impact w minutach"""
        # [{"feature": "prev_stop_delay_min", "impact": +8.3}, ...]
        ...

    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> "XGBoostDelayPredictor": ...

ZADANIE 2 — cns/ml/train_xgb.py:
  - Pobierz 180 dni z mv_training_features
  - Trenuj XGBoostDelayPredictor
  - WALIDACJA: val MAE musi być <= baseline MAE * 0.85 (min 15% poprawa)
    Jeśli nie — wydrukuj ostrzeżenie i NIE nadpisuj modelu produkcyjnego
  - Zapisz do models/xgb_v{YYYYMMDD}.pkl
  - Wydrukuj feature importance top-10 i porównanie z baseline

ZADANIE 3 — aktualizacja cns/api/app.py:
  Zastąp /predict/baseline jako domyślny model przez XGBoost.
  Zachowaj /predict/baseline jako osobny endpoint.

  GET /predict:
  Request: station_id, planned_departure (ISO8601), day_type (opcjonalny),
           prev_stop_delay_min (opcjonalny, default 0)
  Response:
  {
    "station_id": "...",
    "predicted_delay_min": 12.3,
    "p75_delay_min": 18.0,
    "confidence_interval": [8.0, 21.0],
    "model": "xgboost",
    "model_date": "2026-05-31",
    "explanation": [
      {"feature": "prev_stop_delay_min", "impact": +8.3, "value": 10},
      {"feature": "is_frost", "impact": +2.1, "value": true}
    ]
  }

Testy (tests/test_xgb.py):
  - overfitting test: val MAE nie może być >2x train MAE
  - test predict zwraca float
  - test explain zwraca listę z kluczami feature i impact
  - test endpoint

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "ML — XGBoostDelayPredictor" do DEVELOPMENT.md
2. Wpisz osiągnięte metryki (MAE, RMSE, % poprawa vs baseline)
3. Wpisz feature importance top-10 do dokumentacji
4. Zaktualizuj endpointy API w DEVELOPMENT.md (nowy /predict, zaktualizowany /predict/baseline)
5. Dodaj opis procesu trenowania i walidacji (data leakage prevention)
```

---

### FAZA 4 — Web Dashboard

#### Zadanie 4.1 — Inicjalizacja projektu Next.js

**Status:** ❌ Do zrobienia
**GitHub Issue:** #6
**Pliki wyjściowe:** katalog `dashboard/`, aktualizacja `docker-compose.yml`

```
PROMPT — wklej do sesji `claude`:

Zainicjuj projekt Next.js 15 App Router dla dashboardu cyrk_na_szynach.

Uruchom w katalogu root repo (obok cns/):
npx create-next-app@latest dashboard \
  --typescript --tailwind --app --src-dir --import-alias "@/*" --no-git

Następnie dodaj zależności:
cd dashboard
npm install recharts @tanstack/react-table lucide-react date-fns
npm install maplibre-gl react-map-gl
npm install -D @types/maplibre-gl

ZADANIE 1 — dashboard/src/lib/api.ts:
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export interface ActiveDelay { ... }   // mapowanie z cns/api/app.py ActiveDelay
export interface StationStat { ... }   // mapowanie z StationDelayStat

export const fetchActiveDelays = (limit = 50): Promise<ActiveDelay[]> => ...
export const fetchTopStations = (limit = 20): Promise<StationStat[]> => ...
export const fetchStats = (): Promise<Record<string, number>> => ...
export const fetchPrediction = (
  stationId: string, departure: string, prevDelay?: number
): Promise<PredictionResponse> => ...

ZADANIE 2 — Struktura src/app/:
  layout.tsx                — nagłówek: logo, linki Opóźnienia / Mapa / Predykcja
  page.tsx                  → redirect do /delays
  delays/page.tsx           — tablica aktualnych opóźnień
  map/page.tsx              — mapa Polski (placeholder na razie)
  predict/page.tsx          — widget predykcji (placeholder na razie)
  error.tsx                 — strona błędu
  loading.tsx               — skeleton loader

ZADANIE 3 — dashboard/.env.local:
  NEXT_PUBLIC_API_URL=http://localhost:8000

ZADANIE 4 — aktualizacja docker-compose.yml (root projektu):
  Dodaj serwis dashboard:
    build: ./dashboard
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_URL: http://fastapi:8000
    depends_on: [fastapi]
    restart: unless-stopped
  Dodaj Dockerfile do dashboard/ (multi-stage: build + runner)

ZADANIE 5 — nowy endpoint FastAPI (cns/api/app.py):
  GET /delays/stations/map
  → lista [{station_id, station_name, latitude, longitude,
            avg_delay_min, delay_rate_pct, total_stops}]
  Źródło: JOIN v_station_delay_stats ze stations (latitude, longitude)

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "Web Dashboard" do DEVELOPMENT.md
2. Dodaj komendy uruchomienia do CONTEXT.md (cd dashboard && npm run dev)
3. Dodaj endpoint /delays/stations/map do tabeli endpointów
4. Opisz strukturę katalogów Next.js w DEVELOPMENT.md
```

---

#### Zadanie 4.2 — Tablica opóźnień i mapa Polski

**Status:** ❌ Do zrobienia (wymaga: Zadania 4.1)
**GitHub Issue:** #7

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj dwa główne widoki dashboardu cyrk_na_szynach.

WIDOK 1 — dashboard/src/app/delays/page.tsx — Tablica opóźnień:
- Pobierz z GET /delays/active, odświeżaj co 60s (useEffect + setInterval)
- Użyj @tanstack/react-table do sortowania i filtrowania
- Kolumny: Stacja, Pociąg (scheduleId), Opóźnienie [min], Plan. odjazd, Aktualizacja
- Sortuj domyślnie po opóźnieniu malejąco
- Kolor wiersza: zielony <5 min, żółty 5–15 min, czerwony >15 min, szary odwołany
- Filtr tekstowy na nazwę stacji (input u góry)
- Licznik "Aktualnie opóźnionych: N" nad tabelą
- Auto-refresh badge z odliczaniem do kolejnego odświeżenia

WIDOK 2 — dashboard/src/app/map/page.tsx — Mapa Polski:
- MapLibre GL JS z bezpłatną mapą wektorową:
  style: "https://demotiles.maplibre.org/style.json"
- Dane z GET /delays/stations/map
- Punkty = stacje, rozmiar i kolor proporcjonalne do avg_delay_min
  Skala kolorów: zielony (0-3 min) → żółty (3-8 min) → pomarańczowy (8-15 min) → czerwony (>15 min)
- Tooltip po hover: nazwa stacji, śr. opóźnienie, % pociągów opóźnionych
- Legenda skali kolorów w rogu mapy
- Centruj mapę na Polsce: lng=19.1, lat=52.0, zoom=6

Wspólne komponenty (dashboard/src/components/):
  DelayBadge.tsx  — kolorowy badge z wartością opóźnienia
  LoadingSpinner.tsx
  ErrorBanner.tsx

Po implementacji wykonaj OBOWIĄZKOWO:
1. Zaktualizuj DEVELOPMENT.md — opisz widoki dashboardu (tablica i mapa)
2. Opisz konwencję kolorowania opóźnień (progi w minutach)
3. Wymień użyte biblioteki (recharts, maplibre-gl) w sekcji Stack
```

---

#### Zadanie 4.3 — Widget predykcji

**Status:** ❌ Do zrobienia (wymaga: Zadań 3.2 i 4.1)
**GitHub Issue:** #8

```
PROMPT — wklej do sesji `claude`:

Zaimplementuj stronę predykcji dla cyrk_na_szynach dashboard.

Plik: dashboard/src/app/predict/page.tsx

FORMULARZ:
- Autocomplete wyboru stacji (pobierz listę z GET /delays/stations/top?limit=200)
  Użyj prostego <datalist> lub komponentu Combobox z Tailwind
- DateTimePicker dla planowanego odjazdu (natywny <input type="datetime-local">)
- Number input: "Aktualne opóźnienie na poprzednim przystanku [min]" (default: 0)
- Przycisk "Przewiduj opóźnienie"

WYNIK PREDYKCJI:
- Główna liczba: przewidywane opóźnienie w minutach (duży font, kolorowy)
- Pasek: "50% szansa na mniej niż X min | 75% szansa na mniej niż Y min"
- Lista wyjaśnień SHAP (top 5 czynników):
  np. "🔴 Poprzednie opóźnienie: +8.3 min"
      "🌨 Opady śniegu: +2.1 min"
      "📅 Piątek wieczór: +1.8 min"
- Timestamp predykcji ("prognoza z: HH:MM")

HISTORIA PREDYKCJI:
- LocalStorage (lub React state) — ostatnie 5 predykcji w sesji
- Pokazuj jako mini-karty pod formularzem

Integracja FastAPI — rozszerz GET /predict o obsługę prev_stop_delay_min:
  query param: prev_stop_delay_min (int, opcjonalny, default 0)

Obsługa błędów:
- Jeśli model nie załadowany → 503 z komunikatem "Model w trakcie ładowania"
- Jeśli stacja nieznana → 404 z fallback na BaselineModel

Po implementacji wykonaj OBOWIĄZKOWO:
1. Zaktualizuj DEVELOPMENT.md — pełna dokumentacja endpointu /predict (request + response)
2. Dodaj przykładowy request i response JSON do dokumentacji
3. Opisz logikę SHAP explanation w dokumentacji
```

---

### FAZA 5 — Monitoring i Operacje

#### Zadanie 5.1 — Health monitoring kolektora

**Status:** ❌ Do zrobienia
**GitHub Issue:** #9

```
PROMPT — wklej do sesji `claude`:

Dodaj monitoring procesu kolekcjonowania danych do cyrk_na_szynach.

Cel: wykrywanie luk w danych (kolektor przestał działać, luki >20 min).

ZADANIE 1 — migrations/005_monitoring.sql:
CREATE TABLE collector_health (
  id SERIAL PRIMARY KEY,
  check_time TIMESTAMPTZ DEFAULT NOW(),
  last_snapshot_at TIMESTAMPTZ,
  minutes_since_snapshot INTEGER,
  snapshots_last_24h INTEGER,
  expected_snapshots_24h INTEGER DEFAULT 96,
  gaps JSONB,  -- [{from_time, to_time, gap_minutes}]
  status VARCHAR(20) CHECK (status IN ('OK','WARNING','CRITICAL'))
);
CREATE INDEX ON collector_health (check_time DESC);

ZADANIE 2 — cns/collector/health.py:
class HealthChecker:
  CRITICAL_THRESHOLD = 30   # minut bez snapshotu → CRITICAL
  WARNING_COVERAGE = 0.80   # <80% oczekiwanych snapshotów → WARNING

  def check(self) -> HealthStatus: ...
    # CRITICAL jeśli last_snapshot_at > 30 min temu
    # WARNING jeśli snapshots_last_24h < 77 (80% z 96)
    # Wykryj luki: SELECT fetched_at FROM operations_snapshots
    #              WHERE fetched_at > NOW() - INTERVAL '24h' ORDER BY fetched_at
    #              → oblicz przerwy między kolejnymi snapshotami

  def save_check(self, status: HealthStatus) -> None: ...

ZADANIE 3 — uruchomienie w DataCollector:
  Wywołuj health.check() co 5 minut (nowy harmonogram między istniejącymi)

ZADANIE 4 — endpoint FastAPI GET /health/collector:
  Response:
  {
    "status": "OK",
    "last_snapshot_at": "2026-05-31T14:30:00Z",
    "minutes_since_last_snapshot": 8,
    "snapshots_last_24h": 93,
    "expected_24h": 96,
    "coverage_pct": 96.9,
    "gaps_last_24h": [],
    "checked_at": "2026-05-31T14:38:00Z"
  }

Testy: sprawdź logikę progów CRITICAL/WARNING ze sztucznymi danymi.

Po implementacji wykonaj OBOWIĄZKOWO:
1. Dodaj sekcję "Monitoring" do DEVELOPMENT.md
2. Opisz progi alertów i co oznacza każdy status
3. Zaktualizuj tabelę endpointów API o /health/collector
4. Dodaj instrukcję monitoringu (np. curl /health/collector) do CONTEXT.md
```

---

## CZĘŚĆ B — Checklist: Hosting na Hetzner CPX21 + Domena

Wykonuj kroki po kolei. Zaznaczaj `[x]` po zakończeniu.

---

### KROK 1 — Konto Hetzner i serwer

- [ ] Załóż konto na hetzner.com (wymaga karty kredytowej lub PayPal)
- [ ] Wygeneruj klucz SSH lokalnie (WSL2 terminal):
  ```bash
  ssh-keygen -t ed25519 -C "cyrk-prod" -f ~/.ssh/cyrk_prod
  # Klucz publiczny: ~/.ssh/cyrk_prod.pub
  ```
- [ ] W Hetzner Console → Security → SSH Keys → Add SSH Key → wklej zawartość `cyrk_prod.pub`
- [ ] Utwórz serwer:
  - Typ: **CPX21** (2 vCPU, 4 GB RAM, 40 GB SSD NVMe, ~4.5 EUR/mies.)
  - OS: **Ubuntu 24.04 LTS**
  - Lokalizacja: Nuremberg (nbg1) lub Helsinki (hel1)
  - Wybrany SSH Key: cyrk-prod
  - Nazwa: cyrk-na-szynach
- [ ] Zanotuj publiczne IP serwera: `__________________`
- [ ] Pierwsze logowanie:
  ```bash
  ssh -i ~/.ssh/cyrk_prod root@<IP>
  # Jeśli połączenie działa → krok 1 gotowy
  ```

---

### KROK 2 — Domena

- [ ] Zarejestruj domenę na **domeny.pl** (lub ovh.pl) — wybierz krótką nazwę projektu
- [ ] Opłać rejestrację (~15–30 PLN/rok dla domeny .pl)
- [ ] Zanotuj dane logowania do panelu rejestratora
- [ ] Zanotuj nazwę domeny: `__________________`

---

### KROK 3 — DNS przez Cloudflare

- [ ] Załóż konto na cloudflare.com (free tier, bez karty)
- [ ] W Cloudflare → Add a Site → wpisz domenę → wybierz plan Free
- [ ] Skopiuj 2 nameservery Cloudflare (np. `ada.ns.cloudflare.com`)
- [ ] W panelu domeny.pl → Zmień nameservery → wklej nameservery z Cloudflare
  - Propagacja DNS: do 24h, zwykle 1–2h
- [ ] Po propagacji w Cloudflare → DNS → Add record:
  ```
  Typ: A | Name: @   | Content: <IP serwera>  | Proxy: ON (🟠)
  Typ: A | Name: www | Content: <IP serwera>  | Proxy: ON (🟠)
  ```
- [ ] Cloudflare → SSL/TLS → Overview → ustaw **Full (strict)**
- [ ] Sprawdź propagację: `nslookup twojadomena.pl` → powinno zwrócić IP serwera

---

### KROK 4 — Konfiguracja serwera

Połącz się przez SSH i wykonaj sekwencyjnie:

```bash
# Aktualizacja systemu
apt update && apt upgrade -y

# Docker (oficjalny skrypt)
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Weryfikacja
docker --version
docker compose version

# Firewall
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"
ufw --force enable
ufw status

# Użytkownik aplikacyjny
adduser cyrk --disabled-password --gecos ""
usermod -aG docker cyrk
mkdir -p /home/cyrk/.ssh
cp ~/.ssh/authorized_keys /home/cyrk/.ssh/
chown -R cyrk:cyrk /home/cyrk/.ssh
chmod 700 /home/cyrk/.ssh
chmod 600 /home/cyrk/.ssh/authorized_keys
```

- [ ] `apt update && upgrade` wykonany
- [ ] Docker zainstalowany i uruchomiony
- [ ] UFW skonfigurowany (22, 80, 443 open)
- [ ] Użytkownik `cyrk` z dostępem do Docker
- [ ] Test logowania jako cyrk: `ssh -i ~/.ssh/cyrk_prod cyrk@<IP>`

---

### KROK 5 — GitHub Deploy Key

```bash
# NA SERWERZE, zalogowany jako cyrk:
su - cyrk
ssh-keygen -t ed25519 -C "cyrk-deploy-readonly" -f ~/.ssh/deploy_key -N ""
cat ~/.ssh/deploy_key.pub
# Skopiuj wydrukowany klucz publiczny

# Skonfiguruj SSH żeby używał tego klucza dla GitHub:
cat >> ~/.ssh/config << 'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/deploy_key
  StrictHostKeyChecking no
EOF
chmod 600 ~/.ssh/config
```

- [ ] Wygeneruj deploy key na serwerze (powyższe komendy)
- [ ] W GitHub → repo → Settings → Deploy keys → Add deploy key
  - Title: "Hetzner CPX21 prod"
  - Key: wklej zawartość `deploy_key.pub`
  - Allow write access: **NIE** (read-only)
- [ ] Test połączenia z GitHub: `ssh -T git@github.com`
  - Oczekiwany wynik: `Hi USER/cyrk_na_szynach! You've successfully authenticated`
- [ ] Sklonuj repo:
  ```bash
  git clone git@github.com:<TWOJ_USER>/cyrk_na_szynach.git /home/cyrk/app
  ```

---

### KROK 6 — Plik .env na serwerze

Nie wrzucaj .env do gita — przekopiuj bezpiecznie z lokalnej maszyny:

```bash
# Z WSL2 na lokalnym komputerze:
scp -i ~/.ssh/cyrk_prod cyrk_na_szynach/.env cyrk@<IP>:/home/cyrk/app/.env
```

- [ ] .env skopiowany na serwer
- [ ] Sprawdź DATABASE_URL — zmień `localhost` na nazwę serwisu Docker (`db`):
  ```
  DATABASE_URL=postgresql://cyrk_na_szynach:HASLO@db:5432/cyrk_na_szynach
  ```
- [ ] Dodaj do .env:
  ```
  DOMAIN=twojadomena.pl
  ```
- [ ] Sprawdź że PKP_API_KEY jest ustawiony poprawnie

---

### KROK 7 — Docker Compose w produkcji

Upewnij się że `docker-compose.yml` w repo ma następujące serwisy:

- [ ] Serwis `db` (PostgreSQL 16) z named volume dla danych:
  ```yaml
  volumes:
    - postgres_data:/var/lib/postgresql/data
  ```
- [ ] Serwis `collector` z `restart: unless-stopped` i zależnością od `db`
- [ ] Serwis `fastapi` z `restart: unless-stopped`, port 8000 dostępny wewnętrznie
- [ ] Serwis `dashboard` (po zbudowaniu Fazy 4) z `restart: unless-stopped`
- [ ] Serwis `nginx` (na razie tylko jako reverse proxy dla fastapi, bez dashboard)
- [ ] Zdefiniowany volume `postgres_data` na końcu pliku
- [ ] Wszystkie serwisy z `restart: unless-stopped`

```bash
# NA SERWERZE jako cyrk:
cd /home/cyrk/app
docker compose up -d --build
docker compose logs -f   # obserwuj przez 2-3 minuty
docker compose ps        # wszystkie serwisy powinny być "Up"
```

- [ ] `docker compose ps` — wszystkie serwisy "Up"
- [ ] Kolektor loguje pobrane dane (widać w `docker compose logs collector`)
- [ ] FastAPI odpowiada: `curl http://localhost:8000/` → `{"status": "ok"}`

---

### KROK 8 — Nginx + SSL (Let's Encrypt)

```bash
# NA SERWERZE jako root:
apt install certbot -y

# Tymczasowo zatrzymaj nginx żeby certbot mógł użyć portu 80:
docker compose stop nginx 2>/dev/null || true

# Pobierz certyfikat:
certbot certonly --standalone \
  -d twojadomena.pl \
  -d www.twojadomena.pl \
  --non-interactive --agree-tos -m twoj@email.pl

# Certyfikaty w: /etc/letsencrypt/live/twojadomena.pl/
```

Konfiguracja Nginx (plik `nginx/nginx.conf` w repo):
```nginx
server {
    listen 80;
    server_name twojadomena.pl www.twojadomena.pl;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name twojadomena.pl www.twojadomena.pl;

    ssl_certificate /etc/letsencrypt/live/twojadomena.pl/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/twojadomena.pl/privkey.pem;

    location /api/ {
        proxy_pass http://fastapi:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location / {
        proxy_pass http://dashboard:3000;
        proxy_set_header Host $host;
    }
}
```

Zamontuj certyfikaty w serwisie nginx w docker-compose.yml:
```yaml
nginx:
  volumes:
    - /etc/letsencrypt:/etc/letsencrypt:ro
    - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
```

- [ ] Certyfikat pobrany bez błędów
- [ ] Konfiguracja nginx.conf dodana do repo (z podmienioną domeną)
- [ ] `docker compose up -d nginx` — nginx uruchomiony z SSL
- [ ] Test w przeglądarce: `https://twojadomena.pl` → kłódka SSL ✅
- [ ] Auto-renewal: `certbot renew --dry-run` → "Congratulations, all simulated renewals succeeded"
- [ ] Dodaj do crontab (root): `0 3 * * * certbot renew --quiet && docker compose -f /home/cyrk/app/docker-compose.yml restart nginx`

---

### KROK 9 — Migracje bazy i weryfikacja

```bash
# NA SERWERZE jako cyrk:
cd /home/cyrk/app

# Wykonaj migrację początkową:
docker exec -i $(docker compose ps -q db) \
  psql -U cyrk_na_szynach -d cyrk_na_szynach \
  < migrations/001_initial_schema.sql

# Sprawdź statystyki (po ~15 minutach zbierania danych):
docker compose exec fastapi poetry run cns db-stats

# Sprawdź API:
curl https://twojadomena.pl/api/stats
curl https://twojadomena.pl/api/delays/active
```

- [ ] Migracja 001 wykonana pomyślnie
- [ ] `db-stats` pokazuje stacje i przewoźników (bootstrap się wykonał)
- [ ] Po 15 minutach: snapshots_count > 0
- [ ] API dostępne publicznie pod domeną

---

### KROK 10 — Deployment workflow i monitoring uptime

**Aktualizacja kodu (deploy):**
```bash
# NA SERWERZE jako cyrk (lub przez GitHub Actions):
cd /home/cyrk/app
git pull origin main
docker compose up -d --build
```

- [ ] Dodaj alias do ~/.bashrc użytkownika cyrk:
  ```bash
  alias deploy='cd /home/cyrk/app && git pull && docker compose up -d --build'
  ```
- [ ] Załóż konto na **UptimeRobot** (uptimerobot.com, free tier):
  - Monitor typ HTTP(S), URL: `https://twojadomena.pl/api/health/collector`
  - Sprawdzaj co 5 minut
  - Alert email gdy status != 200
- [ ] Backup bazy (dodaj do crontab cyrk):
  ```bash
  0 2 * * * docker exec $(docker compose -f ~/app/docker-compose.yml ps -q db) \
    pg_dump -U cyrk_na_szynach cyrk_na_szynach | gzip \
    > ~/backups/backup_$(date +\%Y\%m\%d).sql.gz
  ```
- [ ] Utwórz katalog `~/backups` i sprawdź że backup działa

---

## Podsumowanie faz i zależności

| Faza | Zadanie | Wymaga | Status |
|------|---------|--------|--------|
| 1.1 | WeatherClient | — | ❌ |
| 1.2 | CalendarService | — | ❌ |
| 2.1 | Feature Store (widok) | 1.1 + 1.2 | ❌ |
| 3.1 | BaselineModel | 2.1 | ❌ |
| 3.2 | XGBoost ML | 3.1 | ❌ |
| 4.1 | Next.js setup | — | ❌ |
| 4.2 | Tablica + mapa | 4.1 | ❌ |
| 4.3 | Widget predykcji | 4.1 + 3.2 | ❌ |
| 5.1 | Health monitoring | — | ❌ |

**Dobre punkty startowe (bez zależności):** Faza 1.1, 1.2, 4.1 i 5.1 możesz zacząć równolegle.

---

*cyrk_na_szynach | Plan wygenerowany: 2026-05-31*
