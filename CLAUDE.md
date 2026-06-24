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
    ├── api/app.py               ← FastAPI: /delays/*, /rankings/*, /stats, /predict
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
disruptions       (id, disruption_id, message, disruption_type_code,
                   start_station_id FK, end_station_id FK,
                   has_bus_replacement BOOL, collected_at, collected_date DATE)
schedules         (id, schedule_id, order_id, carrier_code, operating_date)
schedule_stops    (id, schedule_id FK, station_id FK, arrival_time, departure_time)
```

**Hot/Cold Storage (migracja 023–024, czerwiec 2026):**
```
train_runs        (id PK SERIAL, schedule_id, order_id, operating_date)
                   UNIQUE (schedule_id, order_id, operating_date)
                   ← klucz kursów; zastępuje train_operations jako FK

station_stops_hot (id BIGSERIAL PK, train_run_id FK, station_id FK,
                   planned_sequence, planned_arrival/departure,
                   actual_arrival/departure,
                   delay_arrival_min, delay_departure_min SMALLINT,
                   is_confirmed, is_cancelled, last_seen_at)
                   UNIQUE NULLS NOT DISTINCT (train_run_id, station_id)
                   ← GŁÓWNA TABELA: ostatnie 3 dni, UPSERT per kurs×stacja

station_stops_archive (train_run_id, station_id NOT NULL, operating_date,
                       actual_arrival/departure,
                       delay_arrival_min, delay_departure_min SMALLINT,
                       is_cancelled)
                   PRIMARY KEY (operating_date, train_run_id, station_id)
                   PARTITION BY RANGE (operating_date) — partycje miesięczne
                   ← historia >3 dni, tylko faktyczne pomiary

Widoki:
  v_station_stops       — UNION ALL hot + archive (ujednolicony dostęp)
  v_active_delays       — aktywne pociągi z opóźnieniami (na station_stops_hot)
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

**Widoki zmaterializowane:**
```
mv_training_features   — cechy ML: station_stops_hot × weather × calendar
mv_train_run_delays    — max opóźnienie per kurs (v_station_stops)
mv_cancelled_runs      — odwołane kursy per dzień × przewoźnik
collector_health       — zdrowie kolektora
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
6. **Stacje spoza słownika** — FK na `station_stops_hot.station_id` jest `ON DELETE SET NULL`
   (API zwraca stacje których nie ma w `/dictionaries/stations`); w archive `station_id NOT NULL`
   — wiersze z NULL station_id filtrowane przez `archive_hot_data()` przed INSERT do archive
7. **Dwa osobne liczniki godzinowe** — carriers używa innego (widoczne w logach: 1966 vs 99)
8. **UPSERT do station_stops_hot** — jeden wiersz per (train_run_id, station_id);
   kolektor nadpisuje przy kolejnych snapshotach (`ON CONFLICT DO UPDATE`);
   walidacja ID w Pythonie przed połączeniem; pociąg z błędnym ID jest pomijany w całości
9. **10000 rekordów limit** — API zwraca max 10k pociągów/stronę; paginacja niezbadana
10. **`stations` w odpowiedzi** — to słownik `{id: nazwa}`, nie lista; na poziomie głównym JSON
11. **`/schedules` wymaga jawnego `pageSize=10000`** — bez tego parametru API zwraca domyślnie
    ograniczoną liczbę tras (~6900 z ~7200), reszta pociągów trafia do `/operations` bez
    numeru i nazwy. Parametr dodany do `PKPClient.get_schedules()`.
12. **`commercial_category` FK blokuje cały rekord rozkładu** — trasy z kategorią nieobecną
    w `commercial_categories` rzucały FK violation i były w całości pomijane (tracąc
    `national_number`). Naprawione subquery `(SELECT symbol FROM commercial_categories WHERE symbol = %s)`,
    które zwraca NULL zamiast błędu.
13. **Rozkłady pobierane przy każdym starcie collectora** — usunięty guard `hour < 4`
    i dodane wywołanie w `_bootstrap()`. Bez tego: po restarcie collectora w nocy
    pociągi nie miały numerów aż do 04:00.
14. **Pociągi nocne bez numeru/nazwy** — `operating_date` pociągu startującego przed północą
    to data wczorajsza. Po restarcie collectora JOIN `sc.operating_date = to_.operating_date`
    nie trafiał, bo `_fetch_schedules_if_needed` pobierał tylko `date_from=today`.
    Naprawione: zakres `date_from=yesterday, date_to=today` (jeden request API).
15. **Widok `v_active_delays` filtruje po dacie** (migracja 015→016) — tylko pociągi z
    `operating_date >= CURRENT_DATE` (wyłącznie dzisiaj). Wczorajsze wpisy (artefakty
    starych snapshotów) są odfiltrowywane. Migracja 016 jest self-contained — dodaje
    brakujące kolumny IF NOT EXISTS (train_name, is_confirmed, is_cancelled).
16. **VACUUM nie zwalnia miejsca do OS** — zwykły `VACUUM` tylko oznacza strony jako wolne
    wewnątrz pliku tabeli. Fizyczne miejsce wraca do systemu wyłącznie przez `DROP TABLE`
    (natychmiastowe) lub `VACUUM FULL` (wymaga 2× wolnego miejsca). Przy migracji historycznej
    należy najpierw zarchiwizować wszystkie dni (INSERT-only), a dopiero potem DROP TABLE.
17. **`PARALLEL 0` w VACUUM w kontenerze Docker** — domyślny `/dev/shm` Dockera to ~64 MB;
    próba `SET maintenance_work_mem='512MB'` + VACUUM powoduje błąd "No space left on device"
    przy alokacji DSM. Zawsze używaj `VACUUM (ANALYZE, PARALLEL 0)` wewnątrz kontenera.
18. **Hot/Cold Storage — save_snapshot** — kolektor pisze wyłącznie do `train_runs` +
    `station_stops_hot`; stare tabele `station_stops` i `train_operations` zostały usunięte
    (czerwiec 2026). `archive_hot_data(retention_days=3)` przenosi dane starsze niż 3 dni
    do `station_stops_archive` i usuwa je z hot; wywoływany raz dziennie przez collector.

---

## Stan projektu

### Działa ✅
- Kolekcjonowanie RT co 15 min — UPSERT do `station_stops_hot` (1 wiersz/kurs×stacja)
- Słowniki stacji i przewoźników (upsert)
- Rozkład planowy (przy starcie + raz dziennie, `pageSize=10000`)
- Utrudnienia (co 60 min)
- Obsługa rate-limit + czekanie na kolejną godzinę
- Filtrowanie anomalii >200 min
- FastAPI: `/delays/stations/top`, `/delays/active`, `/stats`, `/rankings/*`
- WeatherClient (Open-Meteo): pobieranie pogody co 1h dla 30 stacji PKP
- CalendarService: klasyfikacja dni (HOLIDAY/WEEKEND/LONG_WEEKEND/WINTER_BREAK/SUMMER_BREAK)
- Feature Store `mv_training_features`: LATERAL weather + LAG + flagi binarne, REFRESH CONCURRENTLY
- Testy: 298 łącznie (parser + postgres + weather + calendar + features + collector + api_rankings)
- Filtr widoku `v_active_delays` (migracja 016→024): tylko CURRENT_DATE, hot storage
- Kolektor pobiera rozkłady yesterday–today: pociągi nocne mają numery po restarcie
- Rankingi: 4 zakładki w dashboardzie (wszech czasów / dzienny / miesięczny pociągi / miesięczny spółki)
- **Hot/Cold Storage** (migracja 023–024, czerwiec 2026): `train_runs` + `station_stops_hot`
  (3 dni) + `station_stops_archive` (partycje miesięczne); stare tabele usunięte (60 GB → 251 MB)

### Backlog — kolejność implementacji

| Faza | Zadanie | Zależności | Status |
|------|---------|------------|--------|
| 1.1 | WeatherClient + `weather_observations` | — | ✅ |
| 1.2 | CalendarService + `calendar_events` | — | ✅ |
| 2.1 | Feature Store (`mv_training_features`) | 1.1 + 1.2 | ✅ |
| 3.1 | BaselineModel + `/predict/baseline` | 2.1 | ✅ |
| 3.2 | XGBoostDelayPredictor + `/predict` | 3.1 | ✅ |
| 4.1 | Next.js setup (`dashboard/`) | — | ✅ |
| 4.2 | Tablica opóźnień + mapa Polski | 4.1 | ✅ |
| 4.3 | Widget predykcji | 4.1 + 3.2 | ✅ |
| 5.1 | Health monitoring + `/health/collector` | — | ✅ |

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
