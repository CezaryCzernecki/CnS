"""
HealthChecker – monitoring procesu kolekcjonowania danych.

Progi alertów:
  CRITICAL  last_snapshot_at > 30 min temu  (kolektor przestał działać)
  WARNING   snapshots_last_24h < 77         (<80% z oczekiwanych 96)
  OK        wszystkie warunki spełnione

Luka (gap): przerwa między kolejnymi snapshotami > GAP_THRESHOLD_MIN minut.

Architektura: `compute_health_status` to czysta funkcja — testowalana bez bazy.
`HealthChecker` wykonuje zapytania i persystuje wynik.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stałe
# ---------------------------------------------------------------------------

CRITICAL_THRESHOLD_MIN = 30     # brak snapshotu > 30 min → CRITICAL
WARNING_COVERAGE = 0.80         # < 80% oczekiwanych snapshotów → WARNING
GAP_THRESHOLD_MIN = 20          # przerwa > 20 min między snapshotami to luka
EXPECTED_SNAPSHOTS_24H = 96     # co 15 min przez 24h


# ---------------------------------------------------------------------------
# Typy danych
# ---------------------------------------------------------------------------

@dataclass
class GapInfo:
    from_time: str   # ISO-format UTC
    to_time: str
    gap_minutes: int


@dataclass
class HealthStatus:
    status: str                              # OK | WARNING | CRITICAL
    last_snapshot_at: Optional[datetime]
    minutes_since_snapshot: Optional[int]
    snapshots_last_24h: int
    expected_snapshots_24h: int
    gaps: list[GapInfo] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Czysta funkcja – testowalana bez bazy
# ---------------------------------------------------------------------------

def compute_health_status(
    snapshots: list[datetime],
    now: Optional[datetime] = None,
    expected_24h: int = EXPECTED_SNAPSHOTS_24H,
    gap_threshold_min: int = GAP_THRESHOLD_MIN,
    critical_threshold_min: int = CRITICAL_THRESHOLD_MIN,
    warning_coverage: float = WARNING_COVERAGE,
) -> HealthStatus:
    """
    Oblicza stan zdrowia kolektora na podstawie listy timestampów snapshotów.

    Args:
        snapshots:  lista datetime z ostatnich 24h z operations_snapshots
        now:        punkt odniesienia (domyślnie utcnow); podaj w testach!
    """
    if now is None:
        now = datetime.now(timezone.utc)

    def _utc(dt: datetime) -> datetime:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    # Ostatni snapshot
    last_snapshot_at: Optional[datetime] = None
    minutes_since: Optional[int] = None
    if snapshots:
        last_snapshot_at = _utc(max(snapshots))
        minutes_since = max(0, int((now - last_snapshot_at).total_seconds() / 60))

    # Wykrywanie luk między kolejnymi snapshotami
    gaps: list[GapInfo] = []
    if len(snapshots) >= 2:
        sorted_snaps = sorted(_utc(s) for s in snapshots)
        for a, b in zip(sorted_snaps, sorted_snaps[1:]):
            gap_min = int((b - a).total_seconds() / 60)
            if gap_min > gap_threshold_min:
                gaps.append(GapInfo(
                    from_time=a.isoformat(),
                    to_time=b.isoformat(),
                    gap_minutes=gap_min,
                ))

    # Status
    if minutes_since is None or minutes_since >= critical_threshold_min:
        status = "CRITICAL"
    elif len(snapshots) < int(expected_24h * warning_coverage):
        status = "WARNING"
    else:
        status = "OK"

    return HealthStatus(
        status=status,
        last_snapshot_at=last_snapshot_at,
        minutes_since_snapshot=minutes_since,
        snapshots_last_24h=len(snapshots),
        expected_snapshots_24h=expected_24h,
        gaps=gaps,
        checked_at=now,
    )


# ---------------------------------------------------------------------------
# HealthChecker – wykonuje zapytania i persystuje wyniki
# ---------------------------------------------------------------------------

class HealthChecker:
    """Pobiera dane z bazy, oblicza status i zapisuje do collector_health."""

    def __init__(self, db_url: str):
        self._db_url = db_url

    def _fetch_recent_snapshots(self) -> list[datetime]:
        """Zwraca listę fetched_at z ostatnich 24h."""
        sql = """
            SELECT fetched_at
            FROM operations_snapshots
            WHERE fetched_at > NOW() - INTERVAL '24 hours'
            ORDER BY fetched_at
        """
        try:
            import psycopg
            with psycopg.connect(self._db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error("HealthChecker: błąd pobierania snapshotów: %s", e)
            return []

    def check(self) -> HealthStatus:
        snapshots = self._fetch_recent_snapshots()
        status = compute_health_status(snapshots)
        logger.info(
            "Health: %s | ostatni snapshot: %s min temu | snapshots 24h: %d/%d | luki: %d",
            status.status,
            status.minutes_since_snapshot,
            status.snapshots_last_24h,
            status.expected_snapshots_24h,
            len(status.gaps),
        )
        return status

    def save_check(self, status: HealthStatus) -> None:
        sql = """
            INSERT INTO collector_health
                (check_time, last_snapshot_at, minutes_since_snapshot,
                 snapshots_last_24h, expected_snapshots_24h, gaps, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        gaps_json = json.dumps(
            [{"from_time": g.from_time, "to_time": g.to_time, "gap_minutes": g.gap_minutes}
             for g in status.gaps]
        )
        try:
            import psycopg
            with psycopg.connect(self._db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        status.checked_at,
                        status.last_snapshot_at,
                        status.minutes_since_snapshot,
                        status.snapshots_last_24h,
                        status.expected_snapshots_24h,
                        gaps_json,
                        status.status,
                    ))
        except Exception as e:
            logger.error("HealthChecker: błąd zapisu: %s", e)
