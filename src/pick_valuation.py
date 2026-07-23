"""Draft-pick ownership and valuation.

Turns Sleeper draft/traded-pick data into tradeable
:class:`~src.trade_calculator.TradeAsset` objects:

1. Every roster owns its own future picks; Sleeper's traded-picks list
   reassigns them (``GET /league/{id}/traded_picks``).
2. A pick's slot tier (early/mid/late) is projected from the *original*
   owner's roster strength — the league's weakest team's picks are priced
   as early picks.
3. Values come from a consensus table on the same 0–100 scale as player
   values, with a superflex rookie premium and a discount per year out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .league_settings import LeagueFormat
from .trade_calculator import TradeAsset


# Consensus pick values on the 0–100 player-value scale, keyed by
# (round, projected slot tier).  Migrated from the old app.py PICK_VALUES.
DEFAULT_PICK_VALUES: dict[tuple[int, str], float] = {
    (1, "early"): 90.0, (1, "mid"): 78.0, (1, "late"): 65.0,
    (2, "early"): 42.0, (2, "mid"): 32.0, (2, "late"): 25.0,
    (3, "early"): 16.0, (3, "mid"): 11.0, (3, "late"): 8.0,
    (4, "early"): 5.0,  (4, "mid"): 3.5,  (4, "late"): 2.5,
}

SUPERFLEX_ROOKIE_PREMIUM = 1.10  # rookie QBs inflate SF pick markets
FUTURE_YEAR_DISCOUNT = 0.90  # per year beyond the next draft

_ROUND_SUFFIX = {1: "1st", 2: "2nd", 3: "3rd"}


@dataclass(frozen=True)
class OwnedPick:
    """A future draft pick and who currently holds it."""

    season: str
    round: int
    original_roster_id: int  # whose finish determines the slot
    owner_roster_id: int


def future_seasons(league_season: str | int, years: int = 3) -> list[str]:
    """Seasons whose rookie drafts are still ahead: next *years* seasons."""
    start = int(league_season) + 1
    return [str(start + i) for i in range(years)]


def resolve_pick_ownership(
    rosters: list[dict],
    traded_picks: list[dict],
    seasons: list[str],
    rounds: int = 4,
) -> list[OwnedPick]:
    """Compute current ownership of every future pick.

    Base state: each roster owns its own pick for every season/round.
    Sleeper traded-pick entries (``{season, round, roster_id, owner_id}``,
    where ``roster_id`` is the original owner) then reassign them.
    """
    ownership: dict[tuple[str, int, int], int] = {}
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None:
            continue
        for season in seasons:
            for rnd in range(1, rounds + 1):
                ownership[(season, rnd, rid)] = rid

    for tp in traded_picks:
        key = (str(tp.get("season")), tp.get("round"), tp.get("roster_id"))
        if key in ownership and tp.get("owner_id") is not None:
            ownership[key] = tp["owner_id"]

    return [
        OwnedPick(
            season=season,
            round=rnd,
            original_roster_id=orig,
            owner_roster_id=owner,
        )
        for (season, rnd, orig), owner in sorted(ownership.items())
    ]


def estimate_slot_tier(
    original_roster_id: int,
    roster_strength_rank: dict[int, int],
    num_teams: int,
) -> str:
    """Project a pick's slot tier from its original owner's strength.

    *roster_strength_rank* maps roster_id → rank with 1 = strongest team.
    The weakest third of the league drafts early, the strongest third late.
    """
    rank = roster_strength_rank.get(original_roster_id)
    if rank is None or num_teams <= 0:
        return "mid"
    if rank > num_teams * 2 / 3:
        return "early"
    if rank <= num_teams / 3:
        return "late"
    return "mid"


def pick_value(
    round_num: int,
    tier: str,
    fmt: LeagueFormat,
    year_offset: int = 0,
) -> float:
    """Consensus value of a pick on the 0–100 player scale."""
    value = DEFAULT_PICK_VALUES.get((round_num, tier), 1.0)
    if fmt.is_superflex:
        value *= SUPERFLEX_ROOKIE_PREMIUM
    value *= FUTURE_YEAR_DISCOUNT ** max(0, year_offset)
    return value


def pick_display_name(
    pick: OwnedPick, team_names: dict[int, str] | None = None
) -> str:
    """``"2027 1st Round Pick"``, plus ``(via Team)`` for acquired picks."""
    suffix = _ROUND_SUFFIX.get(pick.round, f"{pick.round}th")
    name = f"{pick.season} {suffix} Round Pick"
    if pick.owner_roster_id != pick.original_roster_id:
        origin = (team_names or {}).get(
            pick.original_roster_id, f"Team {pick.original_roster_id}"
        )
        name += f" (via {origin})"
    return name


def build_pick_assets(
    owned_picks: list[OwnedPick],
    roster_strength_rank: dict[int, int],
    fmt: LeagueFormat,
    current_season: str | int,
    team_names: dict[int, str] | None = None,
    max_round: int = 4,
) -> dict[int, list[TradeAsset]]:
    """Convert owned picks into ``{owner_roster_id: [TradeAsset, …]}``."""
    next_draft = int(current_season) + 1
    assets: dict[int, list[TradeAsset]] = {}
    for pick in owned_picks:
        if pick.round > max_round:
            continue
        tier = estimate_slot_tier(
            pick.original_roster_id, roster_strength_rank, fmt.num_teams
        )
        year_offset = int(pick.season) - next_draft
        value = pick_value(pick.round, tier, fmt, year_offset)
        assets.setdefault(pick.owner_roster_id, []).append(
            TradeAsset(
                name=pick_display_name(pick, team_names),
                position="PICK",
                value=value,
                is_pick=True,
                pick_year=int(pick.season),
                pick_round=pick.round,
            )
        )
    return assets
