"""Parse raw SportsDataIO rows into typed models.

Keeps identity fields (``PlayerID``, ``Name``, ``Team``, ``Position``) — which
are *not* scrambled — for joining to nflverse, and collects the scrambled
numeric stats into a ``stats`` dict keyed by SportsDataIO field name so the
reconciliation layer can iterate them generically.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .data_providers import _as_float
from .name_matching import normalize_name
from .scrambled_fields import PROJECTION_FIELDS, REALIZED_FIELD_MAP


class SportsDataStat(BaseModel):
    """One player's realized season stats as returned by SportsDataIO.

    ``stats`` holds the scrambled realized values keyed by SportsDataIO field
    name (e.g. ``"ReceivingYards"``); identity fields are top-level.
    """

    player_id: str
    name: str
    team: str = ""
    position: str = ""
    season: int = 0
    played: float = 0.0
    stats: dict[str, float] = Field(default_factory=dict)

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


class SportsDataProjection(BaseModel):
    """One player's season projection (forward-looking, always approximate)."""

    player_id: str
    name: str
    team: str = ""
    position: str = ""
    season: int = 0
    projections: dict[str, float] = Field(default_factory=dict)

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


class NewsItem(BaseModel):
    """One SportsDataIO news item.  Free text — not scrambled, no validation."""

    title: str = ""
    content: str = ""
    source: str = ""
    url: str = ""
    updated: str = ""


def _id(row: dict) -> Optional[str]:
    pid = row.get("PlayerID")
    return str(pid) if pid not in (None, "") else None


def parse_season_stats(rows: list[dict]) -> list[SportsDataStat]:
    """Parse a PlayerSeasonStats response into :class:`SportsDataStat` rows."""
    out: list[SportsDataStat] = []
    for row in rows:
        pid = _id(row)
        if pid is None:
            continue
        stats = {
            field: _as_float(row.get(field)) or 0.0
            for field in REALIZED_FIELD_MAP
        }
        out.append(
            SportsDataStat(
                player_id=pid,
                name=str(row.get("Name") or ""),
                team=str(row.get("Team") or ""),
                position=str(row.get("Position") or ""),
                season=int(_as_float(row.get("Season")) or 0),
                played=_as_float(row.get("Played")) or 0.0,
                stats=stats,
            )
        )
    return out


def parse_season_projections(rows: list[dict]) -> list[SportsDataProjection]:
    """Parse a PlayerSeasonProjectionStats response."""
    out: list[SportsDataProjection] = []
    for row in rows:
        pid = _id(row)
        if pid is None:
            continue
        projections = {
            field: _as_float(row.get(field)) or 0.0 for field in PROJECTION_FIELDS
        }
        out.append(
            SportsDataProjection(
                player_id=pid,
                name=str(row.get("Name") or ""),
                team=str(row.get("Team") or ""),
                position=str(row.get("Position") or ""),
                season=int(_as_float(row.get("Season")) or 0),
                projections=projections,
            )
        )
    return out


def parse_player_news(rows: list[dict]) -> list[NewsItem]:
    """Parse a NewsByPlayerID response into :class:`NewsItem` rows, newest first."""
    out: list[NewsItem] = []
    for row in rows:
        out.append(
            NewsItem(
                title=str(row.get("Title") or ""),
                content=str(row.get("Content") or ""),
                source=str(row.get("Source") or row.get("OriginalSource") or ""),
                url=str(row.get("Url") or row.get("OriginalSourceUrl") or ""),
                updated=str(row.get("Updated") or row.get("TimeAgo") or ""),
            )
        )
    # SportsDataIO returns newest-first, but sort defensively on the ISO ``Updated``.
    out.sort(key=lambda n: n.updated, reverse=True)
    return out
