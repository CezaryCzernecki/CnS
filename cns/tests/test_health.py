"""
Testy HealthChecker — logika progów CRITICAL/WARNING/OK i wykrywania luk.

Używa wyłącznie `compute_health_status` (czysta funkcja) — nie wymaga bazy danych.
"""

from datetime import datetime, timezone, timedelta

import pytest

from cns.collector.health import (
    CRITICAL_THRESHOLD_MIN,
    EXPECTED_SNAPSHOTS_24H,
    GAP_THRESHOLD_MIN,
    WARNING_COVERAGE,
    GapInfo,
    HealthStatus,
    compute_health_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _snaps_every_15min(count: int, end: datetime = _NOW) -> list[datetime]:
    """Generuje `count` snapshotów co 15 min wstecz od `end`."""
    return [end - timedelta(minutes=i * 15) for i in range(count)]


# ---------------------------------------------------------------------------
# Status CRITICAL
# ---------------------------------------------------------------------------

class TestCritical:
    def test_brak_snapshotow_to_critical(self):
        s = compute_health_status([], now=_NOW)
        assert s.status == "CRITICAL"
        assert s.minutes_since_snapshot is None
        assert s.snapshots_last_24h == 0

    def test_stary_snapshot_to_critical(self):
        old = _NOW - timedelta(minutes=CRITICAL_THRESHOLD_MIN + 5)
        s = compute_health_status([old], now=_NOW)
        assert s.status == "CRITICAL"
        assert s.minutes_since_snapshot == CRITICAL_THRESHOLD_MIN + 5

    def test_snapshot_dokladnie_na_progu_to_critical(self):
        threshold = _NOW - timedelta(minutes=CRITICAL_THRESHOLD_MIN)
        s = compute_health_status([threshold], now=_NOW)
        assert s.status == "CRITICAL"

    def test_snapshot_sekundę_przed_progiem_nie_jest_critical(self):
        just_before = _NOW - timedelta(minutes=CRITICAL_THRESHOLD_MIN - 1)
        # Jeden świeży snapshot – ale tylko 1 snapshot, więc WARNING (coverage)
        s = compute_health_status([just_before], now=_NOW)
        assert s.status != "CRITICAL"
        assert s.minutes_since_snapshot == CRITICAL_THRESHOLD_MIN - 1


# ---------------------------------------------------------------------------
# Status WARNING
# ---------------------------------------------------------------------------

class TestWarning:
    def test_niska_pokrywalnosc_to_warning(self):
        # 70 snapshotów < 77 (80% z 96)
        snaps = _snaps_every_15min(70)
        s = compute_health_status(snaps, now=_NOW)
        assert s.status == "WARNING"
        assert s.snapshots_last_24h == 70

    def test_prog_warning_to_76_snapshotow(self):
        # int(96 * 0.80) = 76, więc próg to < 76
        # 75 → WARNING
        snaps75 = _snaps_every_15min(75)
        s75 = compute_health_status(snaps75, now=_NOW)
        assert s75.status == "WARNING"

        # 76 → OK (≥ 76)
        snaps76 = _snaps_every_15min(76)
        s76 = compute_health_status(snaps76, now=_NOW)
        assert s76.status == "OK"

    def test_niski_coverage_przelicza_poprawnie(self):
        snaps = _snaps_every_15min(50)
        s = compute_health_status(snaps, now=_NOW)
        assert s.snapshots_last_24h == 50
        assert s.expected_snapshots_24h == EXPECTED_SNAPSHOTS_24H


# ---------------------------------------------------------------------------
# Status OK
# ---------------------------------------------------------------------------

class TestOk:
    def test_pelne_pokrycie_to_ok(self):
        snaps = _snaps_every_15min(96)
        s = compute_health_status(snaps, now=_NOW)
        assert s.status == "OK"
        assert s.snapshots_last_24h == 96
        assert s.minutes_since_snapshot == 0

    def test_80_procent_pokrycia_to_ok(self):
        snaps = _snaps_every_15min(77)
        s = compute_health_status(snaps, now=_NOW)
        assert s.status == "OK"

    def test_ok_zawiera_poprawne_pola(self):
        snaps = _snaps_every_15min(90)
        s = compute_health_status(snaps, now=_NOW)
        assert isinstance(s.last_snapshot_at, datetime)
        assert s.last_snapshot_at.tzinfo is not None
        assert s.minutes_since_snapshot == 0
        assert s.checked_at == _NOW


# ---------------------------------------------------------------------------
# Wykrywanie luk (gaps)
# ---------------------------------------------------------------------------

class TestGapDetection:
    def test_brak_luk_przy_regularnych_snapshotach(self):
        snaps = _snaps_every_15min(96)
        s = compute_health_status(snaps, now=_NOW)
        assert s.gaps == []

    def test_wykrywa_lupe_wieksza_niz_prog(self):
        # Snapshot o 08:00 i następny o 08:30 → luka 30 min
        base = _NOW - timedelta(hours=4)
        snaps = [base, base + timedelta(minutes=30), base + timedelta(minutes=45)]
        snaps += _snaps_every_15min(10, end=base - timedelta(minutes=15))
        s = compute_health_status(snaps, now=_NOW)
        luki = [g for g in s.gaps if g.gap_minutes >= GAP_THRESHOLD_MIN]
        assert len(luki) >= 1
        assert luki[0].gap_minutes == 30

    def test_nie_wykrywa_lupy_na_progu(self):
        # Przerwa dokładnie GAP_THRESHOLD_MIN → NIE jest luką (warunek >)
        base = _NOW - timedelta(hours=4)
        snaps = [base, base + timedelta(minutes=GAP_THRESHOLD_MIN)]
        s = compute_health_status(snaps, now=_NOW)
        assert len(s.gaps) == 0

    def test_wykrywa_wiele_luk(self):
        # Budujemy regularne snapshoty co 15 min, ale z dwiema lukami:
        # luka #1: brak snapshotów między godz 20h a 19h temu (godzinna przerwa)
        # luka #2: brak snapshotów między 10h a 8h temu (2h przerwa)
        base = _NOW - timedelta(hours=23)
        # Regularne snapshoty z wyjątkiem dwóch okien
        snaps = [
            base + timedelta(minutes=i * 15)
            for i in range(93)  # 93 × 15 min ≈ 23h
            if not (timedelta(hours=3) < base + timedelta(minutes=i * 15) - base < timedelta(hours=4))
            and not (timedelta(hours=13) < base + timedelta(minutes=i * 15) - base < timedelta(hours=15))
        ]
        s = compute_health_status(snaps, now=_NOW)
        # Dwie luki (każda >20 min)
        assert len(s.gaps) == 2
        gap_mins = sorted(g.gap_minutes for g in s.gaps)
        assert gap_mins[0] >= GAP_THRESHOLD_MIN
        assert gap_mins[1] >= GAP_THRESHOLD_MIN

    def test_gap_info_zawiera_poprawne_pola(self):
        a = _NOW - timedelta(hours=5)
        b = _NOW - timedelta(hours=4)   # gap 1h = 60 min
        snaps = [a, b, _NOW - timedelta(minutes=5)]
        s = compute_health_status(snaps, now=_NOW)
        assert len(s.gaps) >= 1
        gap = s.gaps[0]
        assert isinstance(gap, GapInfo)
        assert gap.gap_minutes == 60
        assert gap.from_time == a.isoformat()
        assert gap.to_time == b.isoformat()

    def test_pojedynczy_snapshot_brak_luk(self):
        snaps = [_NOW - timedelta(minutes=5)]
        s = compute_health_status(snaps, now=_NOW)
        assert s.gaps == []


# ---------------------------------------------------------------------------
# Wartości minutsSinceSnapshot i pola HealthStatus
# ---------------------------------------------------------------------------

class TestHealthStatusFields:
    def test_minutes_since_snapshot_poprawny(self):
        snaps = [_NOW - timedelta(minutes=8)]
        s = compute_health_status(snaps, now=_NOW)
        assert s.minutes_since_snapshot == 8

    def test_last_snapshot_at_to_max_z_listy(self):
        snaps = [
            _NOW - timedelta(minutes=20),
            _NOW - timedelta(minutes=5),   # ← najnowszy
            _NOW - timedelta(minutes=15),
        ]
        s = compute_health_status(snaps, now=_NOW)
        expected = (_NOW - timedelta(minutes=5)).replace(tzinfo=timezone.utc)
        assert s.last_snapshot_at == expected

    def test_naive_datetime_jest_traktowany_jako_utc(self):
        naive = _NOW.replace(tzinfo=None) - timedelta(minutes=10)
        s = compute_health_status([naive], now=_NOW)
        assert s.minutes_since_snapshot == 10

    def test_customowe_progi(self):
        snaps = _snaps_every_15min(60)  # 60 < 80% z 80 = 64 → WARNING
        s = compute_health_status(snaps, now=_NOW, expected_24h=80)
        assert s.status == "WARNING"
        assert s.expected_snapshots_24h == 80

    def test_checked_at_domyslnie_utcnow(self):
        from datetime import datetime, timezone
        s = compute_health_status([])
        assert s.checked_at.tzinfo is not None
        # Sprawdź że checked_at jest w przybliżeniu teraz (< 5s różnicy)
        diff = abs((datetime.now(timezone.utc) - s.checked_at).total_seconds())
        assert diff < 5
