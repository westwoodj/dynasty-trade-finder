"""Tests for the weighted value engine."""

from __future__ import annotations

import pytest

from src.data_providers import NormalizedPlayerValue
from src.value_engine import (
    AGE_CURVES,
    PlayerValuation,
    ValueEngine,
    ValueWeights,
    age_multiplier,
)


def npv(
    name: str,
    source: str,
    value: float,
    position: str = "WR",
    raw_value: float | None = None,
    **kwargs,
) -> NormalizedPlayerValue:
    return NormalizedPlayerValue(
        name=name,
        position=position,
        source=source,
        value=value,
        raw_value=raw_value if raw_value is not None else value * 100,
        **kwargs,
    )


NO_ADJUST = ValueWeights(age_weight=0.0)


# ---------------------------------------------------------------------------
# Age curve
# ---------------------------------------------------------------------------


class TestAgeMultiplier:
    def test_unknown_age_is_neutral(self) -> None:
        assert age_multiplier("RB", None) == 1.0

    def test_unknown_position_is_neutral(self) -> None:
        assert age_multiplier("PICK", 30.0) == 1.0

    def test_young_rb_boosted(self) -> None:
        assert age_multiplier("RB", 22.0) == pytest.approx(1.05)

    def test_rb_curve_interpolates(self) -> None:
        assert age_multiplier("RB", 26.0) == pytest.approx((1.00 + 0.85) / 2)

    def test_old_rb_clamped_to_last_point(self) -> None:
        assert age_multiplier("RB", 33.0) == pytest.approx(0.65)

    def test_rb_monotonic_decline_25_to_30(self) -> None:
        mults = [age_multiplier("RB", a) for a in range(25, 31)]
        assert all(m1 >= m2 for m1, m2 in zip(mults, mults[1:]))
        assert mults[0] > mults[-1]

    def test_qb_flat_until_33(self) -> None:
        assert age_multiplier("QB", 27.0) == 1.0
        assert age_multiplier("QB", 33.0) == 1.0
        assert age_multiplier("QB", 38.0) == pytest.approx(0.75)

    def test_curves_cover_all_skill_positions(self) -> None:
        assert set(AGE_CURVES) == {"QB", "RB", "WR", "TE"}


# ---------------------------------------------------------------------------
# Source blending
# ---------------------------------------------------------------------------


class TestBlend:
    def test_weighted_mean_all_sources(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [npv("Justin Jefferson", "fantasycalc", 100.0)],
                "ktc": [npv("Justin Jefferson", "ktc", 80.0)],
                "draftsharks": [npv("Justin Jefferson", "draftsharks", 60.0)],
            }
        )
        assert len(valuations) == 1
        v = next(iter(valuations.values()))
        # 0.5*100 + 0.3*80 + 0.2*60 = 86
        assert v.base_value == pytest.approx(86.0)
        assert v.values_by_source == {
            "fantasycalc": 100.0,
            "ktc": 80.0,
            "draftsharks": 60.0,
        }

    def test_weights_renormalized_when_source_missing(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [npv("Justin Jefferson", "fantasycalc", 100.0)],
                "ktc": [npv("Justin Jefferson", "ktc", 80.0)],
            }
        )
        v = next(iter(valuations.values()))
        # (0.5*100 + 0.3*80) / 0.8 = 92.5 — NOT dragged down by absence
        assert v.base_value == pytest.approx(92.5)

    def test_single_source_player_keeps_full_value(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {"draftsharks": [npv("Deep Sleeper", "draftsharks", 10.0)]}
        )
        v = next(iter(valuations.values()))
        assert v.base_value == pytest.approx(10.0)

    def test_custom_source_weights(self) -> None:
        weights = ValueWeights(
            source_weights=(("fantasycalc", 1.0), ("ktc", 0.0)), age_weight=0.0
        )
        engine = ValueEngine(weights)
        valuations = engine.blend(
            {
                "fantasycalc": [npv("A Player", "fantasycalc", 100.0)],
                "ktc": [npv("A Player", "ktc", 20.0)],
            }
        )
        v = next(iter(valuations.values()))
        assert v.base_value == pytest.approx(100.0)

    def test_sleeper_id_key_preferred(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Justin Jefferson", "fantasycalc", 100.0, sleeper_id="6786")
                ]
            }
        )
        assert "6786" in valuations
        assert valuations["6786"].sleeper_id == "6786"

    def test_alias_names_merge_across_sources(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Marquise Brown", "fantasycalc", 40.0, sleeper_id="5848")
                ],
                "ktc": [npv("Hollywood Brown", "ktc", 50.0)],
            }
        )
        assert len(valuations) == 1
        v = valuations["5848"]
        assert set(v.values_by_source) == {"fantasycalc", "ktc"}

    def test_same_name_different_position_not_merged(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Josh Allen", "fantasycalc", 95.0, position="QB")
                ],
                "ktc": [npv("Josh Allen", "ktc", 5.0, position="LB")],
            }
        )
        assert len(valuations) == 2

    def test_fantasycalc_processed_first_regardless_of_dict_order(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "ktc": [npv("Justin Jefferson", "ktc", 80.0)],
                "fantasycalc": [
                    npv("Justin Jefferson", "fantasycalc", 100.0, sleeper_id="6786")
                ],
            }
        )
        # FC first means the merged valuation is keyed by sleeper_id
        assert list(valuations) == ["6786"]


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


class TestAdjustments:
    def test_zero_weights_mean_adjusted_equals_base(self) -> None:
        engine = ValueEngine(ValueWeights(age_weight=0.0))
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Old RB", "fantasycalc", 50.0, position="RB", age=30.0,
                        trend_30_day=-500.0, injury_risk="High")
                ]
            }
        )
        v = next(iter(valuations.values()))
        assert v.adjusted_value == pytest.approx(v.base_value)

    def test_age_weight_scales_the_curve(self) -> None:
        full = ValueEngine(ValueWeights(age_weight=1.0))
        half = ValueEngine(ValueWeights(age_weight=0.5))
        rows = {
            "fantasycalc": [npv("Old RB", "fantasycalc", 50.0, position="RB", age=33.0)]
        }
        v_full = next(iter(full.blend(rows).values()))
        v_half = next(iter(half.blend(rows).values()))
        assert v_full.adjusted_value == pytest.approx(50.0 * 0.65)
        assert v_half.adjusted_value == pytest.approx(50.0 * (1 - 0.35 / 2))

    def test_trend_momentum(self) -> None:
        engine = ValueEngine(ValueWeights(age_weight=0.0, trend_weight=1.0))
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Riser", "fantasycalc", 50.0, raw_value=5000.0,
                        trend_30_day=500.0)
                ]
            }
        )
        v = next(iter(valuations.values()))
        # trend fraction = 500/5000 = +10%
        assert v.adjusted_value == pytest.approx(50.0 * 1.10)

    def test_trend_clamped(self) -> None:
        engine = ValueEngine(ValueWeights(age_weight=0.0, trend_weight=1.0))
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Moonshot", "fantasycalc", 50.0, raw_value=1000.0,
                        trend_30_day=900.0)  # +90% raw, clamps to +30%
                ]
            }
        )
        v = next(iter(valuations.values()))
        assert v.adjusted_value == pytest.approx(50.0 * 1.30)

    def test_injury_penalty(self) -> None:
        engine = ValueEngine(ValueWeights(age_weight=0.0, injury_weight=1.0))
        valuations = engine.blend(
            {
                "draftsharks": [
                    npv("Glass Cannon", "draftsharks", 50.0, injury_risk="High")
                ]
            }
        )
        v = next(iter(valuations.values()))
        assert v.adjusted_value == pytest.approx(50.0 * (1 - 0.12))

    def test_adp_divergence_boosts_underdrafted(self) -> None:
        engine = ValueEngine(ValueWeights(age_weight=0.0, adp_divergence_weight=1.0))
        valuations = engine.blend(
            {
                "draftsharks": [
                    npv("Market Discount", "draftsharks", 50.0,
                        adp=40.0, overall_rank=20)
                ]
            }
        )
        v = next(iter(valuations.values()))
        # (adp 40 - value rank 20) / 100 = +20%
        assert v.adjusted_value == pytest.approx(50.0 * 1.20)

    def test_components_explain_adjusted_over_base(self) -> None:
        engine = ValueEngine(
            ValueWeights(age_weight=0.7, trend_weight=0.5, injury_weight=0.5)
        )
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Complex Case", "fantasycalc", 60.0, position="RB",
                        age=28.0, raw_value=6000.0, trend_30_day=-600.0)
                ],
                "draftsharks": [
                    npv("Complex Case", "draftsharks", 50.0, position="RB",
                        injury_risk="Medium")
                ],
            }
        )
        v = next(iter(valuations.values()))
        c = v.components
        expected = v.base_value * (1 + c["age"]) * (
            1 + c["trend"] + c["injury"] + c["adp"]
        )
        assert v.adjusted_value == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Output maps
# ---------------------------------------------------------------------------


class TestOutputMaps:
    def test_name_value_map(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {"fantasycalc": [npv("Justin Jefferson", "fantasycalc", 100.0)]}
        )
        assert engine.name_value_map(valuations) == {
            "Justin Jefferson": pytest.approx(100.0)
        }

    def test_sleeper_value_map_skips_unjoined(self) -> None:
        engine = ValueEngine(NO_ADJUST)
        valuations = engine.blend(
            {
                "fantasycalc": [
                    npv("Joined", "fantasycalc", 90.0, sleeper_id="111"),
                ],
                "ktc": [npv("Name Only", "ktc", 40.0)],
            }
        )
        smap = engine.sleeper_value_map(valuations)
        assert set(smap) == {"111"}

    def test_cache_key_stable_and_distinct(self) -> None:
        w1 = ValueWeights()
        w2 = ValueWeights(age_weight=0.9)
        assert w1.cache_key() == ValueWeights().cache_key()
        assert w1.cache_key() != w2.cache_key()
        assert hash(w1)  # frozen → hashable
