"""Trade value calculation for dynasty superflex PPR fantasy football.

Key concepts
------------
* **Superflex** — a flex spot that can hold a QB, giving QBs a large value
  premium over standard leagues (two QBs can be started simultaneously).
* **PPR** — one point per reception; rewards high-volume pass-catchers
  (WRs, TEs, pass-catching RBs).
* **Dynasty** — players are kept year-over-year, so youth and long-term
  upside are priced into player values.
* **Trade grade** — a letter grade derived from the percentage value swing
  the receiving side gains relative to the giving side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TradeAsset:
    """A tradeable asset: either a player or a future draft pick."""

    name: str
    position: str
    team: str = ""
    age: Optional[float] = None
    value: float = 0.0
    # Draft-pick specific fields
    is_pick: bool = False
    pick_year: Optional[int] = None
    pick_round: Optional[int] = None

    @property
    def display_name(self) -> str:
        if self.is_pick:
            suffix = {1: "1st", 2: "2nd", 3: "3rd"}.get(
                self.pick_round or 0, f"{self.pick_round}th"
            )
            return f"{self.pick_year} {suffix} Round Pick"
        return self.name


@dataclass
class TradeResult:
    """Result of evaluating a proposed trade."""

    giving: list[TradeAsset]
    receiving: list[TradeAsset]
    giving_value: float
    receiving_value: float
    value_delta: float  # receiving_value - giving_value
    grade: str
    summary: str

    @property
    def is_favorable(self) -> bool:
        """True when the receiving side is worth more than the giving side."""
        return self.value_delta > 0

    @property
    def value_delta_pct(self) -> float:
        """Value delta as a fraction of the giving-side total."""
        if self.giving_value <= 0:
            return 0.0
        return self.value_delta / self.giving_value


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class TradeCalculator:
    """Evaluate and grade fantasy football trades."""

    # Superflex PPR positional multipliers applied on top of raw values.
    # QBs receive the largest boost because two can be started in superflex.
    # TEs receive a modest boost due to positional scarcity.
    SUPERFLEX_MULTIPLIERS: dict[str, float] = {
        "QB": 1.30,
        "RB": 1.00,
        "WR": 1.00,
        "TE": 1.10,
    }

    # Grade thresholds: (min_pct_delta, grade, description)
    # pct_delta = value_delta / giving_value
    GRADE_THRESHOLDS: list[tuple[float, str, str]] = [
        (0.20, "A+", "Excellent — major value gain"),
        (0.10, "A", "Great — clear value advantage"),
        (0.05, "B+", "Good — slight value advantage"),
        (-0.05, "B", "Fair — roughly even trade"),
        (-0.10, "C+", "Below average — slight value loss"),
        (-0.20, "C", "Poor — significant value loss"),
        (float("-inf"), "D", "Very unfavourable — major value loss"),
    ]

    def calculate_trade_value(
        self,
        giving: list[TradeAsset],
        receiving: list[TradeAsset],
        positional_need: Optional[dict[str, int]] = None,
    ) -> TradeResult:
        """
        Evaluate a proposed trade.

        Args:
            giving: Assets you are sending away.
            receiving: Assets you are getting back.
            positional_need: Optional dict ``{position: need_score}`` where a
                higher score means your roster needs more of that position.
                Each need point adds a 5 % bonus to the asset's value.

        Returns:
            :class:`TradeResult` with grade, value totals, and delta.
        """
        giving_value = self._total_value(giving, positional_need=None)
        receiving_value = self._total_value(receiving, positional_need)
        delta = receiving_value - giving_value
        grade, summary = self._grade(delta, giving_value)
        return TradeResult(
            giving=giving,
            receiving=receiving,
            giving_value=giving_value,
            receiving_value=receiving_value,
            value_delta=delta,
            grade=grade,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Positional need
    # ------------------------------------------------------------------

    def calculate_positional_need(
        self,
        roster: list[TradeAsset],
        starter_slots: dict[str, int],
    ) -> dict[str, int]:
        """
        Return a need score per position based on how far below target depth
        a roster is.

        *Target depth* is twice the number of starter slots for each position.
        A score of 0 means no need; higher means more need.

        Args:
            roster: Current roster assets (players only, not picks).
            starter_slots: ``{position: num_slots}``, e.g.
                ``{"QB": 2, "RB": 2, "WR": 3, "TE": 1}`` for superflex.

        Returns:
            ``{position: need_score}``
        """
        counts: dict[str, int] = {}
        for asset in roster:
            pos = asset.position.upper()
            counts[pos] = counts.get(pos, 0) + 1

        need: dict[str, int] = {}
        for pos, slots in starter_slots.items():
            target = slots * 2
            shortage = max(0, target - counts.get(pos, 0))
            need[pos] = shortage
        return need

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _total_value(
        self,
        assets: list[TradeAsset],
        positional_need: Optional[dict[str, int]],
    ) -> float:
        total = 0.0
        for asset in assets:
            val = asset.value
            mult = self.SUPERFLEX_MULTIPLIERS.get(asset.position.upper(), 1.0)
            val *= mult
            if positional_need:
                need = positional_need.get(asset.position.upper(), 0)
                val *= 1.0 + need * 0.05
            total += val
        return total

    def _grade(
        self, delta: float, giving_value: float
    ) -> tuple[str, str]:
        if giving_value <= 0:
            return "N/A", "No assets on giving side"
        pct = delta / giving_value
        for threshold, grade, summary in self.GRADE_THRESHOLDS:
            if pct >= threshold:
                return grade, summary
        return "D", "Very unfavourable — major value loss"
