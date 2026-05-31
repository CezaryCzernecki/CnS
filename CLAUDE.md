# cyrk_na_szynach — kontekst dla Claude Code

System kolekcjonowania i archiwizacji danych o opóźnieniach pociągów PKP PLK w czasie
rzeczywistym. Źródło: oficjalne API `pdp-api.plk-sa.pl` (plan Basic: 100/h, 1000/dzień).
Cel docelowy: predykcja opóźnień + web dashboard.

---

## Stack

- Python 3.12 + Poetry | psycopg3 | PostgreSQL 16 (Docker) | requests
- FastAPI + uvicorn | pytest + unittest.mock
- Środowisko: WSL2 Ubuntu + PyCharm
- Planowane: xgboost, pandas, shap | Next.js 15 (dashboard/)

---

## Struktura projektu

```
cyrk_na_szynach/
├── pyproject.toml
├── .env                         ← PKP_API_KEY + DATABASE_URL (NIE w git)
├── migrations/
│   └── 001_initial_schema.sql   ← tabele, indeksy, widoki
└── cns/
    ├── __main__.py              ← CLI entry point
    ├── api/app.py               ← FastAPI: /delays/stations/top, /delays/active, /stats
    ├── collector/
    │   ├── client.py            ← PKPClient: HTTP, retry 3x, rate-limit headers
    │   ├── parser.py            ← JSON → dataclasses (defensywny, .get() wszędzie)
    │   └── collector.py         ← DataCollector: harmonogram co 15/60 min / 1 dzień
    ├── models/records.py        ← StationStop, TrainOperation, OperationsSnapshot
    ├── storage/postgres.py      ← PostgresStorage: batch insert przez unnest
    └── tests/
        ├── test_parser.py
        └── test_postgres.py     ← wzorzec mockowania psycopg3
```

---

## Komendy

```bash
poetry run cns --once --verbose      # jednorazowe pobranie
poetry run cns --verbose             # tryb ciągły co 15 min
poetry run cns db-init               # wykonaj migracje SQL
poetry run cns db-stats              # statystyki tabel
poetry run cns api-serve             # FastAPI na 127.0.0.1:8000
poetry run cns api-serve --reload    # tryb dev z hot-reload
poetry run pytest -v                 # wszystkie testy
poetry run pytest cns/tests/test_postgres.py -v

# Bezpośrednio do bazy:
docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach < plik.sql
```

---

## Wzorce kodowania — naśladuj istniejące pliki

**Nowy klient HTTP** → wzorzec: `cns/collector/client.py`
- Autoryzacja przez nagłówek `X-API-Key`
- Retry 3x dla błędów 5xx (backoff 2s, 4s, 8s z `time.sleep`)
- Śledzenie limitów z `X-RateLimit-Hourly-Remaining` / `X-RateLimit-Daily-Remaining`
- `RateLimitError` z polem `retry_after`

**Nowa metoda storage** → wzorzec: `cns/storage/postgres.py`
- Połączenie per operacja: `with _conn(self._db_url) as conn:`
- Bulk insert przez `unnest` zamiast pętli (1 round-trip na 10k rekordów)
- Walidacja danych w Pythonie przed otwarciem połączenia

**Nowe testy** → wzorzec: `cns/tests/test_postgres.py`
```python
@pytest.fixture
def storage():
    with patch.object(PostgresStorage, "_verify_connection"):
        return PostgresStorage("postgresql://test/testdb")

def test_cokolwiek(storage):
    mock_conn, mock_cursor = _make_conn_mock()
    with patch("cns.storage.postgres._conn", return_value=mock_conn):
        storage.metoda(...)
    mock_cursor.execute.assert_called_once()
```

**Storage Protocol** (`cns/storage/postgres.py`):
```python
class Storage(Protocol):
    def save_snapshot(self, snapshot: OperationsSnapshot) -> None: ...
    def save_disruptions(self, raw: dict) -> None: ...  # surowy JSON
    def save_schedules(self, raw: dict) -> None: ...    # surowy JSON
    def save_raw(self, name: str, data: dict) -> None: ...
```

---

## Schemat bazy (skrót)

```
stations          (station_id PK, name, short_name, latitude, longitude)
carriers          (code PK, name)
operations_snapshots (id, data_version, fetched_at, total_trains, total_stops)
train_operations  (id, snapshot_id FK, schedule_id, order_id, operating_date, train_status)
station_stops     (id, train_op_id FK, station_id,
                   planned_arrival/departure, actual_arrival/departure,
                   delay_arrival_min, delay_departure_min)
                   ← GŁÓWNA TABELA: ~650k rekordów/dzień
disruptions       (id, disruption_id, message, collected_at, collected_date DATE)
schedules         (id, schedule_id, order_id, carrier_code, operating_date)
schedule_stops    (id, schedule_id FK, station_id FK, arrival_time, departure_time)

Widoki:
  v_active_delays       — pociągi status P z opóźnieniami
  v_station_delay_stats — statystyki per stacja (7 dni, min. 10 pomiarów)
```

```
weather_observations  (id, station_id, observed_at, is_forecast,
                       temperature_c, precipitation_mm, wind_speed_kmh,
                       snowfall_cm, visibility_m, cloud_cover_pct,
                       weather_code, collected_at)
                       UNIQUE (station_id, observed_at, is_forecast)
```

```
calendar_events   (id, event_date, zone CHAR(1), day_type, event_name)
                   UNIQUE NULLS NOT DISTINCT (event_date, zone)
```

**Nowe tabele (planowane, jeszcze nie istnieją):**
```
mv_training_features   ← Faza 2.1 (widok zmaterializowany)
collector_health       ← Faza 5.1
```

---

## KRYTYCZNE ustalenia empiryczne — NIE ignoruj

1. **Klucz `trains[]`** — endpoint `/operations` zwraca `trains[]`, nie `operations[]`
2. **`trainNumber` i `carrierCode` niedostępne** w `/operations` — są tylko w `/schedules`.
   Łączymy po `(schedule_id, order_id, operating_date)`.
3. **ID jako int w JSON** — `stationId`, `scheduleId`, `orderId` to `int` w JSON → castuj na `str`
4. **Opóźnienia z różnicy** — API nie zwraca gotowych wartości; liczymy `actual - planned`
5. **Anomalie >200 min** — przesunięcia rozkładowe (dobowe), nie prawdziwe opóźnienia.
   Filtrowane przez `MAX_REALISTIC_DELAY = 200` w `StationStop`
6. **Stacje spoza słownika** — FK na `station_stops.station_id` jest `ON DELETE SET NULL`
   (API zwraca stacje których nie ma w `/dictionaries/stations`)
7. **Dwa osobne liczniki godzinowe** — carriers używa innego (widoczne w logach: 1966 vs 99)
8. **Batch insert bez SAVEPOINT** — błędne rekordy filtrowane w Pythonie przed połączeniem;
   jeśli `unnest INSERT` rzuci wyjątek, cały snapshot jest wycofywany (rollback)
9. **10000 rekordów limit** — API zwraca max 10k pociągów/stronę; paginacja niezbadana
10. **`stations` w odpowiedzi** — to słownik `{id: nazwa}`, nie lista; na poziomie głównym JSON

---

## Stan projektu

### Działa ✅
- Kolekcjonowanie RT co 15 min (10k pociągów/snapshot)
- Batch insert przez `unnest` (<10s dla 10k × 17 przystanków)
- Słowniki stacji i przewoźników (upsert)
- Rozkład planowy (raz dziennie po 04:00)
- Utrudnienia (co 60 min)
- Obsługa rate-limit + czekanie na kolejną godzinę
- Filtrowanie anomalii >200 min
- FastAPI: `/delays/stations/top`, `/delays/active`, `/stats`
- Testy: `test_parser.py` + `test_postgres.py` + `test_weather.py` (mocki psycopg3 + requests)
- WeatherClient (Open-Meteo): pobieranie pogody co 1h dla 30 stacji PKP
- CalendarService: klasyfikacja dni (HOLIDAY/WEEKEND/LONG_WEEKEND/WINTER_BREAK/SUMMER_BREAK)
- Feature Store `mv_training_features`: LATERAL weather + LAG + flagi binarne, REFRESH CONCURRENTLY
- Testy: 166 łącznie (parser + postgres + weather + calendar + features)

### Backlog — kolejność implementacji

| Faza | Zadanie | Zależności | Status |
|------|---------|------------|--------|
| 1.1 | WeatherClient + `weather_observations` | — | ✅ |
| 1.2 | CalendarService + `calendar_events` | — | ✅ |
| 2.1 | Feature Store (`mv_training_features`) | 1.1 + 1.2 | ✅ |
| 3.1 | BaselineModel + `/predict/baseline` | 2.1 | ✅ |
| 3.2 | XGBoostDelayPredictor + `/predict` | 3.1 | ✅ |
| 4.1 | Next.js setup (`dashboard/`) | — | ✅ |
| 4.2 | Tablica opóźnień + mapa Polski | 4.1 | ❌ |
| 4.3 | Widget predykcji | 4.1 + 3.2 | ❌ |
| 5.1 | Health monitoring + `/health/collector` | — | ❌ |

Gotowe prompty dla każdego zadania: `cyrk_na_szynach_plan.md`

---

## Dokumentacja — aktualizuj po każdym zadaniu

Po zaimplementowaniu każdego zadania wykonaj OBOWIĄZKOWO:

1. **`DEVELOPMENT.md`** — dodaj sekcję nowego modułu (opis, schemat tabel, gotcha)
2. **`CLAUDE.md` (ten plik)**:
   - Przenieś zadanie z `❌` na `✅` w tabeli backlogu
   - Dodaj nowe tabele do sekcji "Schemat bazy"
   - Dodaj nowe komendy do sekcji "Komendy" jeśli nowe skrypty
3. **`README.md`** — zaktualizuj sekcję "FastAPI — endpointy" przy nowych endpointach
4. `git commit -m "feat: [opis] + docs"`
