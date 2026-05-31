# cyrk_na_szynach – kontekst projektu dla Claude

## Czym jest projekt

System kolekcjonowania i archiwizacji danych o opóźnieniach pociągów PKP PLK w czasie rzeczywistym.
Źródło danych: oficjalne API PKP PLK (`pdp-api.plk-sa.pl`), plan Basic (100/h, 1000/dzień).

## Stack techniczny

- **Python 3.12**, **Poetry** (zarządzanie zależnościami)
- **psycopg3** (PostgreSQL driver)
- **PostgreSQL 16** w Dockerze
- **requests** (HTTP client z retry)
- **FastAPI + uvicorn** (REST API)
- Środowisko: **WSL2 Ubuntu**, PyCharm

## Struktura projektu

```
cyrk_na_szynach/          ← katalog projektu (pyproject.toml TUTAJ)
├── pyproject.toml
├── .env                    ← PKP_API_KEY + DATABASE_URL (nie w git)
├── migrations/
│   └── 001_initial_schema.sql
└── cns/
    ├── api/
    │   └── app.py          ← FastAPI: GET /delays/stations/top, /delays/active, /stats
    ├── collector/
    │   ├── client.py       ← klient HTTP PKP API (retry, rate-limit)
    │   ├── parser.py       ← JSON → dataclasses
    │   └── collector.py    ← orkiestrator (harmonogram co 15 min)
    ├── models/
    │   └── records.py      ← dataclasses: TrainOperation, StationStop, itp.
    ├── storage/
    │   └── postgres.py     ← zapis do PostgreSQL (batch insert przez unnest)
    ├── tests/
    │   ├── test_parser.py  ← testy jednostkowe parsera
    │   └── test_postgres.py ← testy storage z mockami psycopg
    └── __main__.py         ← CLI: --once, --verbose, db-init, db-stats, api-serve
```

## Stan bazy danych (2026-05-29)

```
stations:       3,259   ← słownik stacji PKP PLK
carriers:          22   ← przewoźnicy (IC, KM, PR, ...)
snapshots:          3   ← każde pobranie /operations
train_operations: ~20k  ← pociągi z każdego snapshotu
station_stops:   ~344k  ← przystanki z opóźnieniami (główna tabela)
disruptions:       317  ← utrudnienia w ruchu
```

## Kluczowe ustalenia empiryczne (ważne!)

**Struktura API (zweryfikowana na rzeczywistych danych):**
- `/operations` zwraca klucz `trains[]` (nie `operations[]`)
- Każdy pociąg ma zagnieżdżoną listę `stations[]` (przystanki)
- `stations` na poziomie głównym = słownik `{id: nazwa}` stacji
- `trainNumber` i `carrierCode` są NIEDOSTĘPNE w `/operations` (są w `/schedules`)
- `stationId`, `scheduleId`, `orderId` to `int` w JSON (castujemy na `str`)
- Opóźnienia liczone z `actual - planned` (API nie zwraca gotowych wartości)
- Anomalie: różnice >200 min to przesunięcia rozkładowe, nie opóźnienia

**Statusy pociągów (trainStatus):**
- `S` = scheduled (zaplanowany)
- `P` = in progress (w trasie) ← jedyne istotne dla RT
- `C` = completed (zakończony)
- `X` = cancelled (odwołany)
- `Q` = unknown edge case

**Baza danych:**
- `UNIQUE (disruption_id, collected_date)` — `collected_date` to osobna kolumna DATE
- FK na `station_stops.station_id` i `schedules.commercial_category` zostały usunięte
  (API zawiera stacje i kategorie spoza słownika)
- Zapis snapshotu: batch insert przez `unnest` (1 round trip na 10k pociągów zamiast 10k)

## Komendy

```bash
poetry run cns --once --verbose    # jednorazowe pobranie
poetry run cns --verbose           # tryb ciągły co 15 min
poetry run cns db-init             # migracje SQL
poetry run cns db-stats            # statystyki bazy
poetry run cns api-serve           # FastAPI na 127.0.0.1:8000
poetry run pytest -v                    # testy
docker exec -i cyrk-na-szynach-db psql -U cyrk_na_szynach -d cyrk_na_szynach < plik.sql
```

## Co działa ✅

- Kolekcjonowanie danych real-time (co 15 min, 10000 pociągów/snapshot)
- Zapis do PostgreSQL — batch insert przez `unnest` (cel: <10s zamiast ~50s)
- Słowniki stacji i przewoźników (upsert)
- Rozkład planowy (raz dziennie)
- Utrudnienia (co 60 min)
- Obsługa rate-limit (czeka do kolejnej godziny jeśli limit wyczerpany)
- Filtrowanie anomalii >200 min
- FastAPI: `/delays/stations/top`, `/delays/active`, `/stats`
- Testy jednostkowe: parser + storage + weather + calendar + features (166 testów łącznie)
- WeatherClient (Open-Meteo): pobieranie pogody co 1h dla ~30 głównych stacji PKP

## Co jest do zrobienia (backlog)

- [x] WeatherClient + tabela `weather_observations` (Faza 1.1) ✅
- [x] CalendarService + tabela `calendar_events` (Faza 1.2) ✅
- [x] Feature Store `mv_training_features` (Faza 2.1) ✅
- [ ] Dashboard / wizualizacja opóźnień (Streamlit?)
- [ ] Analiza opóźnień per przewoźnik
- [ ] Alerty (email/push) dla dużych opóźnień
- [ ] Paginacja /operations (czy API zwraca >10000 pociągów?)
- [ ] Połączenie operations z schedules (carrierCode przez scheduleId+orderId)
- [ ] Monitoring procesu kolekcjonowania (czy działa? luki w danych?)
- [ ] Testy integracyjne API (httpx + TestClient)
