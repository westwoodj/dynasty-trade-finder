"""Local SQLModel-backed persistence for the Dynasty Trade Finder.

A single SQLite file (``data/dtf.db``) durably caches Sleeper and Parse API
responses across restarts, holds one table per Parse value source, persists
user preferences, and stores value-history snapshots.  The store follows a
*cache-until-manual-refresh* policy: cached data is reused indefinitely and
only re-fetched when the caller passes ``force=True`` (wired to a Refresh
button in the UI).
"""

from .engine import default_engine, get_engine, init_db
from .store import DtfStore

__all__ = ["DtfStore", "get_engine", "default_engine", "init_db"]
