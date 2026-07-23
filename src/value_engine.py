"""Weighted multi-source player valuation.

Blends per-source 0–100 values from :mod:`src.data_providers` into a single
:class:`PlayerValuation` per player using user-configurable
:class:`ValueWeights`:

* **source blend** — weighted mean across sources, renormalized per player
  so a player covered by only one source isn't penalized;
* **adjustments** — age curve, 30-day trend momentum, injury risk, and
  ADP-vs-value divergence, each scaled by its weight and recorded in
  ``components`` so the UI can explain *why* a player's adjusted value
  differs from base.

All weighting happens here, upstream of
:class:`~src.trade_calculator.TradeCalculator`; assets carry the adjusted
value, so the calculator's tested interface is untouched.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .data_providers import NormalizedPlayerValue
from .name_matching import match_name, normalize_name


DEFAULT_SOURCE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("fantasycalc", 0.5),
    ("ktc", 0.3),
    ("draftsharks", 0.2),
)


class ValueWeights(BaseModel):
    """User-configurable weighting of the value model.

    Frozen (hashable) so it can key ``st.cache_data``.  Adjustment weights
    are 0–1 dials: 0 disables the adjustment, 1 applies it fully.
    """

    model_config = ConfigDict(frozen=True)

    source_weights: tuple[tuple[str, float], ...] = DEFAULT_SOURCE_WEIGHTS
    age_weight: float = 0.5
    trend_weight: float = 0.0
    injury_weight: float = 0.0
    adp_divergence_weight: float = 0.0
    production_weight: float = 0.0

    def source_weights_dict(self) -> dict[str, float]:
        return dict(self.source_weights)

    def cache_key(self) -> str:
        sources = ",".join(f"{s}={w:g}" for s, w in self.source_weights)
        return (
            f"{sources}|age{self.age_weight:g}|tr{self.trend_weight:g}"
            f"|inj{self.injury_weight:g}|adp{self.adp_divergence_weight:g}"
            f"|prod{self.production_weight:g}"
        )


class PlayerValuation(BaseModel):
    """A player's blended, adjusted value with a full explanation trail."""

    key: str  # sleeper_id when known, else "name:<normalized>[:<pos>]"
    name: str
    position: str
    team: str = ""
    age: Optional[float] = None
    sleeper_id: Optional[str] = None
    base_value: float = 0.0  # weighted source blend, 0–100
    adjusted_value: float = 0.0  # after age/trend/injury/adp adjustments
    values_by_source: dict[str, float] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)  # fraction per adjustment
    fc_player_id: Optional[str] = None  # for FantasyCalc trade-history drill-down
    ktc_slug: Optional[str] = None
    trend_30_day: Optional[float] = None  # display value from primary source
    trend_frac: Optional[float] = None  # mean 30-day trend as fraction of value
    injury_risk: Optional[str] = None
    adp: Optional[float] = None
    # Internal signals accumulated during blending
    _trend_fracs: list[float] = PrivateAttr(default_factory=list)
    _adp_rank: Optional[float] = PrivateAttr(default=None)
    _value_rank: Optional[int] = PrivateAttr(default=None)


# ---------------------------------------------------------------------------
# Age curves
# ---------------------------------------------------------------------------

# Piecewise (age, multiplier) points per position; linear interpolation
# between points, clamped outside.  Reflects dynasty aging consensus:
# RBs cliff hardest and earliest, WRs decline from ~27, QBs and TEs last.
AGE_CURVES: dict[str, list[tuple[float, float]]] = {
    "QB": [(33.0, 1.00), (38.0, 0.75)],
    "RB": [(23.0, 1.05), (25.0, 1.00), (27.0, 0.85), (29.0, 0.65)],
    "WR": [(24.0, 1.05), (27.0, 1.00), (30.0, 0.85), (32.0, 0.70)],
    "TE": [(28.0, 1.00), (31.0, 0.85)],
}


def age_multiplier(position: str, age: Optional[float]) -> float:
    """Full-strength age multiplier for *position* at *age* (1.0 if unknown)."""
    curve = AGE_CURVES.get(position.upper())
    if not curve or age is None:
        return 1.0
    if age <= curve[0][0]:
        return curve[0][1]
    if age >= curve[-1][0]:
        return curve[-1][1]
    for (a1, m1), (a2, m2) in zip(curve, curve[1:]):
        if a1 <= age <= a2:
            frac = (age - a1) / (a2 - a1)
            return m1 + frac * (m2 - m1)
    return 1.0


def _injury_signal(risk: Optional[str]) -> float:
    """Value penalty fraction for a DraftSharks injury-risk label."""
    if not risk:
        return 0.0
    label = risk.lower()
    if "very high" in label:
        return -0.15
    if "high" in label:
        return -0.12
    if "medium" in label or "moderate" in label:
        return -0.05
    return 0.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


# Max ± swing of the recent-production adjustment at production_weight = 1.
PRODUCTION_MAX = 0.25


class ValueEngine:
    """Blend multi-source values into weighted, adjusted valuations."""

    def __init__(
        self,
        weights: Optional[ValueWeights] = None,
        production: Optional[dict[str, float]] = None,
    ) -> None:
        self.weights = weights or ValueWeights()
        # Recent-production signal in [-1, 1] keyed by ``sleeper_id`` and
        # ``name:<normalized>`` (see :func:`src.nfl_stats.production_signal_map`).
        self._production = production or {}

    # ------------------------------------------------------------------

    def blend(
        self, sources: dict[str, list[NormalizedPlayerValue]]
    ) -> dict[str, PlayerValuation]:
        """Merge per-source rows into ``{key: PlayerValuation}``.

        FantasyCalc rows are processed first (they carry ``sleeper_id``,
        the strongest identity); other sources attach by name match, with
        a position guard so same-named players at different positions
        don't merge.
        """
        valuations: dict[str, PlayerValuation] = {}
        by_norm_name: dict[str, str] = {}  # normalized name -> valuation key

        ordered = sorted(sources, key=lambda s: s != "fantasycalc")
        for source in ordered:
            for row in sources[source]:
                self._attach(row, valuations, by_norm_name)

        for valuation in valuations.values():
            self._finalize(valuation)
        return valuations

    # ------------------------------------------------------------------

    def name_value_map(
        self, valuations: dict[str, PlayerValuation]
    ) -> dict[str, float]:
        """``{display_name: adjusted_value}`` for the name-keyed
        :class:`~src.trade_analyzer.TradeAnalyzer` interface."""
        return {v.name: v.adjusted_value for v in valuations.values()}

    def sleeper_value_map(
        self, valuations: dict[str, PlayerValuation]
    ) -> dict[str, PlayerValuation]:
        """``{sleeper_id: PlayerValuation}`` for direct roster joins."""
        return {
            v.sleeper_id: v for v in valuations.values() if v.sleeper_id
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _attach(
        self,
        row: NormalizedPlayerValue,
        valuations: dict[str, PlayerValuation],
        by_norm_name: dict[str, str],
    ) -> None:
        norm = row.normalized_name
        valuation = self._resolve(row, norm, valuations, by_norm_name)

        valuation.values_by_source[row.source] = row.value
        if row.sleeper_id and not valuation.sleeper_id:
            valuation.sleeper_id = row.sleeper_id
        if row.age is not None and valuation.age is None:
            valuation.age = row.age
        if row.team and not valuation.team:
            valuation.team = row.team
        if row.source == "fantasycalc":
            valuation.fc_player_id = row.source_player_id
        if row.source == "ktc":
            valuation.ktc_slug = row.source_player_id
        if row.injury_risk and not valuation.injury_risk:
            valuation.injury_risk = row.injury_risk
        if row.adp is not None:
            valuation.adp = row.adp
            valuation._adp_rank = row.adp
            if row.overall_rank is not None:
                valuation._value_rank = row.overall_rank
        if row.trend_30_day is not None:
            if valuation.trend_30_day is None:
                valuation.trend_30_day = row.trend_30_day
            if row.raw_value > 0:
                valuation._trend_fracs.append(row.trend_30_day / row.raw_value)

    def _resolve(
        self,
        row: NormalizedPlayerValue,
        norm: str,
        valuations: dict[str, PlayerValuation],
        by_norm_name: dict[str, str],
    ) -> PlayerValuation:
        """Find the existing valuation this row belongs to, or create one."""
        matched = match_name(row.name, by_norm_name.keys())
        if matched:
            candidate = valuations[by_norm_name[matched]]
            # Position guard: same name, different position = different player
            if (
                not candidate.position
                or not row.position
                or candidate.position == row.position
            ):
                return candidate

        key = row.sleeper_id or f"name:{norm}"
        if (
            not row.sleeper_id
            and key in valuations
            and valuations[key].position not in ("", row.position)
        ):
            key = f"name:{norm}:{row.position}"
        if key not in valuations:
            valuations[key] = PlayerValuation(
                key=key,
                name=row.name,
                position=row.position,
                team=row.team,
                age=row.age,
                sleeper_id=row.sleeper_id,
            )
            by_norm_name.setdefault(norm, key)
        return valuations[key]

    def _finalize(self, valuation: PlayerValuation) -> None:
        """Compute base (weighted blend) and adjusted values in place."""
        weights = self.weights
        source_weights = weights.source_weights_dict()

        total_weight = 0.0
        weighted_sum = 0.0
        for source, value in valuation.values_by_source.items():
            w = source_weights.get(source, 0.0)
            total_weight += w
            weighted_sum += w * value
        if total_weight > 0:
            base = weighted_sum / total_weight
        else:  # only unweighted sources cover this player — plain mean
            vals = list(valuation.values_by_source.values())
            base = sum(vals) / len(vals) if vals else 0.0
        valuation.base_value = base

        # Age: interpolate the positional curve, scaled by the age dial.
        age_mult = age_multiplier(valuation.position, valuation.age)
        age_factor = 1.0 + weights.age_weight * (age_mult - 1.0)

        # Trend: mean per-source trend fraction, clamped to ±30%.
        trend_sig = 0.0
        if valuation._trend_fracs:
            mean_frac = sum(valuation._trend_fracs) / len(valuation._trend_fracs)
            valuation.trend_frac = mean_frac
            trend_sig = _clamp(mean_frac, -0.3, 0.3)

        # Injury: penalty from the DraftSharks risk label.
        injury_sig = _injury_signal(valuation.injury_risk)

        # ADP divergence: positive when the market drafts the player later
        # (higher ADP) than his value rank — i.e. potentially cheap to buy.
        adp_sig = 0.0
        if valuation._adp_rank is not None and valuation._value_rank is not None:
            adp_sig = _clamp(
                (valuation._adp_rank - valuation._value_rank) / 100.0, -0.2, 0.2
            )

        # Recent production: a ±1 signal from validated fantasy PPG percentile
        # within the player's position, scaled to a bounded value swing.
        prod_sig = self._production_signal(valuation)

        components = {
            "age": age_factor - 1.0,
            "trend": weights.trend_weight * trend_sig,
            "injury": weights.injury_weight * injury_sig,
            "adp": weights.adp_divergence_weight * adp_sig,
            "production": weights.production_weight * PRODUCTION_MAX * prod_sig,
        }
        valuation.components = components
        valuation.adjusted_value = base * age_factor * (
            1.0
            + components["trend"]
            + components["injury"]
            + components["adp"]
            + components["production"]
        )

    def _production_signal(self, valuation: PlayerValuation) -> float:
        """Recent-production signal in [-1, 1] for *valuation* (0 if unknown)."""
        if not self._production:
            return 0.0
        if valuation.sleeper_id and valuation.sleeper_id in self._production:
            return self._production[valuation.sleeper_id]
        return self._production.get(f"name:{normalize_name(valuation.name)}", 0.0)
