"""Smart dynasty analytics: buy-low/sell-high flags, team timeline
profiling, timeline-fit trade bonuses, and value-over-replacement.

Everything here is pure logic over :class:`~src.value_engine.PlayerValuation`
and :class:`~src.trade_calculator.TradeAsset` — no network, fully testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .league_settings import LeagueFormat
from .trade_calculator import TradeAsset
from .value_engine import PlayerValuation, age_multiplier

WIN_NOW_AGE = 27.0  # assets this old are win-now capital
FUTURE_AGE = 24.0  # assets this young (and picks) are future capital


# ---------------------------------------------------------------------------
# Buy low / sell high
# ---------------------------------------------------------------------------


@dataclass
class Insight:
    """A flagged market opportunity on a single player."""

    kind: str  # "buy_low" | "sell_high"
    player: PlayerValuation
    score: float  # sort key; higher = stronger signal
    reason: str


def _source_spread(valuation: PlayerValuation) -> float:
    """Cross-source disagreement: (max - min) / mean, 0 if single-source."""
    values = list(valuation.values_by_source.values())
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    return (max(values) - min(values)) / mean


def find_buy_low_sell_high(
    valuations: dict[str, PlayerValuation],
    deltas: Optional[dict[str, float]] = None,
    min_value: float = 15.0,
    trend_threshold: float = 0.05,
    top_n: int = 15,
) -> list[Insight]:
    """Flag players whose market price is moving against their profile.

    * **Buy low** — value falling (30-day trend or local snapshot delta)
      while the age curve says the player isn't in decline: the market may
      be overreacting.
    * **Sell high** — value rising on a player past his positional age
      peak: the market is paying tomorrow's price for yesterday's profile.

    Cross-source disagreement (spread) strengthens either signal.
    """
    deltas = deltas or {}
    insights: list[Insight] = []
    for v in valuations.values():
        if v.base_value < min_value:
            continue
        trend = v.trend_frac
        local_delta = deltas.get(v.key, 0.0)
        spread = _source_spread(v)
        age_mult = age_multiplier(v.position, v.age)

        falling = (trend is not None and trend <= -trend_threshold) or local_delta <= -3.0
        rising = (trend is not None and trend >= trend_threshold) or local_delta >= 3.0

        if falling and age_mult >= 0.85:
            drop_pct = abs(trend or local_delta / max(v.base_value, 1.0)) * 100
            reason = (
                f"Value down ~{drop_pct:.0f}% over 30 days while the "
                f"{v.position} age curve still favors him"
            )
            if spread >= 0.2:
                reason += f"; sources disagree by {spread * 100:.0f}%"
            insights.append(
                Insight("buy_low", v, abs(trend or 0.0) + spread + abs(min(local_delta, 0.0)) / 100, reason)
            )
        elif rising and age_mult < 1.0:
            rise_pct = abs(trend or local_delta / max(v.base_value, 1.0)) * 100
            reason = (
                f"Value up ~{rise_pct:.0f}% over 30 days on a "
                f"{v.position} already past his age peak — sell into strength"
            )
            insights.append(
                Insight("sell_high", v, abs(trend or 0.0) + spread + max(local_delta, 0.0) / 100, reason)
            )
    return sorted(insights, key=lambda i: i.score, reverse=True)[:top_n]


# ---------------------------------------------------------------------------
# Team timeline profiling
# ---------------------------------------------------------------------------


@dataclass
class TeamProfile:
    """A roster's competitive-timeline breakdown."""

    roster_id: int
    team_name: str
    total_value: float
    win_now_value: float  # players aged >= 27
    core_value: float  # everyone in between (incl. unknown ages)
    future_value: float  # players <= 24 plus draft picks
    pick_value: float
    wins: int = 0
    losses: int = 0
    classification: str = "balanced"  # "contender" | "rebuilder" | "balanced"

    @property
    def win_pct(self) -> float:
        games = self.wins + self.losses
        return self.wins / games if games else 0.5


def profile_team(
    roster_id: int,
    team_name: str,
    assets: list[TradeAsset],
    picks: Optional[list[TradeAsset]] = None,
    wins: int = 0,
    losses: int = 0,
) -> TeamProfile:
    """Split a roster's value into win-now / core / future capital and
    classify the team's competitive timeline."""
    picks = picks or []
    win_now = sum(a.value for a in assets if a.age is not None and a.age >= WIN_NOW_AGE)
    future_players = sum(
        a.value for a in assets if a.age is not None and a.age <= FUTURE_AGE
    )
    pick_total = sum(p.value for p in picks)
    total = sum(a.value for a in assets) + pick_total
    future = future_players + pick_total
    core = total - win_now - future

    classification = "balanced"
    if total > 0:
        wn_ratio = win_now / total
        fut_ratio = future / total
        games = wins + losses
        win_pct = wins / games if games else 0.5
        if fut_ratio - wn_ratio > 0.15:
            classification = "rebuilder"
        elif wn_ratio - fut_ratio > 0.15:
            classification = "contender"
        elif games and win_pct >= 0.6:
            classification = "contender"
        elif games and win_pct <= 0.4:
            classification = "rebuilder"

    return TeamProfile(
        roster_id=roster_id,
        team_name=team_name,
        total_value=total,
        win_now_value=win_now,
        core_value=core,
        future_value=future,
        pick_value=pick_total,
        wins=wins,
        losses=losses,
        classification=classification,
    )


def _orientation(profile: TeamProfile) -> float:
    """Win-now vs future tilt as a share of roster value (−1..+1)."""
    if profile.total_value <= 0:
        return 0.0
    return (profile.win_now_value - profile.future_value) / profile.total_value


def _pct_rank(values: list[float], value: float) -> float:
    """Fraction of *values* at or below *value* (0..1)."""
    if not values:
        return 0.5
    return sum(1 for v in values if v <= value) / len(values)


def classify_teams(profiles: dict[int, TeamProfile]) -> dict[int, TeamProfile]:
    """Assign contender/rebuilder/balanced labels *relative to the league*.

    Ranks teams by a blend of roster orientation (win-now vs future value
    share) and record, then splits into thirds.  A whole-league pass is
    what makes the label meaningful: in a young dynasty league every roster
    looks future-heavy against fixed thresholds, so absolute classification
    (see :func:`profile_team`) collapses to "rebuilder" for everyone.
    Contenders and rebuilders are defined by where a team sits among its
    twelve rivals, which is exactly the question a trader is asking.

    Mutates each profile's ``classification`` in place and returns *profiles*.
    """
    teams = list(profiles.values())
    n = len(teams)
    if n == 0:
        return profiles
    if n < 3:
        for team in teams:
            team.classification = "balanced"
        return profiles

    orientations = [_orientation(t) for t in teams]
    win_pcts = [t.win_pct for t in teams]
    has_record = any(t.wins + t.losses for t in teams)

    scored: list[tuple[float, TeamProfile]] = []
    for team in teams:
        orient_pct = _pct_rank(orientations, _orientation(team))
        if has_record:
            record_pct = _pct_rank(win_pcts, team.win_pct)
            score = 0.6 * orient_pct + 0.4 * record_pct
        else:  # offseason — roster construction is all we have
            score = orient_pct
        scored.append((score, team))

    scored.sort(key=lambda s: s[0], reverse=True)
    third = n / 3.0
    for i, (_, team) in enumerate(scored):
        if i < third:
            team.classification = "contender"
        elif i >= n - third:
            team.classification = "rebuilder"
        else:
            team.classification = "balanced"
    return profiles


# ---------------------------------------------------------------------------
# Timeline-fit trade bonus
# ---------------------------------------------------------------------------


def _is_win_now_asset(asset: TradeAsset) -> bool:
    return not asset.is_pick and asset.age is not None and asset.age >= WIN_NOW_AGE


def _is_future_asset(asset: TradeAsset) -> bool:
    return asset.is_pick or (asset.age is not None and asset.age <= FUTURE_AGE)


def timeline_fit_bonus(
    my_classification: str,
    their_classification: str,
    giving: tuple[TradeAsset, ...],
    receiving: tuple[TradeAsset, ...],
) -> float:
    """Score bonus for trades that fit both teams' timelines.

    A contender buying win-now assets from a rebuilder (and paying with
    youth/picks) is the trade both managers actually want to make.
    """
    if my_classification == their_classification:
        return 0.0
    bonus = 0.0
    for asset in receiving:
        if my_classification == "contender" and _is_win_now_asset(asset):
            bonus += 0.03
        if my_classification == "rebuilder" and _is_future_asset(asset):
            bonus += 0.03
    for asset in giving:
        if my_classification == "contender" and _is_future_asset(asset):
            bonus += 0.02
        if my_classification == "rebuilder" and _is_win_now_asset(asset):
            bonus += 0.02
    return min(bonus, 0.12)


# ---------------------------------------------------------------------------
# Value over replacement
# ---------------------------------------------------------------------------


def replacement_baselines(
    valuations: dict[str, PlayerValuation], fmt: LeagueFormat
) -> dict[str, float]:
    """Replacement-level value per position: the first player ranked past
    every starting slot in the league (true team count, not the snapped
    query count)."""
    by_pos: dict[str, list[float]] = {}
    for v in valuations.values():
        by_pos.setdefault(v.position, []).append(v.adjusted_value)

    baselines: dict[str, float] = {}
    for pos, n_slots in fmt.slots_dict().items():
        values = sorted(by_pos.get(pos, []), reverse=True)
        idx = fmt.num_teams * n_slots
        baselines[pos] = values[idx] if idx < len(values) else 0.0
    return baselines


def value_based_need(
    assets: list[TradeAsset],
    baselines: dict[str, float],
    starter_slots: dict[str, int],
) -> dict[str, float]:
    """Positional need in *replacement players short* units.

    For each position, compares the roster's top starters against the
    league replacement baseline: 0 = every starter clears replacement
    level, 1.0 = one full starter slot below replacement.  Unlike the
    headcount heuristic, three bad RBs still register as RB need.
    """
    need: dict[str, float] = {}
    for pos, n_slots in starter_slots.items():
        baseline = baselines.get(pos, 0.0)
        if baseline <= 0 or n_slots <= 0:
            need[pos] = 0.0
            continue
        top = sorted(
            (a.value for a in assets if a.position == pos and not a.is_pick),
            reverse=True,
        )[:n_slots]
        top += [0.0] * (n_slots - len(top))
        shortage = sum(max(0.0, baseline - v) for v in top)
        need[pos] = round(shortage / baseline, 2)
    return need
