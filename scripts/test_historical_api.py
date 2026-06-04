"""
Test: czy /operations/train/{scheduleId}/{orderId}/{date} zwraca dane historyczne?

Uruchomienie:
  cd /home/cezary/toralert_v0.3_complete/cns
  PKP_API_KEY=<twoj_klucz> python /home/cezary/cns/CnS/scripts/test_historical_api.py

  Lub jeśli masz .env:
  set -a && source .env && set +a
  python /home/cezary/cns/CnS/scripts/test_historical_api.py
"""

import os
import sys
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("PKP_API_KEY", "")
BASE_URL = "https://pdp-api.plk-sa.pl/api/v1"

if not API_KEY:
    print("❌  Brak PKP_API_KEY w środowisku.")
    print("   Uruchom: set -a && source .env && set +a")
    sys.exit(1)

HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}

# Pary (scheduleId, orderId) z bazy — pobrane 2026-06-02
SAMPLES = [
    (2026, 408560116, date(2026, 6, 2)),   # PR 49015 — najnowsze, MUSI działać
    (2026, 408560116, date(2026, 5, 15)),   # ten sam pociąg, 3 tyg. temu — KLUCZOWY TEST
    (2026, 408560116, date(2026, 1, 15)),   # 5 miesięcy temu
    (2026, 408560116, date(2025, 6, 15)),   # rok temu
]


def test_train(schedule_id: int, order_id: int, operating_date: date) -> dict:
    url = f"{BASE_URL}/operations/train/{schedule_id}/{order_id}/{operating_date.isoformat()}"
    print(f"\n→  GET {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"   HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            trains = data.get("trains", [])
            if trains:
                stops = trains[0].get("stations", [])
                confirmed = [s for s in stops if s.get("isConfirmed")]
                has_actual = any(
                    s.get("actualArrival") or s.get("actualDeparture")
                    for s in confirmed
                )
                return {
                    "status": "OK",
                    "trains_count": len(trains),
                    "stops_count": len(stops),
                    "confirmed_stops": len(confirmed),
                    "has_actual_times": has_actual,
                    "train_status": trains[0].get("trainStatus"),
                }
            else:
                return {"status": "EMPTY_TRAINS", "raw_keys": list(data.keys())}
        else:
            return {"status": f"HTTP_{r.status_code}", "body": r.text[:200]}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)}


print("=" * 60)
print("TEST: historyczne dane z /operations/train")
print("=" * 60)

results = {}
for sid, oid, d in SAMPLES:
    label = d.isoformat()
    result = test_train(sid, oid, d)
    results[label] = result
    status = result["status"]
    if status == "OK":
        has = "✅ ACTUAL TIMES" if result["has_actual_times"] else "⚠️  BRAK actual times"
        print(f"   {label}: {has}  "
              f"(pociąg={result['train_status']}, "
              f"potwierdzone={result['confirmed_stops']}/{result['stops_count']})")
    else:
        print(f"   {label}: ❌ {status}")

# Podsumowanie
print("\n" + "=" * 60)
print("WNIOSEK:")
ok_dates = [d for d, r in results.items() if r.get("has_actual_times")]
if not ok_dates:
    ok_dates_plain = [d for d, r in results.items() if r["status"] == "OK"]
    if ok_dates_plain:
        print("⚠️  Endpoint działa ale BRAK actual times w odpowiedzi.")
        print("   Dane historyczne niedostępne przez API.")
        print("   → Ścieżka B: scraping kolejopedia.pl + wniosek do PKP")
    else:
        print("❌  Endpoint nie zwraca danych historycznych.")
        print("   → Ścieżka B: scraping kolejopedia.pl + wniosek do PKP")
else:
    oldest = min(ok_dates)
    print(f"✅  Dane historyczne DOSTĘPNE od daty: {oldest}")
    print("   → Ścieżka A: backfill przez API")
    print(f"   Daty z danymi: {ok_dates}")

print("=" * 60)

# Sprawdź też ile limitów zostało
try:
    r = requests.get(f"{BASE_URL}/apikey/usage", headers=HEADERS, timeout=10)
    if r.status_code == 200:
        usage = r.json()
        print(f"\nLimity API: {json.dumps(usage, indent=2, ensure_ascii=False)}")
except Exception:
    pass
