"""Sleeper league settings → normalized league format detection.

Reads a Sleeper league dict (``GET /league/{id}``) and derives the
format facts that drive valuation: team count, number of startable QBs
(superflex detection), PPR level, TE premium, dynasty vs redraft, and
starter slots per position.  The result feeds:

* FantasyCalc / DraftSharks query parameters (so fetched values match
  the league's actual format),
* positional multipliers for :class:`~src.trade_calculator.TradeCalculator`,
* starter-slot targets for positional-need scoring.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


class LeagueFormat(BaseModel):
    """Normalized description of a fantasy league's format.

    Frozen (hashable) so it can be passed to ``st.cache_data`` functions.
    """

    model_config = ConfigDict(frozen=True)

    num_teams: int = 12
    num_qbs: int = 2  # startable QBs (QB + SUPER_FLEX slots); 2+ = superflex
    ppr: float = 1.0
    te_premium: bool = False
    is_dynasty: bool = True
    starter_slots: tuple[tuple[str, int], ...] = (
        ("QB", 2),
        ("RB", 2),
        ("WR", 3),
        ("TE", 1),
    )

    def slots_dict(self) -> dict[str, int]:
        return dict(self.starter_slots)

    @property
    def is_superflex(self) -> bool:
        return self.num_qbs >= 2

    def cache_key(self) -> str:
        """Stable string key for Streamlit caching."""
        slots = ",".join(f"{pos}{n}" for pos, n in self.starter_slots)
        return (
            f"t{self.num_teams}-qb{self.num_qbs}-ppr{self.ppr}"
            f"-tep{int(self.te_premium)}-dyn{int(self.is_dynasty)}-{slots}"
        )

    def describe(self) -> str:
        """Human-readable summary, e.g. ``12-team · Superflex · Full PPR``."""
        qb = "Superflex" if self.is_superflex else "1QB"
        ppr = {1.0: "Full PPR", 0.5: "Half PPR", 0.0: "Non-PPR"}.get(
            self.ppr, f"{self.ppr} PPR"
        )
        parts = [f"{self.num_teams}-team", qb, ppr]
        if self.te_premium:
            parts.append("TE Premium")
        parts.append("Dynasty" if self.is_dynasty else "Redraft")
        return " · ".join(parts)


def detect_league_format(league: dict) -> LeagueFormat:
    """Derive a :class:`LeagueFormat` from a Sleeper league dict."""
    roster_positions: list[str] = league.get("roster_positions") or []
    scoring: dict = league.get("scoring_settings") or {}
    settings: dict = league.get("settings") or {}

    counts: dict[str, int] = {}
    for slot in roster_positions:
        counts[slot] = counts.get(slot, 0) + 1

    num_qbs = counts.get("QB", 0) + counts.get("SUPER_FLEX", 0)
    if num_qbs == 0:
        num_qbs = 1

    # Dedicated slots per position; generic FLEX counted toward WR depth
    # (a simplification — most flex spots end up as WRs in practice).
    starter_slots = (
        ("QB", num_qbs),
        ("RB", counts.get("RB", 2)),
        ("WR", counts.get("WR", 2) + counts.get("FLEX", 0) + counts.get("REC_FLEX", 0)),
        ("TE", max(counts.get("TE", 1), 1)),
    )

    ppr = _snap(float(scoring.get("rec", 0.0)), (0.0, 0.5, 1.0))
    te_premium = float(scoring.get("bonus_rec_te", 0.0)) > 0

    # Sleeper league settings: type 2 = dynasty, 1 = keeper, 0 = redraft
    is_dynasty = settings.get("type", 2) == 2

    return LeagueFormat(
        num_teams=int(league.get("total_rosters") or 12),
        num_qbs=num_qbs,
        ppr=ppr,
        te_premium=te_premium,
        is_dynasty=is_dynasty,
        starter_slots=starter_slots,
    )


def fantasycalc_params(fmt: LeagueFormat) -> dict:
    """Query params for ``FantasyCalc().rankings.list``.

    Plain strings matching the client's enum values, so this module works
    without importing the generated (gitignored) parse_apis client.
    """
    return {
        "ppr": {1.0: "1", 0.5: "0.5", 0.0: "0"}[_snap(fmt.ppr, (0.0, 0.5, 1.0))],
        "num_qbs": "2" if fmt.is_superflex else "1",
        "num_teams": str(int(_snap(fmt.num_teams, (10, 12, 14)))),
        "is_dynasty": fmt.is_dynasty,
    }


def draftsharks_params(fmt: LeagueFormat) -> dict:
    """Query params for ``DraftSharks().players.list``."""
    if fmt.ppr >= 0.75:
        scoring = "ppr"
    elif fmt.ppr >= 0.25:
        scoring = "half-ppr"
    else:
        scoring = "non-ppr"
    return {
        "scoring": scoring,
        "league_type": "superflex" if fmt.is_superflex else "standard",
        "is_dynasty": "true" if fmt.is_dynasty else "false",
    }


def position_multipliers(fmt: LeagueFormat) -> dict[str, float]:
    """Positional value multipliers appropriate for the league format."""
    return {
        "QB": 1.30 if fmt.is_superflex else 1.00,
        "RB": 1.00,
        "WR": 1.00,
        "TE": 1.20 if fmt.te_premium else 1.10,
    }


def _snap(value: float, options: tuple[float, ...]) -> float:
    """Snap *value* to the nearest allowed option."""
    return min(options, key=lambda o: abs(o - value))
