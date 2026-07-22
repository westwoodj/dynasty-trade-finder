"""SQLite snapshot store for player-value history.

The app saves one snapshot of all valuations per day (on first load), so
local trend history accumulates organically with use — no scheduler.
FantasyCalc's trade-history endpoint backfills charts for players before
local history exists.

Stdlib ``sqlite3`` only; the database file lives under ``data/`` (gitignored).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .value_engine import PlayerValuation

DEFAULT_DB = Path("data") / "value_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date TEXT NOT NULL,
    player_key    TEXT NOT NULL,
    name          TEXT NOT NULL,
    position      TEXT NOT NULL,
    base_value    REAL NOT NULL,
    adjusted_value REAL NOT NULL,
    sources_json  TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, player_key)
)
"""


class ValueStore:
    """Daily valuation snapshots keyed by (date, player)."""

    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        valuations: dict[str, "PlayerValuation"],
        snapshot_date: Optional[date] = None,
    ) -> int:
        """Persist today's valuations; same-day re-saves overwrite in place."""
        day = (snapshot_date or date.today()).isoformat()
        rows = [
            (
                day,
                v.key,
                v.name,
                v.position,
                v.base_value,
                v.adjusted_value,
                json.dumps(v.values_by_source),
            )
            for v in valuations.values()
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def has_snapshot(self, snapshot_date: Optional[date] = None) -> bool:
        day = (snapshot_date or date.today()).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM snapshots WHERE snapshot_date = ? LIMIT 1", (day,)
            ).fetchone()
        return row is not None

    def snapshot_dates(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT snapshot_date FROM snapshots ORDER BY snapshot_date"
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------

    def get_history(self, player_key: str, days: int = 365) -> list[tuple[str, float]]:
        """``[(iso_date, adjusted_value), …]`` for the trailing *days*."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_date, adjusted_value FROM snapshots "
                "WHERE player_key = ? AND snapshot_date >= ? "
                "ORDER BY snapshot_date",
                (player_key, cutoff),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_deltas(self, days: int = 30) -> dict[str, float]:
        """Adjusted-value change per player: latest snapshot vs the baseline.

        Baseline is the most recent snapshot at least *days* older than the
        latest one, falling back to the oldest available snapshot when
        history is still shorter than the window.
        """
        dates = self.snapshot_dates()
        if len(dates) < 2:
            return {}
        latest = dates[-1]
        cutoff = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        eligible = [d for d in dates[:-1] if d <= cutoff]
        baseline = eligible[-1] if eligible else dates[0]

        with self._connect() as conn:
            latest_rows = dict(
                conn.execute(
                    "SELECT player_key, adjusted_value FROM snapshots "
                    "WHERE snapshot_date = ?",
                    (latest,),
                ).fetchall()
            )
            baseline_rows = dict(
                conn.execute(
                    "SELECT player_key, adjusted_value FROM snapshots "
                    "WHERE snapshot_date = ?",
                    (baseline,),
                ).fetchall()
            )
        return {
            key: value - baseline_rows[key]
            for key, value in latest_rows.items()
            if key in baseline_rows
        }
