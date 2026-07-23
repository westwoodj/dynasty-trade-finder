"""Validate/correct scrambled SportsDataIO stats against nflverse.

Policy (per product decision): nflverse (nflreadpy) is the **ground truth** for
realized stats.  For every matched player, each scrambled realized-stat field is
replaced with the confirmed nflverse value, and any meaningful divergence is
recorded as a :class:`Correction` for the data-quality panel.  Forward
projections have no nflverse counterpart and are handled separately (kept, and
flagged *approximate* in the UI).

Join order:
1. **By ID** — SportsDataIO ``PlayerID`` equals nflverse ``fantasy_data_id``.
2. **By name** — exact normalized-name match with a compatible position
   (deliberately not fuzzy: a wrong match would silently mis-correct a player).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .name_matching import normalize_name
from .nfl_stats import PlayerPerformance
from .scrambled_fields import REALIZED_FIELD_MAP
from .sportsdata_provider import SportsDataStat

# Relative divergence above which a field is counted as "corrected" (scrambling
# is ±5-20%, so anything past ~1% is a genuine scramble, not float noise).
RECORD_THRESHOLD = 0.01


class Correction(BaseModel):
    """A scrambled SportsDataIO value replaced by the nflverse value."""

    field: str  # SportsDataIO field name
    sportsdata_value: float
    nfl_value: float

    @property
    def pct_off(self) -> Optional[float]:
        """SportsDataIO's divergence from the truth, as a fraction of it."""
        if self.nfl_value == 0:
            return None if self.sportsdata_value == 0 else float("inf")
        return abs(self.sportsdata_value - self.nfl_value) / abs(self.nfl_value)


class ReconciledPlayer(BaseModel):
    """A player's realized stats after validation against nflverse."""

    player_id: str
    name: str
    team: str = ""
    position: str = ""
    season: int = 0
    matched: bool = False
    match_method: str = ""  # "id" | "name" | ""
    # Final realized values keyed by SportsDataIO field name (nflverse values
    # for matched players; original SportsDataIO values when unmatched).
    stats: dict[str, float] = Field(default_factory=dict)
    corrections: list[Correction] = Field(default_factory=list)
    gsis_id: Optional[str] = None
    sleeper_id: Optional[str] = None

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


class ValidationReport(BaseModel):
    """Summary of a reconciliation run for the data-quality panel."""

    total: int = 0
    matched_by_id: int = 0
    matched_by_name: int = 0
    unmatched: int = 0
    corrected_fields: int = 0
    players_corrected: int = 0
    unmatched_names: list[str] = Field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.matched_by_id + self.matched_by_name

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total else 0.0


def _positions_compatible(a: str, b: str) -> bool:
    """True when two position labels are absent or the same (case-insensitive)."""
    a, b = a.strip().upper(), b.strip().upper()
    return not a or not b or a == b


def reconcile(
    sdio_stats: list[SportsDataStat],
    nfl_perf: list[PlayerPerformance],
    record_threshold: float = RECORD_THRESHOLD,
) -> tuple[list[ReconciledPlayer], ValidationReport]:
    """Correct *sdio_stats* against *nfl_perf*; return rows + a report."""
    by_fdid: dict[str, PlayerPerformance] = {}
    by_name: dict[str, PlayerPerformance] = {}
    for p in nfl_perf:
        if p.fantasy_data_id:
            by_fdid[p.fantasy_data_id] = p
        by_name.setdefault(p.normalized_name, p)

    report = ValidationReport(total=len(sdio_stats))
    out: list[ReconciledPlayer] = []

    for row in sdio_stats:
        match: Optional[PlayerPerformance] = None
        method = ""

        by_id = by_fdid.get(row.player_id)
        if by_id is not None:
            match, method = by_id, "id"
        else:
            by_nm = by_name.get(row.normalized_name)
            if by_nm is not None and _positions_compatible(row.position, by_nm.position):
                match, method = by_nm, "name"

        if match is None:
            report.unmatched += 1
            report.unmatched_names.append(row.name)
            out.append(
                ReconciledPlayer(
                    player_id=row.player_id,
                    name=row.name,
                    team=row.team,
                    position=row.position,
                    season=row.season,
                    matched=False,
                    stats=dict(row.stats),  # unvalidated SportsDataIO values
                )
            )
            continue

        if method == "id":
            report.matched_by_id += 1
        else:
            report.matched_by_name += 1

        final_stats: dict[str, float] = {}
        corrections: list[Correction] = []
        for field, attr in REALIZED_FIELD_MAP.items():
            sdio_val = float(row.stats.get(field, 0.0))
            nfl_val = float(getattr(match, attr, 0.0))
            final_stats[field] = nfl_val  # correct to nflverse
            denom = abs(nfl_val) if nfl_val else max(abs(sdio_val), 1.0)
            if abs(sdio_val - nfl_val) / denom > record_threshold:
                corrections.append(
                    Correction(field=field, sportsdata_value=sdio_val, nfl_value=nfl_val)
                )

        if corrections:
            report.players_corrected += 1
            report.corrected_fields += len(corrections)

        out.append(
            ReconciledPlayer(
                player_id=row.player_id,
                name=match.name or row.name,
                team=match.team or row.team,
                position=match.position or row.position,
                season=row.season or match.season,
                matched=True,
                match_method=method,
                stats=final_stats,
                corrections=corrections,
                gsis_id=match.gsis_id or None,
                sleeper_id=match.sleeper_id,
            )
        )

    return out, report
