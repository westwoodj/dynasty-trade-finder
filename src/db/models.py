"""SQLModel table definitions for the persistence layer.

Three groups of tables:

1. **League information** — raw Sleeper payloads cached as JSON columns plus
   fetch metadata.  Sleeper's schema is external and consumed as dicts, so it
   is not re-modeled here.
2. **One table per Parse value source** — FantasyCalc / KeepTradeCut /
   DraftSharks rows (the columns of
   :class:`~src.data_providers.NormalizedPlayerValue` plus a ``format_key``
   and ``fetched_at``), a FantasyCalc trade-history table, and a
   :class:`SourceFetch` bookkeeping table.
3. **Preferences + value history** — :class:`UserPreferences` and
   :class:`ValueSnapshot` (ported from the old ``value_store``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. League information (raw Sleeper payloads as JSON text)
# ---------------------------------------------------------------------------


class SleeperUserCache(SQLModel, table=True):
    __tablename__ = "sleeper_user_cache"

    username: str = Field(primary_key=True)
    user_id: str = ""
    payload_json: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


class SleeperLeaguesCache(SQLModel, table=True):
    __tablename__ = "sleeper_leagues_cache"

    key: str = Field(primary_key=True)  # "<user_id>/<season>"
    payload_json: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


class LeagueDataCache(SQLModel, table=True):
    __tablename__ = "league_data_cache"

    league_id: str = Field(primary_key=True)
    league_json: str = ""
    rosters_json: str = ""
    users_json: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


class DraftDataCache(SQLModel, table=True):
    __tablename__ = "draft_data_cache"

    league_id: str = Field(primary_key=True)
    drafts_json: str = ""
    traded_picks_json: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


class NflPlayersCache(SQLModel, table=True):
    __tablename__ = "nfl_players_cache"

    id: int = Field(default=1, primary_key=True)  # singleton
    payload_json: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 2. Parse value sources — one table per source, shared column set
# ---------------------------------------------------------------------------


class _PlayerValueRow(SQLModel):
    """Shared columns for the per-source value tables (not itself a table).

    Mirrors :class:`~src.data_providers.NormalizedPlayerValue` plus the
    ``format_key`` the values were fetched for and ``fetched_at``.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    format_key: str = Field(index=True)
    name: str = ""
    position: str = ""
    source: str = ""
    value: float = 0.0
    raw_value: float = 0.0
    team: str = ""
    age: Optional[float] = None
    sleeper_id: Optional[str] = None
    source_player_id: Optional[str] = None
    overall_rank: Optional[int] = None
    trend_30_day: Optional[float] = None
    adp: Optional[float] = None
    injury_risk: Optional[str] = None
    projection: Optional[float] = None
    fetched_at: datetime = Field(default_factory=utcnow)


class FantasyCalcValue(_PlayerValueRow, table=True):
    __tablename__ = "fantasycalc_value"


class KeepTradeCutValue(_PlayerValueRow, table=True):
    __tablename__ = "keeptradecut_value"


class DraftSharksValue(_PlayerValueRow, table=True):
    __tablename__ = "draftsharks_value"


# source name (NormalizedPlayerValue.source) -> table class
SOURCE_TABLES: dict[str, type[_PlayerValueRow]] = {
    "fantasycalc": FantasyCalcValue,
    "ktc": KeepTradeCutValue,
    "draftsharks": DraftSharksValue,
}


class FantasyCalcTradeHistory(SQLModel, table=True):
    __tablename__ = "fantasycalc_trade_history"

    key: str = Field(primary_key=True)  # "<fc_player_id>/<format_key>"
    points_json: str = "[]"  # [[iso_date, value], ...]
    fetched_at: datetime = Field(default_factory=utcnow)


class SourceFetch(SQLModel, table=True):
    """One row per (source, format_key) recording the last fetch.

    Presence of a row means "we have fetched this source for this format" —
    the gate for cache-until-manual-refresh even when a fetch returned zero
    rows or errored.
    """

    __tablename__ = "source_fetch"

    key: str = Field(primary_key=True)  # "<source>/<format_key>"
    source: str = ""
    format_key: str = Field(index=True)
    player_count: int = 0
    error: Optional[str] = None
    fetched_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 3. Preferences + value history
# ---------------------------------------------------------------------------


class UserPreferences(SQLModel, table=True):
    __tablename__ = "user_preferences"

    username: str = Field(primary_key=True)
    # Value-model weights
    source_weights_json: str = "[]"  # [["fantasycalc", 0.5], ...]
    age_weight: float = 0.5
    trend_weight: float = 0.0
    injury_weight: float = 0.0
    adp_divergence_weight: float = 0.0
    # League-format overrides
    override_enabled: bool = False
    num_qbs: Optional[int] = None
    ppr: Optional[float] = None
    te_premium: Optional[bool] = None
    # Selections
    selected_season: Optional[str] = None
    selected_league_id: Optional[str] = None
    # Output / trade-finder options
    fairness_weight: float = 0.5
    top_n: int = 25
    include_picks: bool = True
    mutual_only: bool = True
    timeline_fit: bool = True
    arb_threshold: float = 0.20
    updated_at: datetime = Field(default_factory=utcnow)


class ValueSnapshot(SQLModel, table=True):
    __tablename__ = "value_snapshot"

    snapshot_date: str = Field(primary_key=True)  # ISO date
    player_key: str = Field(primary_key=True)
    name: str = ""
    position: str = ""
    base_value: float = 0.0
    adjusted_value: float = 0.0
    sources_json: str = "{}"
