# cyrk_na_szynach – Dokumentacja referencyjna

Pojedyncze źródło prawdy dla schematu bazy danych, PKP PLK Open Data API
i własnego FastAPI. Zweryfikowane empirycznie na podstawie rzeczywistych odpowiedzi
z API (2026-05-27) oraz kodu kolekcjonera.

---

## Spis treści

1. [PKP PLK Open Data API](#1-pkp-plk-open-data-api)
   - 1.1 [Uwierzytelnienie i limity](#11-uwierzytelnienie-i-limity)
   - 1.2 [Wersjonowanie](#12-wersjonowanie)
   - 1.3 [Obsługa błędów](#13-obsługa-błędów)
   - 1.4 [Endpointy – Słowniki](#14-endpointy--słowniki)
   - 1.5 [Endpointy – Rozkład planowy](#15-endpointy--rozkład-planowy)
   - 1.6 [Endpointy – Dane operacyjne (RT)](#16-endpointy--dane-operacyjne-rt)
   - 1.7 [Endpointy – Utrudnienia](#17-endpointy--utrudnienia)
   - 1.8 [Endpointy – Metadane i wersja danych](#18-endpointy--metadane-i-wersja-danych)
   - 1.9 [Ustalenia empiryczne – gotcha](#19-ustalenia-empiryczne--gotcha)
2. [Schemat bazy danych (PostgreSQL 16)](#2-schemat-bazy-danych-postgresql-16)
   - 2.1 [Tabele słownikowe](#21-tabele-słownikowe)
   - 2.2 [Rozkład planowy](#22-rozkład-planowy)
   - 2.3 [Dane operacyjne real-time](#23-dane-operacyjne-real-time)
   - 2.4 [Utrudnienia](#24-utrudnienia)
   - 2.5 [Dane pogodowe](#25-dane-pogodowe)
   - 2.6 [Kalendarz](#26-kalendarz)
   - 2.7 [Monitoring kolektora](#27-monitoring-kolektora)
   - 2.8 [Widoki analityczne](#28-widoki-analityczne)
   - 2.9 [Widok zmaterializowany – Feature Store](#29-widok-zmaterializowany--feature-store)
   - 2.10 [Indeksy](#210-indeksy)
   - 2.11 [Wzrost danych (szacunki)](#211-wzrost-danych-szacunki)
3. [Własne API FastAPI](#3-własne-api-fastapi)
   - 3.1 [Konfiguracja i uruchomienie](#31-konfiguracja-i-uruchomienie)
   - 3.2 [Endpointy](#32-endpointy)
   - 3.3 [Modele Pydantic](#33-modele-pydantic)

---

## 1. PKP PLK Open Data API

**Base URL:** `https://pdp-api.plk-sa.pl/api/v1`

### 1.1 Uwierzytelnienie i limity

**Trzy sposoby autoryzacji (równoważne):**
```
X-API-Key: sk_live_...         ← nagłówek (rekomendowany)
Authorization: Bearer sk_live_...
Authorization: ApiKey sk_live_...
```

**Plany i limity:**

| Plan | Godzinowy | Dzienny | Zastosowanie |
|------|-----------|---------|--------------|
| Basic | 100 | 1 000 | hobby, testy |
| Standard | 500 | 5 000 | aplikacje mobilne |
| Premium | 2 000 | 20 000 | tablice informacyjne |

**Nagłówki odpowiedzi:**
```
X-RateLimit-Hourly-Remaining   ← pozostałe zapytania w tej godzinie
X-RateLimit-Daily-Remaining    ← pozostałe zapytania w tej dobie
Retry-After                    ← sekundy do odblokowania (przy HTTP 429)
```

> **Empiryczne:** carriers używa osobnego licznika godzinowego niż reszta
> endpointów (widoczne w logach: 1966 vs 99 przy tym samym kluczu).

**HTTP 429 Too Many Requests** – odpowiedź przy przekroczeniu limitu:
```json
{
  "error": "RATE_LIMIT_EXCEEDED",
  "message": "...",
  "details": "...",
  "timestamp": "...",
  "path": "...",
  "traceId": "..."
}
```

### 1.2 Wersjonowanie

Aktualny: `v1`. Alternatywne sposoby wskazania wersji:
- Ścieżka URL: `/api/v1/...` (stosujemy)
- Nagłówek: `X-Api-Version: 1.0`
- Query param: `?api-version=1.0`

**Polityka kompatybilności:**
- Zmiany nieprzerywające (nowe pole, endpoint, wartość enum, opcjonalny parametr) – bez powiadomienia w v1
- Zmiany przerywające – tylko w v2, z min. 90-dniowym okresem przejściowym

**Pola zdeprecjonowane (v2.0):**
- `plannedArrivalTime` – format TimeSpan (np. `"15:00:00"`)
- `plannedDepartureTime` – format TimeSpan

Zamiast nich: `plannedArrival` i `plannedDeparture` (pełny datetime ISO 8601).

### 1.3 Obsługa błędów

| Kod | Znaczenie |
|-----|-----------|
| 400 | Bad Request – nieprawidłowe parametry |
| 401 | Unauthorized – brak lub nieważny klucz API |
| 403 | Forbidden – niewystarczające uprawnienia |
| 404 | Not Found |
| 429 | Too Many Requests – przekroczono limit |
| 500 | Internal Server Error |

**Schemat błędu:**
```json
{
  "error": "ERROR_CODE",
  "message": "opis",
  "details": "szczegóły",
  "timestamp": "2026-05-27T15:00:00Z",
  "path": "/api/v1/...",
  "traceId": "uuid"
}
```

### 1.4 Endpointy – Słowniki

#### `GET /dictionaries/stations`

Słownik wszystkich stacji kolejowych.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `search` | string | — | Filtr po nazwie stacji |
| `page` | int | 1 | Numer strony |
| `pageSize` | int | 500 | Rozmiar strony (max: 5000) |

**Struktura odpowiedzi (empiryczna):**
```json
{
  "stations": [
    {
      "id": 33506,
      "name": "Warszawa Centralna",
      "shortName": "W-wa Centralna",
      "latitude": 52.2297,
      "longitude": 21.0035
    }
  ]
}
```

> Parser obsługuje też klucze `items` i `data` jako fallback.
> Pole `id` odpowiada `station_id` (INTEGER).

---

#### `GET /dictionaries/carriers`

Słownik przewoźników kolejowych.

**Brak parametrów.**

**Struktura odpowiedzi (empiryczna):**
```json
{
  "carriers": [
    {
      "code": "IC",
      "name": "PKP Intercity",
      "validFrom": "2024-01-01",
      "validTo": null
    }
  ]
}
```

**Przykładowe kody:** `IC` (PKP Intercity), `KM` (Koleje Mazowieckie),
`KS` (Koleje Śląskie), `KD` (Koleje Dolnośląskie).

---

#### `GET /dictionaries/commercial-categories`

Kategorie handlowe pociągów (np. IC, TLK, EIC, Os).

**Brak parametrów.**

---

#### `GET /dictionaries/stop-types`

Typy zatrzymań na stacjach.

**Brak parametrów.**

---

### 1.5 Endpointy – Rozkład planowy

#### `GET /schedules`

Rozkład planowy pociągów.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `dateFrom` | string YYYY-MM-DD | dziś | Data początkowa |
| `dateTo` | string YYYY-MM-DD | dziś | Data końcowa (max zakres: 31 dni) |
| `stations` | string | wszystkie | IDs stacji rozdzielone przecinkami |
| `carriersInclude` | string | — | Kody przewoźników do uwzględnienia |
| `carriersExclude` | string | — | Kody przewoźników do wykluczenia |

**Struktura odpowiedzi:**
```json
{
  "routes": [
    {
      "scheduleId": 2026,
      "orderId": 513569932,
      "operatingDate": "2026-05-27",
      "carrierCode": "IC",
      "nationalNumber": "1234",
      "commercialCategory": "IC",
      "name": "Hańcza",
      "stops": [
        {
          "stationId": 33506,
          "orderNumber": 1,
          "arrivalTime": "2026-05-27T10:00:00",
          "departureTime": "2026-05-27T10:05:00",
          "platform": "3"
        }
      ]
    }
  ]
}
```

> `carrierCode` i `nationalNumber` (numer handlowy pociągu) są dostępne
> **wyłącznie tutaj**. W `/operations` są zawsze `null`.
> Łączenie: `(scheduleId, orderId, operatingDate)`.

---

#### `GET /schedules/shortened`

To samo co `/schedules`, ale z skróconymi nazwami pól JSON (mniejszy payload).

---

#### `GET /schedules/route/{scheduleId}/{orderId}`

Szczegółowa trasa jednego pociągu z rozkładu.

**Parametry ścieżki:** `scheduleId` (int), `orderId` (int)

---

#### `GET /schedules/routes/{date}`

Lista identyfikatorów tras kursujących w danym dniu.

**Parametry ścieżki:** `date` (YYYY-MM-DD)

**Odpowiedź:** lista par `(scheduleId, orderId)`.

---

### 1.6 Endpointy – Dane operacyjne (RT)

#### `GET /operations`

**Główny endpoint real-time.** Aktualne realizacje pociągów z opóźnieniami.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `stations` | string | wszystkie | IDs stacji rozdzielone przecinkami |
| `carriersInclude` | string | — | Kody przewoźników do uwzględnienia |
| `carriersExclude` | string | — | Kody przewoźników do wykluczenia |
| `fullRoutes` | boolean | false | Zwróć pełną trasę zamiast bieżącego okna |
| `withPlanned` | boolean | false | Uwzględnij przyszłe przystanki (planned) |
| `page` | int | 1 | Numer strony paginacji |
| `pageSize` | int | 1000 | Rozmiar strony (max: **10 000**) |

**Pełna struktura odpowiedzi (zweryfikowana empirycznie 2026-05-27):**
```json
{
  "generatedAt": "2026-05-27T15:00:00",
  "pagination": {
    "page": 1,
    "pageSize": 10000,
    "totalCount": 9847
  },
  "trains": [
    {
      "scheduleId": 2026,
      "orderId": 513569932,
      "trainOrderId": 513569932,
      "operatingDate": "2026-05-27",
      "trainStatus": "P",
      "stations": [
        {
          "stationId": 35428,
          "plannedSequenceNumber": 1,
          "actualSequenceNumber": 1,
          "plannedArrival": "2026-05-27T15:00:00",
          "plannedDeparture": "2026-05-27T15:00:00",
          "actualArrival": "2026-05-27T15:02:00",
          "actualDeparture": "2026-05-27T15:03:00",
          "plannedArrivalTime": "15:00:00",
          "plannedDepartureTime": "15:00:00",
          "isConfirmed": true,
          "isCancelled": false
        }
      ]
    }
  ],
  "stations": {
    "35428": "Grodzisk Mazowiecki Radońska",
    "33506": "Warszawa Centralna"
  }
}
```

**Statusy `trainStatus`:**

| Kod | Znaczenie |
|-----|-----------|
| `S` | Scheduled – zaplanowany, nie ruszył |
| `P` | In Progress – aktualnie w trasie |
| `C` | Completed – zakończył kurs |
| `X` | Cancelled – odwołany |
| `Q` | Unknown – nieznany edge case |

**Kluczowe pola przystanku (`stations[]`):**

| Pole | Typ | Opis |
|------|-----|------|
| `stationId` | int | ID stacji (castuj na str do porównania ze słownikiem) |
| `plannedSequenceNumber` | int | Planowana kolejność przystanku |
| `actualSequenceNumber` | int | Faktyczna kolejność przystanku |
| `plannedArrival` | datetime | Planowany przyjazd (ISO 8601, bez timezone) |
| `plannedDeparture` | datetime | Planowany odjazd |
| `actualArrival` | datetime | Faktyczny przyjazd (może być planowanym dla przyszłych) |
| `actualDeparture` | datetime | Faktyczny odjazd |
| `plannedArrivalTime` ⚠️ | TimeSpan | **DEPRECATED** (v2.0) |
| `plannedDepartureTime` ⚠️ | TimeSpan | **DEPRECATED** (v2.0) |
| `isConfirmed` | boolean | Pociąg faktycznie przejechał przez ten przystanek |
| `isCancelled` | boolean | Przystanek odwołany |

---

#### `GET /operations/shortened`

To samo co `/operations`, skrócone nazwy pól.

---

#### `GET /operations/train/{scheduleId}/{orderId}/{operatingDate}`

Dane realizacji konkretnego pociągu.

**Parametry ścieżki:** `scheduleId`, `orderId`, `operatingDate` (YYYY-MM-DD)

---

#### `GET /operations/statistics`

Statystyki pociągów na dany dzień.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `date` | string YYYY-MM-DD | dziś | Data statystyk |

**Odpowiedź:** liczby pociągów wg statusu (w trasie, zakończone, odwołane, etc.).

---

### 1.7 Endpointy – Utrudnienia

#### `GET /disruptions`

Lista utrudnień kolejowych.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `dateFrom` | string YYYY-MM-DD | dziś | Data początkowa |
| `dateTo` | string YYYY-MM-DD | dziś | Data końcowa (max zakres: 31 dni) |
| `stations` | string | — | IDs stacji |
| `carriersInclude` | string | — | Kody przewoźników |
| `carriersExclude` | string | — | Kody przewoźników do wykluczenia |

**Struktura odpowiedzi (`DisruptionDto`, zweryfikowana empirycznie):**
```json
{
  "disruptions": [
    {
      "disruptionId": 12345,
      "disruptionTypeCode": "DELAY",
      "startStationId": 33506,
      "endStationId": 35428,
      "message": "Opóźnienie z powodu prac torowych.",
      "affectedRoutes": [
        {
          "scheduleId": 2026,
          "orderId": 513569932,
          "operatingDate": "2026-05-27",
          "stationId": 33506,
          "sequenceNumber": 3
        }
      ]
    }
  ]
}
```

> Brak pól: `title`, `dateFrom`, `dateTo`, `carriers` — nie istnieją w schemacie `DisruptionDto`.

---

#### `GET /disruptions/shortened`

To samo co `/disruptions`, skrócone nazwy pól.

---

### 1.8 Endpointy – Metadane i wersja danych

#### `GET /data-version`

GUID bieżącej wersji danych (schedule i operations). Używane do cache'owania:
przed każdym pobraniem `/operations` porównujemy GUID – jeśli niezmieniony,
pomijamy pobranie (oszczędność limitów).

```json
{
  "scheduleVersion": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "operationsVersion": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
}
```

---

#### `GET /fields/schedules`, `GET /fields/schedules/csv`

Opis struktury pól endpointu `/schedules` (bez auth).

#### `GET /fields/operations`, `GET /fields/operations/csv`

Opis struktury pól endpointu `/operations` (bez auth).

#### `GET /fields/disruptions`, `GET /fields/disruptions/csv`

Opis struktury pól endpointu `/disruptions` (bez auth).

---

#### `GET /apikey/info`

Informacje o aktywnym kluczu API (plan, limity, email).

#### `GET /apikey/usage`

Statystyki użycia klucza (dziś, w tym tygodniu, w tym miesiącu).

---

### 1.9 Ustalenia empiryczne – gotcha

| # | Problem | Zachowanie |
|---|---------|------------|
| 1 | Klucz listy pociągów | `/operations` zwraca `trains[]`, **nie** `operations[]` |
| 2 | `trainNumber` i `carrierCode` | Niedostępne w `/operations` (zawsze null). Dostępne tylko w `/schedules`. Łącz po `(scheduleId, orderId, operatingDate)` |
| 3 | Typy ID w JSON | `stationId`, `scheduleId`, `orderId` to `int` w JSON → kastuj na `str` przed porównaniem ze słownikiem |
| 4 | Obliczanie opóźnień | API nie zwraca gotowych wartości opóźnień. Liczymy: `delay = actual - planned` w minutach |
| 5 | Anomalie >200 min | Przesunięcia rozkładowe (pociąg przełożony o dobę = 1440 min). Filtrowane przez `MAX_REALISTIC_DELAY = 200`. Próg wynika z analizy: 1221 anomalii vs 59628 realnych opóźnień |
| 6 | Stacje spoza słownika | API zwraca `stationId` których nie ma w `/dictionaries/stations`. FK `station_stops.station_id` jest `ON DELETE SET NULL` |
| 7 | Słownik stacji w odpowiedzi | `"stations"` na **poziomie głównym** odpowiedzi `/operations` to słownik `{id→nazwa}` – do uzupełnienia nazw bez osobnego zapytania |
| 8 | `isConfirmed` dla przyszłych | API zwraca `actualArrival` dla wszystkich przystanków (przyszłe wypełnia planowanym), więc `IS NOT NULL` nie wystarczy do wykrycia aktualnej pozycji pociągu. Używamy `isConfirmed=true` lub heurystyki `actual_arrival <= fetched_at + 2h` |
| 9 | Offset strefy czasowej | Parser zapisuje czasy z API (strefa Europe/Warsaw, UTC+2 latem) jako naive datetime → psycopg3 traktuje jako UTC → rzeczywiste przesunięcie 2h w bazie. W zimie (UTC+1) offset jest o 1h za duży – akceptowalne przybliżenie |
| 10 | 10 000 rekordów limit | API zwraca max 10 000 pociągów/stronę. Paginacja niezbadana – możliwe że sieć PKP ma więcej |
| 11 | Dwa osobne liczniki godzinowe | `/dictionaries/carriers` używa innego licznika niż pozostałe endpointy |

---

## 2. Schemat bazy danych (PostgreSQL 16)

Stan po wszystkich migracjach (`001`–`013`).

---

### 2.1 Tabele słownikowe

#### `stations`

Słownik stacji kolejowych. Wypełniany przy starcie przez `/dictionaries/stations`.

```sql
CREATE TABLE stations (
    station_id  INTEGER      PRIMARY KEY,   -- ID ze słownika PKP
    name        VARCHAR(200) NOT NULL,
    short_name  VARCHAR(100),
    latitude    FLOAT,
    longitude   FLOAT,
    synced_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

> ~3 259 stacji. Stacje z `latitude IS NULL` pomijane przez WeatherClient.

---

#### `carriers`

Słownik przewoźników. Wypełniany przy starcie przez `/dictionaries/carriers`.

```sql
CREATE TABLE carriers (
    code      VARCHAR(20)  PRIMARY KEY,   -- np. 'IC', 'KM', 'KS'
    name      VARCHAR(200) NOT NULL,      -- np. 'PKP Intercity'
    synced_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

> ~22 przewoźników.

---

#### `commercial_categories`

Kategorie handlowe pociągów (IC, TLK, EIC, Os, OsP, …).

```sql
CREATE TABLE commercial_categories (
    symbol  VARCHAR(20)  PRIMARY KEY,   -- np. 'IC', 'TLK'
    name    VARCHAR(200) NOT NULL       -- np. 'InterCity'
);
```

> Niektóre kody z `/operations` (np. `Os/OsP`, `S4/S40`) mogą nie mieć
> wpisu w słowniku. FK w `schedules.commercial_category` jest `ON DELETE SET NULL`.

---

### 2.2 Rozkład planowy

#### `schedules`

Trasy pociągów z rozkładu planowego. Pobierane raz dziennie po 04:00.

```sql
CREATE TABLE schedules (
    id                   BIGSERIAL    PRIMARY KEY,
    schedule_id          INTEGER      NOT NULL,
    order_id             BIGINT       NOT NULL,
    carrier_code         VARCHAR(20)  REFERENCES carriers(code) ON DELETE SET NULL,
    national_number      VARCHAR(20),             -- numer handlowy (np. '1234')
    commercial_category  VARCHAR(20)  REFERENCES commercial_categories(symbol) ON DELETE SET NULL,
    operating_date       DATE         NOT NULL,
    train_name           VARCHAR(200),            -- nazwa własna (np. 'Hańcza') – z /schedules
    fetched_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (schedule_id, order_id, operating_date)
);
```

**Indeksy:**
```sql
idx_schedules_lookup   ON (schedule_id, order_id, operating_date)
idx_schedules_carrier  ON (carrier_code, operating_date)
```

> `train_name` dodane w migracji `011`. Dla starszych rekordów = NULL.
> Klucz łączący z `train_operations`: `(schedule_id, order_id, operating_date)`.

---

#### `schedule_stops`

Przystanki z rozkładu planowego.

```sql
CREATE TABLE schedule_stops (
    id              BIGSERIAL    PRIMARY KEY,
    schedule_id     BIGINT       NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    station_id      INTEGER      NOT NULL REFERENCES stations(station_id) ON DELETE RESTRICT,
    order_number    INTEGER      NOT NULL,
    arrival_time    TIME,
    departure_time  TIME,
    platform        VARCHAR(20),
    UNIQUE (schedule_id, order_number)
);
```

**Indeks:**
```sql
idx_schedule_stops_station  ON (station_id)
```

> ~100 000 rekordów/dzień.

---

### 2.3 Dane operacyjne real-time

#### `operations_snapshots`

Metadane każdego pobrania `/operations`. Tworzony przy każdym ticku (co 15 min).

```sql
CREATE TABLE operations_snapshots (
    id            BIGSERIAL    PRIMARY KEY,
    data_version  VARCHAR(100),             -- GUID z /data-version (NULL jeśli pominięto)
    fetched_at    TIMESTAMPTZ  NOT NULL,
    total_trains  INTEGER      NOT NULL DEFAULT 0,
    total_stops   INTEGER      NOT NULL DEFAULT 0
);
```

**Indeks:**
```sql
idx_snapshots_fetched_at  ON (fetched_at DESC)
```

> 96 snapshotów/dzień (co 15 min). Jeśli GUID niezmieniony od poprzedniego
> snapshotu – pobranie pomijane (oszczędność limitów API).

---

#### `train_operations`

Jeden rekord per pociąg per snapshot. Odpowiada jednemu elementowi z `trains[]`.

```sql
CREATE TABLE train_operations (
    id              BIGSERIAL    PRIMARY KEY,
    snapshot_id     BIGINT       NOT NULL REFERENCES operations_snapshots(id) ON DELETE CASCADE,
    schedule_id     INTEGER      NOT NULL,
    order_id        BIGINT       NOT NULL,
    operating_date  DATE,
    train_status    CHAR(1)      NOT NULL CHECK (train_status IN ('S','P','C','X','Q')),
    collected_at    TIMESTAMPTZ  NOT NULL
);
```

**Indeksy:**
```sql
idx_train_ops_snapshot  ON (snapshot_id)
idx_train_ops_lookup    ON (schedule_id, order_id, operating_date)
idx_train_ops_status    ON (train_status, collected_at DESC)
```

> ~38 400 rekordów/dzień (400 pociągów × 96 snapshotów).
> `train_status`: S/P/C/X/Q (patrz sekcja 1.6).

---

#### `station_stops`

**Główna tabela systemu.** Jeden rekord per przystanek per pociąg per snapshot.

```sql
CREATE TABLE station_stops (
    id                   BIGSERIAL   PRIMARY KEY,
    train_op_id          BIGINT      NOT NULL REFERENCES train_operations(id) ON DELETE CASCADE,
    station_id           INTEGER     REFERENCES stations(station_id) ON DELETE SET NULL,
    planned_sequence     INTEGER     NOT NULL,
    actual_sequence      INTEGER     NOT NULL,
    planned_arrival      TIMESTAMPTZ,
    actual_arrival       TIMESTAMPTZ,
    planned_departure    TIMESTAMPTZ,
    actual_departure     TIMESTAMPTZ,
    delay_arrival_min    INTEGER,    -- NULL = brak danych lub anomalia >200 min
    delay_departure_min  INTEGER,    -- NULL = brak danych lub anomalia >200 min
    is_confirmed         BOOLEAN     NOT NULL DEFAULT FALSE,  -- z API: isConfirmed
    is_cancelled         BOOLEAN     NOT NULL DEFAULT FALSE   -- z API: isCancelled
);
```

**Indeksy:**
```sql
idx_station_stops_station_time  ON (station_id, planned_departure DESC)
idx_station_stops_delay         ON (delay_departure_min, planned_departure DESC)
                                   WHERE delay_departure_min IS NOT NULL
idx_station_stops_train_op      ON (train_op_id)
idx_station_stops_confirmed     ON (train_op_id, is_confirmed, planned_sequence DESC)
```

> ~650 000 rekordów/dzień (~2–3 GB/miesiąc).
> `station_id` może być NULL gdy stacja nie istnieje w słowniku `stations`.
> `is_confirmed` / `is_cancelled` dodane w migracji `011`. Starsze rekordy mają `FALSE`.
> Opóźnienia obliczane przez parser: `delay = actual - planned` (minuty całkowite).
> Filtr anomalii: `|delay| > 200 min → NULL`.

---

### 2.4 Utrudnienia

#### `disruptions`

Utrudnienia kolejowe z `/disruptions`. Pobierane co 60 min.

```sql
CREATE TABLE disruptions (
    id              BIGSERIAL   PRIMARY KEY,
    disruption_id   INTEGER     NOT NULL,
    message         TEXT,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    collected_date  DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (disruption_id, collected_date)
);
```

**Indeksy:**
```sql
idx_disruptions_collected  ON (collected_at DESC)
idx_disruptions_api_id     ON (disruption_id)
```

> ~310 rekordów/dzień. UNIQUE po `(disruption_id, collected_date)` = jedno utrudnienie/dzień.

---

#### `disruption_affected_routes`

Trasy dotknięte utrudnieniem (z `affectedRoutes[]` w odpowiedzi API).

```sql
CREATE TABLE disruption_affected_routes (
    id              BIGSERIAL   PRIMARY KEY,
    disruption_id   BIGINT      NOT NULL REFERENCES disruptions(id) ON DELETE CASCADE,
    schedule_id     INTEGER     NOT NULL,
    order_id        BIGINT      NOT NULL,
    operating_date  DATE,
    station_id      INTEGER     REFERENCES stations(station_id) ON DELETE SET NULL,
    sequence_number INTEGER
);
```

**Indeksy:**
```sql
idx_disruption_routes_disruption  ON (disruption_id)
idx_disruption_routes_station     ON (station_id)
```

---

### 2.5 Dane pogodowe

#### `weather_observations`

Obserwacje i prognozy pogodowe z Open-Meteo API (bezpłatne). Pobierane co 60 min
dla ~30 głównych węzłów PKP przez `WeatherClient`.

```sql
CREATE TABLE weather_observations (
    id               BIGSERIAL    PRIMARY KEY,
    station_id       VARCHAR(20),               -- soft FK do stations (bez CONSTRAINT)
    observed_at      TIMESTAMPTZ  NOT NULL,     -- czas obserwacji lub godzina prognozy
    is_forecast      BOOLEAN      NOT NULL DEFAULT FALSE,  -- FALSE=obserwacja, TRUE=prognoza
    temperature_c    NUMERIC(5,2),              -- temperatura [°C]
    precipitation_mm NUMERIC(6,2),              -- opady [mm]
    wind_speed_kmh   NUMERIC(6,2),              -- prędkość wiatru [km/h]
    snowfall_cm      NUMERIC(6,2),              -- opady śniegu [cm]
    visibility_m     INTEGER,                   -- widzialność [m]
    cloud_cover_pct  SMALLINT,                  -- zachmurzenie [%]
    weather_code     SMALLINT,                  -- kod WMO
    collected_at     TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (station_id, observed_at, is_forecast)
);
```

**Indeksy:**
```sql
weather_observations_station_time_idx  ON (station_id, observed_at DESC)
weather_observations_forecast_idx      ON (observed_at) WHERE is_forecast = TRUE
```

> `station_id` to VARCHAR(20), natomiast `stations.station_id` to INTEGER.
> Łączenie: `weather_observations.station_id = station_stops.station_id::TEXT`
> (rzutowanie w zapytaniach SQL i w `mv_training_features`).
> Open-Meteo zwraca `visibility` w metrach (nie km).
> `forecast_days=2` → dokładnie 48 rekordów godzinowych.

---

### 2.6 Kalendarz

#### `calendar_events`

Klasyfikacja typów dni: święta, ferie, weekendy. Generowane przez `CalendarService`
przy starcie na 5 lat naprzód. Aktualizowane automatycznie 1 stycznia.

```sql
CREATE TABLE calendar_events (
    id          BIGSERIAL   PRIMARY KEY,
    event_date  DATE        NOT NULL,
    zone        CHAR(1),              -- 'A' | 'B' | 'C' | NULL = cały kraj
    day_type    VARCHAR(30) NOT NULL, -- wartość enum DayType
    event_name  VARCHAR(100),         -- np. 'Wielkanoc', 'Ferie zimowe strefa B'
    UNIQUE NULLS NOT DISTINCT (event_date, zone)  -- PostgreSQL 15+
);
```

**Indeks:**
```sql
calendar_events_date_idx  ON (event_date)
```

**Wartości `day_type` (enum `DayType`):**

| Wartość | Opis | Priorytet |
|---------|------|-----------|
| `HOLIDAY` | Święto ustawowe | najwyższy |
| `WINTER_BREAK` | Ferie zimowe (strefa A/B/C) | 2 |
| `SUMMER_BREAK` | Wakacje letnie (lip–sie) | 3 |
| `WEEKEND` | Sobota lub niedziela | 4 |
| `LONG_WEEKEND` | Pomost – dzień roboczy między dniami wolnymi | 5 |
| `HOLIDAY_EVE` | Dzień przed świętem | 6 |
| `HOLIDAY_RETURN` | Dzień po święcie | 7 |
| `WORKING` | Zwykły dzień roboczy | najniższy |

**Strefy ferii zimowych (MEN):**

| Strefa | Województwa |
|--------|-------------|
| A | dolnośląskie, opolskie, zachodniopomorskie, wielkopolskie |
| B | kujawsko-pomorskie, lubuskie, łódzkie, małopolskie, świętokrzyskie, pomorskie |
| C | lubelskie, mazowieckie, podkarpackie, podlaskie, śląskie, warmińsko-mazurskie |

> Wymaga PostgreSQL 15+ ze względu na `UNIQUE NULLS NOT DISTINCT`.

---

### 2.7 Monitoring kolektora

#### `collector_health`

Status procesu kolekcjonowania. Wypełniany co 5 min przez `HealthChecker`.

```sql
CREATE TABLE collector_health (
    id                      SERIAL      PRIMARY KEY,
    check_time              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_snapshot_at        TIMESTAMPTZ,
    minutes_since_snapshot  INTEGER,
    snapshots_last_24h      INTEGER     NOT NULL DEFAULT 0,
    expected_snapshots_24h  INTEGER     NOT NULL DEFAULT 96,
    gaps                    JSONB,      -- [{from_time, to_time, gap_minutes}]
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('OK','WARNING','CRITICAL'))
);
```

**Indeks:**
```sql
collector_health_check_time_idx  ON (check_time DESC)
```

**Progi statusów:**

| Status | Warunek | Interpretacja |
|--------|---------|---------------|
| `CRITICAL` | `minutes_since_snapshot >= 30` lub brak snapshotów | Kolektor prawdopodobnie nie działa |
| `WARNING` | `snapshots_last_24h < 77` (< 80% z 96) | Pokrycie <80% – rate-limit lub restart |
| `OK` | Oba warunki spełnione | Kolektor działa poprawnie |

> Luka = przerwa między snapshotami > 20 min (co najmniej jeden pominięty cykl 15-min).

---

### 2.8 Widoki analityczne

#### `v_station_delay_stats`

Statystyki opóźnień per stacja z ostatnich 7 dni. Wymaga min. 10 pomiarów.

```sql
SELECT
    station_id, station_name,
    COUNT(*)                                        AS total_stops,
    COUNT(delay_departure_min)                      AS stops_with_data,
    SUM(CASE WHEN delay_departure_min > 0 THEN 1 ELSE 0 END) AS delayed_count,
    ROUND(AVG(delay_departure_min) FILTER (WHERE delay_departure_min > 0), 1) AS avg_delay_min,
    MAX(delay_departure_min)                        AS max_delay_min,
    ROUND(100.0 * delayed_count / stops_with_data, 1) AS delay_rate_pct
FROM station_stops
JOIN train_operations ON ...
WHERE collected_at >= NOW() - INTERVAL '7 days'
  AND delay_departure_min IS NOT NULL
GROUP BY station_id, station_name
HAVING COUNT(*) >= 10
ORDER BY avg_delay_min DESC NULLS LAST;
```

**Kolumny:**

| Kolumna | Typ | Opis |
|---------|-----|------|
| `station_id` | INTEGER | ID stacji |
| `station_name` | TEXT | Nazwa stacji |
| `total_stops` | BIGINT | Łączna liczba zatrzymań |
| `stops_with_data` | BIGINT | Zatrzymania z danymi o opóźnieniu |
| `delayed_count` | BIGINT | Liczba opóźnionych (>0 min) |
| `avg_delay_min` | NUMERIC | Średnie opóźnienie (tylko opóźnione) |
| `max_delay_min` | INTEGER | Maksymalne opóźnienie |
| `delay_rate_pct` | NUMERIC | % opóźnionych zatrzymań |

---

#### `v_active_delays`

Pociągi aktualnie w trasie (status P) i odwołane (X) z **ostatniego snapshotu**.
Jeden wiersz per pociąg (nie per przystanek).

```sql
-- Aktualny widok (po migracji 013)
-- Kolumny:
```

| Kolumna | Typ | Opis |
|---------|-----|------|
| `schedule_id` | INTEGER | ID trasy rozkładowej |
| `order_id` | BIGINT | ID konkretnego kursowania |
| `operating_date` | DATE | Data kursowania |
| `train_status` | CHAR(1) | P = w trasie, X = odwołany |
| `snapshot_time` | TIMESTAMPTZ | Czas pobrania snapshotu |
| `train_number` | VARCHAR | Numer handlowy (z `schedules.national_number`) |
| `train_name` | VARCHAR | Nazwa własna pociągu (np. "Hańcza") |
| `carrier_name` | VARCHAR | Nazwa przewoźnika (z `carriers.name`) |
| `first_station` | TEXT | Pierwsza stacja trasy |
| `first_station_departure` | TIMESTAMPTZ | Planowany odjazd z pierwszej stacji |
| `last_station` | TEXT | Ostatnia stacja trasy |
| `last_station_arrival` | TIMESTAMPTZ | Planowany przyjazd na ostatnią stację |
| `last_visited_station` | TEXT | Ostatni miniiony przystanek (heurystyka `+2h`) |
| `last_visited_arrival` | TIMESTAMPTZ | Faktyczny przyjazd na last_visited |
| `delay_departure_min` | INTEGER | MAX opóźnienia odjazdu na całej trasie |
| `delay_arrival_min` | INTEGER | MAX opóźnienia przyjazdu na całej trasie |

> `last_visited_station`: heurystyka `actual_arrival <= fetched_at + INTERVAL '2 hours'`
> (kompensuje brak poprawnego `is_confirmed` w starszych danych).
> `delay_departure_min` = MAX ze wszystkich przystanków (worst case na trasie).

---

### 2.9 Widok zmaterializowany – Feature Store

#### `mv_training_features`

Widok zmaterializowany dla modelu ML. Łączy `station_stops`, pogodę i kalendarz.
Wymaga UNIQUE indeksu (wymaganie `REFRESH CONCURRENTLY`).

```sql
CREATE MATERIALIZED VIEW mv_training_features AS ... WITH NO DATA;
CREATE UNIQUE INDEX mv_training_features_id_idx     ON mv_training_features (id);
CREATE INDEX mv_training_features_station_date_idx  ON mv_training_features (station_id, operating_date);
CREATE INDEX mv_training_features_date_idx          ON mv_training_features (operating_date);
```

**Kolumny:**

| Kolumna | Źródło | Opis |
|---------|--------|------|
| `id` | `station_stops.id` | PK (wymagany przez CONCURRENTLY) |
| `station_id` | `station_stops` | ID stacji (INTEGER) |
| `station_name` | `stations.name` | Nazwa stacji |
| `delay_departure_min` | `station_stops` | **Target** ML: opóźnienie odjazdu [min] |
| `delay_arrival_min` | `station_stops` | Target alternatywny: opóźnienie przyjazdu |
| `operating_date` | `planned_departure::date` | Data kursowania |
| `hour_of_day` | `EXTRACT(HOUR …)` | Godzina odjazdu [0–23] (SMALLINT) |
| `day_of_week` | `EXTRACT(DOW …)` | Dzień tygodnia [0=Sun … 6=Sat] (SMALLINT) |
| `month` | `EXTRACT(MONTH …)` | Miesiąc [1–12] (SMALLINT) |
| `day_type` | `calendar_events (zone IS NULL)` | Typ dnia ogólnopolski |
| `day_type_zone_b` | `calendar_events (zone='B')` | Typ dnia strefa B |
| `prev_stop_delay_min` | `LAG()` | Opóźnienie poprzedniego przystanku; NULL dla pierwszego |
| `planned_sequence` | `station_stops` | Numer przystanku na trasie |
| `sequence_delta` | `actual - planned` | Zmiana kolejności przystanków |
| `temperature_c` | `weather_observations` | Temperatura [°C] |
| `precipitation_mm` | — | Opady [mm] |
| `wind_speed_kmh` | — | Prędkość wiatru [km/h] |
| `snowfall_cm` | — | Opady śniegu [cm] |
| `visibility_m` | — | Widzialność [m] |
| `cloud_cover_pct` | — | Zachmurzenie [%] |
| `weather_code` | — | Kod WMO |
| `is_snowing` | `snowfall_cm > 1` | BOOLEAN |
| `is_heavy_rain` | `precipitation_mm > 5` | BOOLEAN |
| `is_strong_wind` | `wind_speed_kmh > 70` | BOOLEAN |
| `is_frost` | `temperature_c < -10` | BOOLEAN |
| `is_dense_fog` | `visibility_m < 200` | BOOLEAN |
| `train_status` | `train_operations` | Filtr: tylko C i P |
| `snapshot_time` | `operations_snapshots.fetched_at` | Czas snapshotu |

**Strategia odświeżania:**
```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_training_features;
-- Wymaga: autocommit=True na połączeniu psycopg3
-- Czas: ~10–30s na 1–2M wierszy
-- Częstość: po każdym save_snapshot() (co 15 min), wątek daemon
```

**JOIN z pogodą (LATERAL):**
```sql
LEFT JOIN LATERAL (
  SELECT ...
  FROM weather_observations wo2
  WHERE wo2.station_id = ss.station_id::TEXT   -- rzutowanie INTEGER→TEXT!
    AND wo2.observed_at <= ss.planned_departure
    AND wo2.is_forecast = FALSE
  ORDER BY wo2.observed_at DESC LIMIT 1
) wo ON TRUE
```

---

### 2.10 Indeksy

Pełna lista wszystkich indeksów w systemie:

| Tabela | Indeks | Kolumny | Typ |
|--------|--------|---------|-----|
| `operations_snapshots` | `idx_snapshots_fetched_at` | `(fetched_at DESC)` | BTREE |
| `train_operations` | `idx_train_ops_snapshot` | `(snapshot_id)` | BTREE |
| `train_operations` | `idx_train_ops_lookup` | `(schedule_id, order_id, operating_date)` | BTREE |
| `train_operations` | `idx_train_ops_status` | `(train_status, collected_at DESC)` | BTREE |
| `station_stops` | `idx_station_stops_station_time` | `(station_id, planned_departure DESC)` | BTREE |
| `station_stops` | `idx_station_stops_delay` | `(delay_departure_min, planned_departure DESC)` WHERE NOT NULL | PARTIAL |
| `station_stops` | `idx_station_stops_train_op` | `(train_op_id)` | BTREE |
| `station_stops` | `idx_station_stops_confirmed` | `(train_op_id, is_confirmed, planned_sequence DESC)` | BTREE |
| `schedules` | `idx_schedules_lookup` | `(schedule_id, order_id, operating_date)` | BTREE |
| `schedules` | `idx_schedules_carrier` | `(carrier_code, operating_date)` | BTREE |
| `schedule_stops` | `idx_schedule_stops_station` | `(station_id)` | BTREE |
| `disruptions` | `idx_disruptions_collected` | `(collected_at DESC)` | BTREE |
| `disruptions` | `idx_disruptions_api_id` | `(disruption_id)` | BTREE |
| `disruption_affected_routes` | `idx_disruption_routes_disruption` | `(disruption_id)` | BTREE |
| `disruption_affected_routes` | `idx_disruption_routes_station` | `(station_id)` | BTREE |
| `weather_observations` | `weather_observations_station_time_idx` | `(station_id, observed_at DESC)` | BTREE |
| `weather_observations` | `weather_observations_forecast_idx` | `(observed_at)` WHERE is_forecast=TRUE | PARTIAL |
| `calendar_events` | `calendar_events_date_idx` | `(event_date)` | BTREE |
| `collector_health` | `collector_health_check_time_idx` | `(check_time DESC)` | BTREE |
| `mv_training_features` | `mv_training_features_id_idx` | `(id)` | UNIQUE |
| `mv_training_features` | `mv_training_features_station_date_idx` | `(station_id, operating_date)` | BTREE |
| `mv_training_features` | `mv_training_features_date_idx` | `(operating_date)` | BTREE |

---

### 2.11 Wzrost danych (szacunki)

| Tabela | Rekordów/dzień | Rozmiar/miesiąc |
|--------|----------------|-----------------|
| `station_stops` | ~650 000 | ~2–3 GB |
| `train_operations` | ~38 400 | ~150 MB |
| `operations_snapshots` | 96 | ~1 MB |
| `disruptions` | ~310 | ~5 MB |
| `disruption_affected_routes` | ~5 000 | ~20 MB |
| `weather_observations` | ~720 | ~5 MB |
| `schedules` | ~7 000 | ~30 MB |
| `schedule_stops` | ~100 000 | ~400 MB |

---

## 3. Własne API FastAPI

**Uruchomienie:**
```bash
poetry install -E api
poetry run cns api-serve                        # 127.0.0.1:8000
poetry run cns api-serve --host 0.0.0.0 --port 8080
poetry run cns api-serve --reload               # tryb dev z hot-reload
```

**Zmienna środowiskowa:** `DATABASE_URL` (wymagana; 503 gdy brak)

**Swagger UI:** `http://127.0.0.1:8000/docs`
**ReDoc:** `http://127.0.0.1:8000/redoc`

---

### 3.2 Endpointy

#### `GET /`

Health check.

**Odpowiedź:**
```json
{"status": "ok", "service": "cyrk_na_szynach", "version": "1.0.0"}
```

---

#### `GET /health/collector`

Stan kolektora danych. Źródło: tabela `collector_health`.

**Odpowiedź 200:**
```json
{
  "status": "OK",
  "last_snapshot_at": "2026-05-31T14:30:00+00:00",
  "minutes_since_last_snapshot": 8,
  "snapshots_last_24h": 93,
  "expected_24h": 96,
  "coverage_pct": 96.9,
  "gaps_last_24h": [
    {
      "from_time": "2026-05-31T02:00:00+00:00",
      "to_time": "2026-05-31T02:45:00+00:00",
      "gap_minutes": 45
    }
  ],
  "checked_at": "2026-05-31T14:38:00+00:00"
}
```

**Odpowiedź 503:** brak wpisów w `collector_health` (kolektor nie uruchomiony).

---

#### `GET /delays/stations/top`

Stacje z największymi średnimi opóźnieniami w ostatnich 7 dniach.
Źródło: `v_station_delay_stats` (min. 10 pomiarów per stacja).

**Parametry query:**

| Param | Typ | Domyślny | Zakres | Opis |
|-------|-----|----------|--------|------|
| `limit` | int | 10 | 1–500 | Liczba stacji w wynikach |

**Odpowiedź 200:** `list[StationDelayStat]`

```json
[
  {
    "station_id": 33506,
    "station_name": "Warszawa Centralna",
    "total_stops": 1842,
    "stops_with_data": 1840,
    "delayed_count": 923,
    "avg_delay_min": 8.4,
    "max_delay_min": 97,
    "delay_rate_pct": 50.2
  }
]
```

---

#### `GET /delays/active`

Aktualnie opóźnione pociągi z ostatniego snapshotu.
Źródło: `v_active_delays` (status P i X).

**Parametry query:**

| Param | Typ | Domyślny | Zakres | Opis |
|-------|-----|----------|--------|------|
| `limit` | int | 500 | 1–10 000 | Liczba wyników |

**Odpowiedź 200:** `list[ActiveDelay]`

```json
[
  {
    "schedule_id": 2026,
    "order_id": 513569932,
    "operating_date": "2026-05-27",
    "train_status": "P",
    "snapshot_time": "2026-05-27T15:00:00+00:00",
    "train_number": "5132",
    "train_name": "Hańcza",
    "carrier_name": "PKP Intercity",
    "first_station": "Warszawa Centralna",
    "first_station_departure": "2026-05-27T06:00:00+00:00",
    "last_station": "Gdańsk Główny",
    "last_station_arrival": "2026-05-27T09:30:00+00:00",
    "last_visited_station": "Gdynia Główna",
    "last_visited_arrival": "2026-05-27T09:15:00+00:00",
    "delay_departure_min": 23,
    "delay_arrival_min": 21
  }
]
```

> Sortowanie: `delay_departure_min DESC NULLS LAST`.
> `train_number`, `train_name`, `carrier_name` = NULL gdy brak rozkładu w `schedules`.

---

#### `GET /delays/stations/map`

Stacje z koordynatami GPS i metrykami opóźnień – do wizualizacji na mapie.
Źródło: JOIN `v_station_delay_stats` + `stations`.
Zwraca tylko stacje z `latitude IS NOT NULL AND longitude IS NOT NULL`.

**Parametry query:**

| Param | Typ | Domyślny | Zakres | Opis |
|-------|-----|----------|--------|------|
| `limit` | int | 60 | 1–200 | Liczba stacji |

**Odpowiedź 200:** `list[StationMapPoint]`

```json
[
  {
    "station_id": 33506,
    "station_name": "Warszawa Centralna",
    "latitude": 52.2297,
    "longitude": 21.0035,
    "avg_delay_min": 8.4,
    "delay_rate_pct": 50.2,
    "total_stops": 1842
  }
]
```

---

#### `GET /stats`

Statystyki bazy danych – liczba rekordów per tabela.

**Odpowiedź 200:**
```json
{
  "stations": 3259,
  "carriers": 22,
  "snapshots": 480,
  "train_operations": 184320,
  "station_stops": 3132480,
  "disruptions": 1550,
  "schedules": 35000,
  "last_snapshot": "2026-05-31T14:30:00+00:00"
}
```

---

#### `GET /predict`

Predykcja opóźnienia przez XGBoost z wyjaśnieniem SHAP.
Fallback: gdy XGB niedostępny → model baseline; gdy oba niedostępne → HTTP 503.

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `station_id` | str | **wymagany** | ID stacji PKP (np. `33506`) |
| `planned_departure` | str | **wymagany** | ISO 8601: `2026-05-31T10:00:00` |
| `day_type` | str | auto-detect | Typ dnia (CalendarService) – opcjonalne nadpisanie |
| `prev_stop_delay_min` | float | 0.0 | Opóźnienie z poprzedniego przystanku [min] |
| `planned_sequence` | int ≥1 | 1 | Numer przystanku na trasie |

**Odpowiedź 200:** `XGBPredictionResponse`

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
    {"feature": "prev_stop_delay_min", "impact": 8.3, "value": 5.0},
    {"feature": "station_id",          "impact": 2.1, "value": "33506"},
    {"feature": "hour_of_day",         "impact": 1.4, "value": 10},
    {"feature": "is_heavy_rain",       "impact": 0.8, "value": false},
    {"feature": "day_of_week",         "impact": -0.3, "value": 6}
  ]
}
```

**Odpowiedź 503:**
```json
{"detail": "Model w trakcie ładowania. Uruchom: python -m cns.ml.train_xgb"}
```

> `impact > 0` = cecha zwiększa opóźnienie; `impact < 0` = zmniejsza.
> Wartości SHAP sumują się do `predicted_delay_min - E[model]`.
> Przedziały ufności (70% CI): residua walidacyjne percentyl 15 i 85.

---

#### `GET /predict/baseline`

Predykcja opóźnienia przez model historycznych median (benchmark dla XGBoost).

**Parametry query:**

| Param | Typ | Domyślny | Opis |
|-------|-----|----------|------|
| `station_id` | str | **wymagany** | ID stacji PKP |
| `planned_departure` | str | **wymagany** | ISO 8601 |
| `day_type` | str | auto-detect | Opcjonalne nadpisanie |

**Odpowiedź 200:** `BaselinePredictionResponse`

```json
{
  "station_id": "33506",
  "station_name": "Warszawa Centralna",
  "predicted_delay_min": 7.5,
  "p75_delay_min": 14.0,
  "p90_delay_min": 28.0,
  "sample_count": 312,
  "model": "baseline",
  "model_date": "2026-05-31",
  "fallback": false
}
```

> `fallback: true` → użyto poziomu L2/L3/L4 (niewystarczająca liczba próbek dla L1).
> Hierarchia: L1 `(station, hour_bucket, day_type)` → L2 `(station, hour_bucket)` → L3 `(station)` → L4 globalny.
> `hour_bucket = hour // 2` (12 bucketów dziennie).

---

### 3.3 Modele Pydantic

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
    schedule_id: int
    order_id: int
    operating_date: Optional[str]
    train_status: Optional[str]
    snapshot_time: Optional[str]
    train_number: Optional[str]
    train_name: Optional[str]
    carrier_name: Optional[str]
    first_station: Optional[str]
    first_station_departure: Optional[str]
    last_station: Optional[str]
    last_station_arrival: Optional[str]
    last_visited_station: Optional[str]
    last_visited_arrival: Optional[str]
    delay_departure_min: Optional[int]
    delay_arrival_min: Optional[int]

class StationMapPoint(BaseModel):
    station_id: Optional[int]
    station_name: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    avg_delay_min: Optional[float]
    delay_rate_pct: Optional[float]
    total_stops: int = 0

class ExplanationItem(BaseModel):
    feature: str
    impact: float
    value: Optional[Any]

class XGBPredictionResponse(BaseModel):
    station_id: str
    station_name: Optional[str]
    predicted_delay_min: float
    p75_delay_min: Optional[float]
    confidence_interval: Optional[list[float]]  # [ci_low, ci_high]
    model: str                                   # "xgboost" | "baseline_fallback"
    model_date: Optional[str]
    explanation: Optional[list[ExplanationItem]]

class BaselinePredictionResponse(BaseModel):
    station_id: str
    station_name: Optional[str]
    predicted_delay_min: Optional[float]
    p75_delay_min: Optional[float]
    p90_delay_min: Optional[float]
    sample_count: int
    model: str = "baseline"
    model_date: Optional[str]
    fallback: bool
```

---

## 4. Kolekcja pogody — działanie i założenia

### 4.1 Źródło danych

**Open-Meteo API** (`https://api.open-meteo.com/v1/forecast`) — bezpłatne, bez klucza API.
Dokumentacja: https://open-meteo.com/en/docs

**Częstotliwość:** co 60 min, dla ~30 głównych węzłów PKP (stacje z `latitude IS NOT NULL`).
Stacje wybierane przez `storage.get_weather_stations(limit=30)` — `ORDER BY name`, pierwsze 30.

**Pola pobierane** (parametr `_FIELDS`):
```
temperature_2m, precipitation, wind_speed_10m, snowfall, visibility, cloud_cover, weather_code
```

### 4.2 Dwa rodzaje rekordów pogodowych

W każdym ticku kolektora dla każdej stacji wykonywane są **dwa** wywołania API:

| Wywołanie | Metoda | Parametr API | `is_forecast` | Liczba rekordów | Zastosowanie |
|-----------|--------|-------------|---------------|-----------------|--------------|
| `get_current()` | `current=_FIELDS` | bieżąca chwila | **FALSE** | 1 per stacja | Feature store (LATERAL JOIN) |
| `get_forecast_48h()` | `hourly=_FIELDS, forecast_days=2` | następne 2 doby | **TRUE** | 48 per stacja | API predykcji przyszłych kursów |

Łącznie: ~30 × (1 + 48) = ~1 470 rekordów per tick.

> **Uwaga historyczna:** do migracji `014` kolektor wywoływał tylko `get_forecast_48h()`.
> Skutek: wszystkie rekordy miały `is_forecast=TRUE`, a LATERAL JOIN w `mv_training_features`
> filtrował `AND wo2.is_forecast = FALSE` → kolumny pogodowe były zawsze NULL.
> Naprawione w `014_features_weather_calendar_fix.sql` + `collector.py`.

### 4.3 LATERAL JOIN — semantyka (po migracji 014)

```sql
LEFT JOIN LATERAL (
  SELECT temperature_c, precipitation_mm, wind_speed_kmh,
         snowfall_cm, visibility_m, cloud_cover_pct, weather_code
  FROM weather_observations wo2
  WHERE wo2.station_id  = ss.station_id::TEXT
    AND wo2.observed_at <= ss.planned_departure
  ORDER BY wo2.is_forecast ASC, wo2.observed_at DESC
  LIMIT 1
) wo ON TRUE
```

**Priorytety wyboru:**
1. Preferuje obserwacje (`is_forecast=FALSE`, `int(False)=0`) nad prognozami (`is_forecast=TRUE`, `int(True)=1`)
2. Dla tego samego is_forecast: wybiera najnowszy `observed_at ≤ planned_departure`
3. Fallback na prognozę gdy brak obserwacji (np. dane historyczne sprzed naprawki)

**Pokrycie czasowe obserwacji:**
- `get_current()` zapisuje jedną obserwację per stację per godzinę
- Dla przystanku z `planned_departure` = 10:37, LATERAL JOIN wybierze obserwację z 10:00 (lub 09:00 jeśli 10:00 nie istnieje)
- Maksymalna luka: 1h (interwał kolektora) → błąd pogodowy zazwyczaj pomijalny

### 4.4 Typ danych i rzutowanie station_id

`stations.station_id` = **INTEGER** (tabela `stations`)
`weather_observations.station_id` = **VARCHAR(20)** (tabela `weather_observations`)

Kolektor konwertuje: `str(station_id)` w Pythonie → np. `"33506"`.
LATERAL JOIN rzutuje: `ss.station_id::TEXT` w SQL → np. `"33506"`.
Porównanie `VARCHAR = TEXT` jest poprawne w PostgreSQL. ✓

### 4.5 Jednostki i typy

| Pole API | Pole DB | Typ DB | Uwagi |
|----------|---------|--------|-------|
| `temperature_2m` | `temperature_c` | NUMERIC(5,2) | °C, 2m n.p.g. |
| `precipitation` | `precipitation_mm` | NUMERIC(6,2) | mm |
| `wind_speed_10m` | `wind_speed_kmh` | NUMERIC(6,2) | km/h, 10m n.p.g. |
| `snowfall` | `snowfall_cm` | NUMERIC(6,2) | cm |
| `visibility` | `visibility_m` | INTEGER | **metry** (nie km!) |
| `cloud_cover` | `cloud_cover_pct` | SMALLINT | % |
| `weather_code` | `weather_code` | SMALLINT | kod WMO |

`visibility`, `cloud_cover`, `weather_code` castowane przez `_to_int()` → `None` gdy API zwróci null.

### 4.6 Flagi pogodowe — progi

Obliczane bezpośrednio w `mv_training_features` z wyrażeń BOOLEAN:

| Flaga | Warunek SQL | Prog |
|-------|-------------|------|
| `is_snowing` | `snowfall_cm > 1` | opady śniegu > 1 cm/h |
| `is_heavy_rain` | `precipitation_mm > 5` | opady > 5 mm/h |
| `is_strong_wind` | `wind_speed_kmh > 70` | wiatr > 70 km/h |
| `is_frost` | `temperature_c < -10` | temperatura < -10°C |
| `is_dense_fog` | `visibility_m < 200` | widzialność < 200 m |

**Semantyka NULL w SQL:** `NULL > 1 = NULL` (nie TRUE) → brak danych pogodowych daje `FALSE` dla wszystkich flag. ✓

---

## 5. Kalendarz — działanie i założenia

### 5.1 CalendarService — logika in-memory

Klasa `CalendarService` (bez zależności zewnętrznych) wyznacza typ dnia w Pythonie.
Używana przez: endpoint `/predict`, `_build_features()`, `_bootstrap_calendar()`.

**Algorytm Wielkanocny (Butcher/Meeus):**
Oblicza Wielkanoc dla dowolnego roku gregoriańskiego.
Zweryfikowane: 2023-04-09, 2024-03-31, 2025-04-20, 2026-04-05, 2027-03-28. Zawsze niedziela. ✓

**12 świąt ustawowych per rok:**
```
Stałe:   1.01  6.01  1.05  3.05  15.08  1.11  11.11  25.12  26.12
Ruchome: Wielkanoc  Poniedziałek Wielkanocny  Boże Ciało (+60 dni od Wielkanocy)
```

**Hierarchia priorytetów `get_day_type(d, zone="B")`:**
```
HOLIDAY > WINTER_BREAK > SUMMER_BREAK > WEEKEND >
LONG_WEEKEND > HOLIDAY_EVE > HOLIDAY_RETURN > WORKING
```

**Wakacje letnie:** 1 lipca – 31 sierpnia (stała reguła, niezależna od roku). 62 dni. ✓

**Ferie zimowe:** hardcoded dla lat 2024–2030 per strefa A/B/C.
Brak danych MEN dla roku > 2030 → `_is_winter_break()` zwraca False (nie `WINTER_BREAK`).

**LONG_WEEKEND (pomost):**
Dzień roboczy między dwoma dniami wolnymi. Warunek:
```python
_is_nonworking(d - 1day) AND _is_nonworking(d + 1day) AND d.weekday() < 5 AND NOT _is_holiday(d)
```
Przykład: 2 maja 2025 (piątek) między 1.05 czwartek (święto) a 3.05 sobota (też święto). ✓

### 5.2 Tabela `calendar_events` — co jest przechowywane

`generate_events(year_from, year_to)` generuje **tylko** rekordy specjalne:

| Typ | `zone` | Przykładowy rekord |
|-----|--------|--------------------|
| HOLIDAY | NULL | `(2026-04-05, NULL, 'HOLIDAY', 'Wielkanoc')` |
| WINTER_BREAK | 'A'/'B'/'C' | `(2026-02-02, 'A', 'WINTER_BREAK', 'Ferie zimowe strefa A')` |
| SUMMER_BREAK | NULL | `(2026-07-01, NULL, 'SUMMER_BREAK', 'Wakacje letnie')` |

**Nie są przechowywane:** WEEKEND, WORKING, LONG_WEEKEND, HOLIDAY_EVE, HOLIDAY_RETURN.
Tabela jest rzadka — typowy rok ma ~200–400 wpisów (12 świąt + ~60 ferii × 3 strefy + 62 wakacje).

### 5.3 `day_type` w feature store — semantyka (po migracji 014)

```sql
COALESCE(
    ce.day_type,
    CASE WHEN EXTRACT(DOW FROM ss.planned_departure) IN (0, 6) THEN 'WEEKEND'
         ELSE 'WORKING'
    END
) AS day_type
```

**Mapowanie:**
| Dzień | Wartość `day_type` w feature store |
|-------|------------------------------------|
| Święto | `'HOLIDAY'` (z calendar_events) |
| Ferie strefa B | `'WINTER_BREAK'` (z calendar_events, zone='B') |
| Wakacje letnie | `'SUMMER_BREAK'` (z calendar_events, zone=NULL) |
| Sobota/Niedziela | `'WEEKEND'` (COALESCE → EXTRACT DOW) |
| Dzień roboczy | `'WORKING'` (COALESCE fallback) |
| Długi weekend | `'WORKING'` ⚠️ (brak w calendar_events, traktowany jak zwykły dzień) |
| Dzień przed/po święcie | `'WORKING'` ⚠️ (j.w.) |

> **Ograniczenie:** LONG_WEEKEND, HOLIDAY_EVE, HOLIDAY_RETURN nie są rozróżniane w feature store.
> Dostępne są tylko via `CalendarService.get_day_type()` (runtime prediction).
> Wpływ na model: te typy dni stanowią <5% danych; utrata informacji akceptowalna.

> **Uwaga historyczna:** przed migracją `014`, `day_type=NULL` dla WEEKEND i WORKING.
> Model trenowany na NULL, predykcja wysyłała konkretny string → niespójność train/predict.
> Naprawione przez COALESCE z DOW w migracji `014`.

### 5.4 Cykl życia kalendarza

**Bootstrap (przy starcie):**
```python
_bootstrap_calendar()
  → is_calendar_populated()       # SELECT COUNT(*) FROM calendar_events
  → jeśli 0 wpisów:
      generate_events(today.year, today.year + 5)
      save_calendar_events(rows)  # ON CONFLICT DO UPDATE
```

**Aktualizacja roczna:**
```python
_update_calendar_if_needed()      # wywołane w każdym _tick()
  → jeśli today.year != _last_calendar_year AND today.month==1 AND today.day==1:
      _bootstrap_calendar()       # generuje year+5 do przodu
```

> **Ograniczenie:** jeśli kolektor nie działa 1 stycznia, kalendarz nie jest aktualizowany
> do następnego startu (wtedy bootstrap sprawdzi `is_calendar_populated` = True i pominie).
> Mitygacja: kalendarz generowany 5 lat do przodu przy każdym bootstrapie,
> więc brak danych jest praktycznie niemożliwy przez co najmniej 5 lat. ✓

### 5.5 Zapis do bazy — ON CONFLICT

```sql
INSERT INTO calendar_events (event_date, zone, day_type, event_name)
VALUES (%s, %s, %s, %s)
ON CONFLICT (event_date, zone) DO UPDATE SET
    day_type=EXCLUDED.day_type,
    event_name=EXCLUDED.event_name
```

`UNIQUE NULLS NOT DISTINCT (event_date, zone)` — PostgreSQL 15+.
Dwa wiersze z `zone=NULL` i tą samą `event_date` kolidują (NULL traktowane jako równe). ✓

`day_type` zapisywany jako string (`.value` z enum `DayType`):
```python
r["day_type"].value if hasattr(r["day_type"], "value") else r["day_type"]
```
→ `"HOLIDAY"`, `"WINTER_BREAK"`, `"SUMMER_BREAK"`. ✓
