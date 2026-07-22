"""Backward-compatible value-history store.

The snapshot logic now lives in :class:`src.db.store.DtfStore` (backed by the
shared ``dtf.db`` SQLModel engine).  ``ValueStore`` remains as a thin adapter
so existing callers and tests keep working: constructing it with a ``db_path``
builds a store over a SQLite engine at that path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .db.engine import get_engine
from .db.store import DtfStore

if TYPE_CHECKING:
    from .value_engine import PlayerValuation

DEFAULT_DB = Path("data") / "value_history.db"


class ValueStore:
    """Daily valuation snapshots, delegating to :class:`DtfStore`."""

    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)
        self._store = DtfStore(get_engine(self.db_path))

    def save_snapshot(
        self,
        valuations: dict[str, "PlayerValuation"],
        snapshot_date: Optional[date] = None,
    ) -> int:
        return self._store.save_snapshot(valuations, snapshot_date)

    def has_snapshot(self, snapshot_date: Optional[date] = None) -> bool:
        return self._store.has_snapshot(snapshot_date)

    def snapshot_dates(self) -> list[str]:
        return self._store.snapshot_dates()

    def get_history(self, player_key: str, days: int = 365) -> list[tuple[str, float]]:
        return self._store.get_history(player_key, days)

    def get_deltas(self, days: int = 30) -> dict[str, float]:
        return self._store.get_deltas(days)
