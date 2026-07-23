"""Trade opportunity analysis for dynasty superflex PPR fantasy football.

This module identifies:

* **Arbitrage opportunities** — players whose value differs significantly
  across sources (e.g. a player KTC loves but FantasyCalc is lukewarm on).
  Knowing these gaps lets you buy at the "low" source's price and sell at
  the "high" source's price.

* **Best trade proposals** — trades where you acquire more consensus value
  than you give up, optionally weighted by your positional needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

from .trade_calculator import TradeAsset, TradeCalculator, TradeResult


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TradeProposal:
    """A fully evaluated potential trade between two managers."""

    my_team: str
    their_team: str
    giving: list[TradeAsset]
    receiving: list[TradeAsset]
    result: TradeResult
    score: float  # Higher is better for «my_team»


@dataclass
class ArbitrageOpportunity:
    """A player with a notable value spread across trusted sources."""

    player_name: str
    position: str
    team: str
    values_by_source: dict[str, float]  # {source: value}
    consensus_value: float
    max_value: float
    min_value: float
    spread_pct: float  # (max - min) / consensus
    recommendation: str  # "buy" or "sell"

    @property
    def high_source(self) -> str:
        """Source that gives the highest value."""
        if not self.values_by_source:
            return ""
        return max(self.values_by_source, key=lambda s: self.values_by_source[s])

    @property
    def low_source(self) -> str:
        """Source that gives the lowest value."""
        if not self.values_by_source:
            return ""
        return min(self.values_by_source, key=lambda s: self.values_by_source[s])


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class TradeAnalyzer:
    """Identifies and ranks trade opportunities for a dynasty fantasy team."""

    def __init__(self, calculator: Optional[TradeCalculator] = None) -> None:
        self.calculator = calculator or TradeCalculator()

    # ------------------------------------------------------------------
    # Arbitrage
    # ------------------------------------------------------------------

    def find_arbitrage(
        self,
        values_by_source: dict[str, dict[str, float]],
        spread_threshold: float = 0.20,
    ) -> list[ArbitrageOpportunity]:
        """
        Identify players with a significant value spread across sources.

        Args:
            values_by_source: ``{source_name: {player_name: value}}``.
                Values should already be on a comparable scale (e.g. 0–100
                after normalisation).
            spread_threshold: Minimum ``(max - min) / consensus`` required
                to flag a player as an arbitrage opportunity.  Default 0.20
                means the spread must be at least 20 % of consensus.

        Returns:
            List of :class:`ArbitrageOpportunity`, sorted by spread descending.
        """
        all_players: set[str] = set()
        for source_vals in values_by_source.values():
            all_players.update(source_vals)

        opportunities: list[ArbitrageOpportunity] = []
        for player in all_players:
            source_vals = {
                src: vals[player]
                for src, vals in values_by_source.items()
                if player in vals
            }
            if len(source_vals) < 2:
                continue

            max_val = max(source_vals.values())
            min_val = min(source_vals.values())
            consensus = sum(source_vals.values()) / len(source_vals)
            if consensus <= 0:
                continue

            spread = (max_val - min_val) / consensus
            if spread < spread_threshold:
                continue

            # "buy" = undervalued on one source, so you can acquire cheaply
            # "sell" = overvalued on one source, so you can move him high
            recommendation = "buy" if min_val < consensus * 0.85 else "sell"

            opportunities.append(
                ArbitrageOpportunity(
                    player_name=player,
                    position="",
                    team="",
                    values_by_source=source_vals,
                    consensus_value=consensus,
                    max_value=max_val,
                    min_value=min_val,
                    spread_pct=spread,
                    recommendation=recommendation,
                )
            )

        return sorted(opportunities, key=lambda o: o.spread_pct, reverse=True)

    # ------------------------------------------------------------------
    # Best-trade finder
    # ------------------------------------------------------------------

    def find_best_trades(
        self,
        my_roster: list[TradeAsset],
        all_rosters: dict[str, list[TradeAsset]],
        player_values: dict[str, float],
        positional_need: Optional[dict[str, int]] = None,
        max_assets_per_side: int = 2,
        top_n: int = 20,
    ) -> list[TradeProposal]:
        """
        Enumerate plausible trades and return the most valuable ones.

        Evaluates all combinations of up to *max_assets_per_side* players on
        each side.  Only proposals that are net-positive in value (for «My
        Team») are kept.

        Args:
            my_roster: Your current roster.
            all_rosters: ``{team_name: roster}`` for every other team.
            player_values: ``{player_name: consensus_value}`` lookup.
            positional_need: Optional positional need scores from
                :meth:`TradeCalculator.calculate_positional_need`.
            max_assets_per_side: Maximum number of assets per side (1 or 2).
            top_n: How many proposals to return.

        Returns:
            Top *top_n* :class:`TradeProposal` objects sorted by score.
        """
        my_assets = _enrich(my_roster, player_values)
        proposals: list[TradeProposal] = []

        for team_name, their_roster in all_rosters.items():
            their_assets = _enrich(their_roster, player_values)
            for n in range(1, max_assets_per_side + 1):
                for giving in combinations(my_assets, n):
                    for receiving in combinations(their_assets, n):
                        result = self.calculator.calculate_trade_value(
                            list(giving), list(receiving), positional_need
                        )
                        if not result.is_favorable:
                            continue
                        score = self._score(result, receiving, positional_need)
                        proposals.append(
                            TradeProposal(
                                my_team="My Team",
                                their_team=team_name,
                                giving=list(giving),
                                receiving=list(receiving),
                                result=result,
                                score=score,
                            )
                        )

        return sorted(proposals, key=lambda p: p.score, reverse=True)[:top_n]

    # ------------------------------------------------------------------
    # Trade target ranking
    # ------------------------------------------------------------------

    def rank_trade_targets(
        self,
        my_roster: list[TradeAsset],
        all_rosters: dict[str, list[TradeAsset]],
        player_values: dict[str, float],
        positional_need: Optional[dict[str, int]] = None,
    ) -> list[tuple[TradeAsset, str, float]]:
        """
        Rank individual players on other rosters as acquisition targets.

        Returns:
            List of ``(asset, team_name, desirability_score)`` sorted by
            score descending.
        """
        my_names = {a.name for a in my_roster}
        targets: list[tuple[TradeAsset, str, float]] = []

        for team_name, roster in all_rosters.items():
            for asset in roster:
                if asset.name in my_names:
                    continue
                val = player_values.get(asset.name, 0.0)
                need_bonus = 0.0
                if positional_need:
                    need = positional_need.get(asset.position.upper(), 0)
                    need_bonus = need * 0.5 * val  # up to 50% bonus at max need
                targets.append((asset, team_name, val + need_bonus))

        return sorted(targets, key=lambda t: t[2], reverse=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score(
        self,
        result: TradeResult,
        receiving: tuple[TradeAsset, ...],
        positional_need: Optional[dict[str, int]],
    ) -> float:
        """Compute a desirability score for a proposal (higher = better)."""
        base = result.value_delta_pct
        bonus = 0.0
        if positional_need:
            for asset in receiving:
                need = positional_need.get(asset.position.upper(), 0)
                bonus += need * 0.02
        return base + bonus


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _enrich(
    assets: list[TradeAsset], player_values: dict[str, float]
) -> list[TradeAsset]:
    """Return copies of *assets* with values filled from *player_values*."""
    result = []
    for a in assets:
        result.append(
            TradeAsset(
                name=a.name,
                position=a.position,
                team=a.team,
                age=a.age,
                is_pick=a.is_pick,
                pick_year=a.pick_year,
                pick_round=a.pick_round,
                value=player_values.get(a.name, a.value),
            )
        )
    return result
