# cyrk_na_szynach v1.0

System kolekcjonowania danych o opóźnieniach PKP PLK w czasie rzeczywistym.
Źródło: [pdp-api.plk-sa.pl](https://pdp-api.plk-sa.pl) (oficjalne API PKP PLK)

## Wymagania

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- PostgreSQL 14+ lub Docker
- Klucz API PKP PLK (plan Basic: 100/h, 1000/dzień)

## Szybki start

```bash
# 1. Zainstaluj zależności (kolektor + API)
poetry install -E api

# 2. Skonfiguruj
cp .env.example .env
nano .env   # wpisz PKP_API_KEY i DATABASE_URL

# 3. Uruchom PostgreSQL (Docker)
docker run -d --name cyrk-na-szynach-db \
  -e POSTGRES_DB=cyrk_na_szynach \
  -e POSTGRES_USER=cyrk_na_szynach \
  -e POSTGRES_PASSWORD=haslo \
  -p 5432:5432 \
  postgres:16

# 4. Inicjalizuj bazę
poetry run cns db-init

# 5. Testowe pobranie
poetry run cns --once --verbose

# 6. Sprawdź co trafiło do bazy
poetry run cns db-stats

# 7. Uruchom API
poetry run cns api-serve
# → http://127.0.0.1:8000/docs
```

## Komendy

```bash
# Kolekcjonowanie
poetry run cns --once --verbose   # jednorazowe pobranie
poetry run cns                    # tryb ciągły (co 15 min)
poetry run cns --interval 10      # tryb ciągły co 10 min
poetry run cns --dry-run          # bez zapisu
poetry run cns --no-db            # zapis do plików JSON zamiast bazy

# Baza danych
poetry run cns db-init            # uruchom migracje SQL
poetry run cns db-stats           # statystyki bazy

# API
poetry run cns api-serve                        # domyślnie 127.0.0.1:8000
poetry run cns api-serve --host 0.0.0.0 --port 8080
poetry run cns api-serve --reload               # tryb developerski

# Testy
poetry run pytest -v                   # wszystkie testy
poetry run pytest cns/tests/test_postgres.py -v  # tylko postgres
```

## FastAPI – endpointy

| Endpoint | Opis |
|----------|------|
| `GET /` | Health check |
| `GET /delays/stations/top?limit=10` | Top N stacji z największymi opóźnieniami (7 dni) |
| `GET /delays/active?limit=20` | Aktualnie opóźnione pociągi (status P) |
| `GET /stats` | Statystyki bazy danych |

Interaktywna dokumentacja (Swagger): `http://127.0.0.1:8000/docs`

## Struktura projektu

```
cyrk_na_szynach/
├── pyproject.toml
├── .env.example
├── migrations/
│   ├── 001_initial_schema.sql   # tabele, indeksy, widoki
│   └── 002_timescaledb.sql      # hypertable (opcjonalne)
└── cns/
    ├── __main__.py              # CLI: collect, db-init, db-stats, api-serve
    ├── api/
    │   └── app.py               # FastAPI – endpointy REST
    ├── collector/
    │   ├── client.py            # klient HTTP PKP API
    │   ├── parser.py            # JSON → dataclasses
    │   └── collector.py         # orkiestrator harmonogramu
    ├── models/
    │   └── records.py           # modele danych
    ├── storage/
    │   └── postgres.py          # zapis do PostgreSQL (batch insert)
    └── tests/
        ├── test_parser.py       # testy parsera
        └── test_postgres.py     # testy storage (mocki)
```

## Schemat bazy

| Tabela                      | Opis                                    | Rekordów/dzień |
|-----------------------------|-----------------------------------------|---------------|
| `stations`                  | Słownik stacji (3259)                   | ~0            |
| `carriers`                  | Słownik przewoźników (22)               | ~0            |
| `commercial_categories`     | Kategorie IC, TLK, EIC...               | ~0            |
| `schedules`                 | Rozkład planowy                         | ~7000         |
| `schedule_stops`            | Przystanki z rozkładu                   | ~100 000      |
| `operations_snapshots`      | Metadane każdego pobrania               | 96            |
| `train_operations`          | Pociągi z każdego snapshotu             | ~38 000       |
| `station_stops`             | Przystanki z opóźnieniami ← główna      | ~650 000      |
| `disruptions`               | Utrudnienia w ruchu                     | ~310          |
| `disruption_affected_routes`| Powiązane trasy                         | ~5000         |

## Przydatne zapytania SQL

```sql
-- Aktualnie opóźnione aktywne pociągi
SELECT * FROM v_active_delays LIMIT 20;

-- Najbardziej opóźnione stacje (ostatnie 7 dni)
SELECT station_name, avg_delay_min, delay_rate_pct
FROM v_station_delay_stats
ORDER BY avg_delay_min DESC LIMIT 10;

-- Historia opóźnień dla stacji
SELECT planned_departure, delay_departure_min
FROM station_stops ss
JOIN stations st ON ss.station_id = st.station_id
WHERE st.name ILIKE '%Warszawa Centralna%'
  AND planned_departure > NOW() - INTERVAL '24 hours'
ORDER BY planned_departure DESC;
```
