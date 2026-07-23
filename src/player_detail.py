"""Assemble a single player's detail bundle for the pop-up modal.

Framework-free (no Streamlit) and pure, so it's unit-testable.  It packages the
data already computed each run in ``app.py`` — the blended
:class:`~src.value_engine.PlayerValuation`, the nflverse
:class:`~src.nfl_stats.PlayerPerformance` season line, cross-source value
comparison, and league ownership.  Network/lazy pieces (FantasyCalc value
history, SportsDataIO news) stay out — the renderer pulls those via callables.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .name_matching import normalize_name
from .nfl_stats import PlayerPerformance
from .value_engine import PlayerValuation


class PlayerDetail(BaseModel):
    """Everything the player-detail modal needs for one player."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Identity / join keys
    name: str
    position: str = ""
    team: str = ""
    age: Optional[float] = None
    sleeper_id: Optional[str] = None
    fantasy_data_id: Optional[str] = None  # == SportsDataIO PlayerID (news key)
    fc_player_id: Optional[str] = None  # FantasyCalc trade-history drill-down
    value_key: Optional[str] = None  # PlayerValuation.key, for local snapshot history

    # Value block
    base_value: float = 0.0
    adjusted_value: float = 0.0
    values_by_source: dict[str, float] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)
    consensus: float = 0.0
    spread_pct: float = 0.0
    high_source: Optional[str] = None
    low_source: Optional[str] = None
    trend_frac: Optional[float] = None
    injury_risk: Optional[str] = None
    adp: Optional[float] = None

    # Realized production (nflverse) — None when unmatched
    perf: Optional[PlayerPerformance] = None

    # League ownership — None team means not rostered in this league
    owner_team: Optional[str] = None
    owner_timeline: Optional[str] = None

    @property
    def rostered(self) -> bool:
        return self.owner_team is not None


def _value_comparison(
    values_by_source: dict[str, float]
) -> tuple[float, float, Optional[str], Optional[str]]:
    """``(consensus, spread_pct, high_source, low_source)`` across sources.

    Mirrors the arbitrage math: consensus is the mean of the per-source 0–100
    values; spread is the high-to-low gap as a fraction of consensus.  With
    fewer than two sources there is no spread.
    """
    vals = dict(values_by_source)
    if not vals:
        return 0.0, 0.0, None, None
    consensus = sum(vals.values()) / len(vals)
    if len(vals) < 2:
        return consensus, 0.0, None, None
    high_source = max(vals, key=lambda s: vals[s])
    low_source = min(vals, key=lambda s: vals[s])
    spread = vals[high_source] - vals[low_source]
    spread_pct = spread / consensus if consensus > 0 else 0.0
    return consensus, spread_pct, high_source, low_source


def build_player_detail(
    *,
    name: str,
    position: str = "",
    sleeper_id: Optional[str] = None,
    valuation: Optional[PlayerValuation] = None,
    perf: Optional[PlayerPerformance] = None,
    owner: Optional[str] = None,
    owner_timeline: Optional[str] = None,
) -> PlayerDetail:
    """Bundle one player's valuation, performance, and ownership for the modal."""
    consensus = spread_pct = 0.0
    high_source = low_source = None
    if valuation is not None:
        consensus, spread_pct, high_source, low_source = _value_comparison(
            valuation.values_by_source
        )

    return PlayerDetail(
        name=name or (valuation.name if valuation else "") or (perf.name if perf else ""),
        position=position or (valuation.position if valuation else "")
        or (perf.position if perf else ""),
        team=(valuation.team if valuation else "") or (perf.team if perf else ""),
        age=valuation.age if valuation else None,
        sleeper_id=sleeper_id
        or (valuation.sleeper_id if valuation else None)
        or (perf.sleeper_id if perf else None),
        fantasy_data_id=perf.fantasy_data_id if perf else None,
        fc_player_id=valuation.fc_player_id if valuation else None,
        value_key=valuation.key if valuation else None,
        base_value=valuation.base_value if valuation else 0.0,
        adjusted_value=valuation.adjusted_value if valuation else 0.0,
        values_by_source=dict(valuation.values_by_source) if valuation else {},
        components=dict(valuation.components) if valuation else {},
        consensus=consensus,
        spread_pct=spread_pct,
        high_source=high_source,
        low_source=low_source,
        trend_frac=valuation.trend_frac if valuation else None,
        injury_risk=valuation.injury_risk if valuation else None,
        adp=valuation.adp if valuation else None,
        perf=perf,
        owner_team=owner,
        owner_timeline=owner_timeline,
    )


# ---------------------------------------------------------------------------
# Index helpers — built once per run in app.py, keyed by sleeper_id and name
# ---------------------------------------------------------------------------


def build_perf_index(
    perf: list[PlayerPerformance],
) -> dict[str, PlayerPerformance]:
    """``{sleeper_id | 'name:<norm>': PlayerPerformance}`` for modal lookups."""
    index: dict[str, PlayerPerformance] = {}
    for p in perf:
        index.setdefault(f"name:{p.normalized_name}", p)
        if p.sleeper_id:
            index.setdefault(p.sleeper_id, p)
    return index


def lookup_player(
    index: dict, sleeper_id: Optional[str], name: str
):
    """Look up a value in a dual-keyed index by ``sleeper_id`` then name.

    Works for any index keyed the way :func:`build_perf_index` keys — used for
    both the performance index and the ownership index.
    """
    if sleeper_id and sleeper_id in index:
        return index[sleeper_id]
    return index.get(f"name:{normalize_name(name)}")
