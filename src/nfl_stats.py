"""nflverse player performance data via :mod:`nflreadpy`.

This is the *confirmed, unscrambled* source of realized player production.  It
provides season and weekly stats plus the ffverse ID crosswalk, and is used
both to populate the Performance tab and to validate/correct the scrambled
SportsDataIO stats (see :mod:`src.stat_validation`).

nflreadpy returns **polars** DataFrames and this module works with them natively
(no pandas boundary).  nflreadpy caches downloads to disk itself — pointed at
``data/nflreadpy`` below — so repeated loads in a session are cheap.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

# Point nflreadpy's on-disk cache at the repo's data/ dir before it is imported
# (its config reads these env vars once, at import time).
_CACHE_DIR = Path("data") / "nflreadpy"
os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DIR", str(_CACHE_DIR))

import nflreadpy as nfl  # noqa: E402  (import after env setup)
from pydantic import BaseModel  # noqa: E402

from .name_matching import normalize_name  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class PlayerPerformance(BaseModel):
    """One player's realized season production (from nflverse, unscrambled)."""

    # Identity / join keys
    gsis_id: str = ""
    sleeper_id: Optional[str] = None
    fantasy_data_id: Optional[str] = None  # == SportsDataIO PlayerID
    name: str = ""
    position: str = ""
    team: str = ""
    season: int = 0

    # Realized stats
    games: float = 0.0
    completions: float = 0.0
    attempts: float = 0.0
    passing_yards: float = 0.0
    passing_tds: float = 0.0
    passing_interceptions: float = 0.0
    carries: float = 0.0
    rushing_yards: float = 0.0
    rushing_tds: float = 0.0
    receptions: float = 0.0
    targets: float = 0.0
    receiving_yards: float = 0.0
    receiving_tds: float = 0.0
    fantasy_points: float = 0.0
    fantasy_points_ppr: float = 0.0
    target_share: Optional[float] = None

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    @property
    def fantasy_points_ppr_per_game(self) -> float:
        return self.fantasy_points_ppr / self.games if self.games else 0.0


# Columns pulled from the nflverse player-stats frame (all confirmed present in
# load_player_stats(..., summary_level="reg")).
_STAT_COLUMNS = (
    "games",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "fantasy_points",
    "fantasy_points_ppr",
    "target_share",
)


def _f(value: object) -> float:
    try:
        if value is None:
            return 0.0
        f = float(value)
        return 0.0 if f != f else f  # guard NaN
    except (TypeError, ValueError):
        return 0.0


def _id_str(value: object) -> Optional[str]:
    """Coerce a crosswalk ID (often a float like 12345.0) to a clean string."""
    if value is None:
        return None
    try:
        f = float(value)
        if f != f:  # NaN
            return None
        return str(int(f))
    except (TypeError, ValueError):
        s = str(value).strip()
        return s or None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def current_season() -> int:
    """The current NFL season year per nflreadpy."""
    return int(nfl.get_current_season())


def _crosswalk_by_gsis() -> dict[str, dict]:
    """gsis_id -> {sleeper_id, fantasy_data_id} from the ffverse crosswalk."""
    try:
        xw = nfl.load_ff_playerids()
    except Exception as err:  # pragma: no cover - network/parse guard
        logger.warning("Could not load ffverse crosswalk: %s", err)
        return {}
    out: dict[str, dict] = {}
    for row in xw.to_dicts():
        gsis = row.get("gsis_id")
        if not gsis:
            continue
        out[str(gsis)] = {
            "sleeper_id": _id_str(row.get("sleeper_id")),
            "fantasy_data_id": _id_str(row.get("fantasy_data_id")),
        }
    return out


def fetch_season_stats(season: int) -> list[PlayerPerformance]:
    """Realized regular-season player stats for *season* (with ID crosswalk).

    Falls back to the prior season once when *season* has no data yet (e.g.
    the current season before Week 1), so the app always shows something.
    """
    df = nfl.load_player_stats(season, summary_level="reg")
    if df.is_empty() and season > 1999:
        logger.info("No %s regular-season stats yet; falling back to %s", season, season - 1)
        season -= 1
        df = nfl.load_player_stats(season, summary_level="reg")
    if df.is_empty():
        return []

    crosswalk = _crosswalk_by_gsis()
    out: list[PlayerPerformance] = []
    for row in df.to_dicts():
        gsis = str(row.get("player_id") or "")
        xref = crosswalk.get(gsis, {})
        stats = {col: _f(row.get(col)) for col in _STAT_COLUMNS}
        out.append(
            PlayerPerformance(
                gsis_id=gsis,
                sleeper_id=xref.get("sleeper_id"),
                fantasy_data_id=xref.get("fantasy_data_id"),
                name=str(row.get("player_display_name") or row.get("player_name") or ""),
                position=str(row.get("position") or ""),
                team=str(row.get("recent_team") or ""),
                season=int(season),
                **stats,
            )
        )
    return out


def production_signal_map(
    perf: list[PlayerPerformance], min_games: int = 4
) -> dict[str, float]:
    """Map players to a recent-production signal in [-1, 1] for the value model.

    Within each skill position (QB/RB/WR/TE), players are ranked by fantasy PPG
    and mapped to their percentile, centered so the positional median is 0, the
    top is +1, and the bottom is -1.  Players below *min_games* (too small a
    sample) are omitted (signal 0 downstream).  Keyed by ``sleeper_id`` and by
    ``name:<normalized>`` so the engine can look up either.
    """
    signals: dict[str, float] = {}
    skill = {"QB", "RB", "WR", "TE"}
    by_pos: dict[str, list[PlayerPerformance]] = {}
    for p in perf:
        if p.position.upper() in skill and p.games >= min_games:
            by_pos.setdefault(p.position.upper(), []).append(p)

    for players in by_pos.values():
        ranked = sorted(players, key=lambda x: x.fantasy_points_ppr_per_game)
        n = len(ranked)
        if n == 1:
            _assign(signals, ranked[0], 0.0)
            continue
        for i, player in enumerate(ranked):
            pct = i / (n - 1)  # 0 (worst) .. 1 (best)
            _assign(signals, player, 2.0 * pct - 1.0)
    return signals


def _assign(signals: dict[str, float], player: PlayerPerformance, value: float) -> None:
    if player.sleeper_id:
        signals[player.sleeper_id] = value
    signals[f"name:{player.normalized_name}"] = value


def fetch_weekly_stats(season: int) -> list[dict]:
    """Trimmed per-week fantasy production for *season* (for the weekly view)."""
    df = nfl.load_player_stats(season, summary_level="week")
    if df.is_empty() and season > 1999:
        season -= 1
        df = nfl.load_player_stats(season, summary_level="week")
    if df.is_empty():
        return []
    keep = [
        "player_id", "player_display_name", "position", "recent_team", "week",
        "fantasy_points", "fantasy_points_ppr", "targets", "receptions",
        "receiving_yards", "rushing_yards", "passing_yards",
    ]
    cols = [c for c in keep if c in df.columns]
    return df.select(cols).to_dicts()
