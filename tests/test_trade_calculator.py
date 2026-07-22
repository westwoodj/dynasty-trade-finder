"""Tests for the trade calculator."""

from __future__ import annotations

import pytest

from src.trade_calculator import TradeAsset, TradeCalculator, TradeResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def calculator() -> TradeCalculator:
    return TradeCalculator()


def _asset(name: str, position: str, value: float, **kwargs) -> TradeAsset:
    return TradeAsset(name=name, position=position, team="KC", value=value, **kwargs)


# ---------------------------------------------------------------------------
# TradeAsset
# ---------------------------------------------------------------------------


class TestTradeAsset:
    def test_display_name_player(self) -> None:
        a = _asset("Patrick Mahomes", "QB", 95.0)
        assert a.display_name == "Patrick Mahomes"

    def test_display_name_pick_first_round(self) -> None:
        a = TradeAsset(
            name="", position="", is_pick=True, pick_year=2025, pick_round=1, value=75.0
        )
        assert "1st" in a.display_name
        assert "2025" in a.display_name

    def test_display_name_pick_second_round(self) -> None:
        a = TradeAsset(
            name="", position="", is_pick=True, pick_year=2026, pick_round=2, value=35.0
        )
        assert "2nd" in a.display_name

    def test_display_name_pick_third_round(self) -> None:
        a = TradeAsset(
            name="", position="", is_pick=True, pick_year=2025, pick_round=3, value=15.0
        )
        assert "3rd" in a.display_name


# ---------------------------------------------------------------------------
# TradeCalculator — _total_value
# ---------------------------------------------------------------------------


class TestTotalValue:
    def test_qb_receives_superflex_multiplier(self, calculator: TradeCalculator) -> None:
        qb = _asset("QB1", "QB", 100.0)
        wr = _asset("WR1", "WR", 100.0)
        qb_total = calculator._total_value([qb], None)
        wr_total = calculator._total_value([wr], None)
        assert qb_total > wr_total
        assert qb_total == pytest.approx(100.0 * 1.30)

    def test_te_receives_superflex_multiplier(self, calculator: TradeCalculator) -> None:
        te = _asset("TE1", "TE", 100.0)
        rb = _asset("RB1", "RB", 100.0)
        assert calculator._total_value([te], None) > calculator._total_value([rb], None)

    def test_multiple_assets_summed(self, calculator: TradeCalculator) -> None:
        assets = [
            _asset("QB1", "QB", 100.0),
            _asset("WR1", "WR", 80.0),
        ]
        total = calculator._total_value(assets, None)
        assert total == pytest.approx(100.0 * 1.30 + 80.0)

    def test_positional_need_increases_value(self, calculator: TradeCalculator) -> None:
        rb = _asset("RB1", "RB", 100.0)
        without_need = calculator._total_value([rb], {})
        with_need = calculator._total_value([rb], {"RB": 3})
        assert with_need > without_need

    def test_zero_need_no_change(self, calculator: TradeCalculator) -> None:
        qb = _asset("QB1", "QB", 100.0)
        without_need = calculator._total_value([qb], {})
        with_need = calculator._total_value([qb], {"QB": 0})
        assert with_need == pytest.approx(without_need)

    def test_unknown_position_uses_default_multiplier(
        self, calculator: TradeCalculator
    ) -> None:
        k = _asset("K1", "K", 10.0)
        total = calculator._total_value([k], None)
        assert total == pytest.approx(10.0)  # Multiplier defaults to 1.0


# ---------------------------------------------------------------------------
# TradeCalculator — calculate_trade_value
# ---------------------------------------------------------------------------


class TestCalculateTradeValue:
    def test_favorable_trade(self, calculator: TradeCalculator) -> None:
        giving = [_asset("RB2", "RB", 50.0)]
        receiving = [_asset("WR1", "WR", 90.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.is_favorable
        assert result.value_delta > 0

    def test_unfavorable_trade(self, calculator: TradeCalculator) -> None:
        giving = [_asset("QB1", "QB", 95.0)]
        receiving = [_asset("RB3", "RB", 30.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert not result.is_favorable
        assert result.value_delta < 0

    def test_even_trade_returns_b_grade(self, calculator: TradeCalculator) -> None:
        giving = [_asset("WR1", "WR", 70.0)]
        receiving = [_asset("WR2", "WR", 70.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.grade == "B"

    def test_grade_a_plus_on_large_gain(self, calculator: TradeCalculator) -> None:
        giving = [_asset("RB2", "RB", 40.0)]
        receiving = [_asset("WR1", "WR", 100.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.grade == "A+"

    def test_grade_d_on_large_loss(self, calculator: TradeCalculator) -> None:
        giving = [_asset("QB1", "QB", 95.0)]
        receiving = [_asset("RB5", "RB", 10.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.grade == "D"

    def test_value_delta_pct_on_result(self, calculator: TradeCalculator) -> None:
        giving = [_asset("WR1", "WR", 80.0)]
        receiving = [_asset("WR2", "WR", 100.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        expected_pct = (100.0 - 80.0) / 80.0
        assert result.value_delta_pct == pytest.approx(expected_pct)

    def test_empty_giving_returns_na_grade(self, calculator: TradeCalculator) -> None:
        result = calculator.calculate_trade_value([], [_asset("WR1", "WR", 50.0)])
        assert result.grade == "N/A"

    def test_result_stores_assets(self, calculator: TradeCalculator) -> None:
        giving = [_asset("QB1", "QB", 80.0)]
        receiving = [_asset("RB1", "RB", 60.0)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.giving == giving
        assert result.receiving == receiving


# ---------------------------------------------------------------------------
# TradeCalculator — calculate_positional_need
# ---------------------------------------------------------------------------


class TestCalculatePositionalNeed:
    def test_need_when_below_depth_target(self, calculator: TradeCalculator) -> None:
        roster = [_asset("QB1", "QB", 90.0)]  # Only 1 QB, target is 4 (2*2)
        need = calculator.calculate_positional_need(
            roster, {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
        )
        assert need["QB"] == 3  # 4 target - 1 have = 3

    def test_no_need_when_at_depth_target(self, calculator: TradeCalculator) -> None:
        roster = [_asset(f"WR{i}", "WR", 70.0) for i in range(6)]  # 6 WRs, target 6
        need = calculator.calculate_positional_need(roster, {"WR": 3})
        assert need["WR"] == 0

    def test_no_negative_need(self, calculator: TradeCalculator) -> None:
        roster = [_asset(f"QB{i}", "QB", 90.0) for i in range(10)]
        need = calculator.calculate_positional_need(roster, {"QB": 2})
        assert need["QB"] == 0

    def test_missing_position_has_max_need(self, calculator: TradeCalculator) -> None:
        roster = [_asset("WR1", "WR", 70.0)]  # No TEs
        need = calculator.calculate_positional_need(roster, {"TE": 1})
        assert need["TE"] == 2  # 2 target, 0 have


# ---------------------------------------------------------------------------
# TradeCalculator — grade thresholds
# ---------------------------------------------------------------------------


class TestGradeThresholds:
    @pytest.mark.parametrize(
        "giving_val,receiving_val,expected_grade",
        [
            (100.0, 125.0, "A+"),   # +25% → A+
            (100.0, 113.0, "A"),    # +13% → A
            (100.0, 107.0, "B+"),   # +7%  → B+
            (100.0, 101.0, "B"),    # +1%  → B
            (100.0, 93.0, "C+"),    # -7%  → C+
            (100.0, 85.0, "C"),     # -15% → C
            (100.0, 70.0, "D"),     # -30% → D
        ],
    )
    def test_grade(
        self,
        giving_val: float,
        receiving_val: float,
        expected_grade: str,
        calculator: TradeCalculator,
    ) -> None:
        giving = [_asset("WR1", "WR", giving_val)]
        receiving = [_asset("WR2", "WR", receiving_val)]
        result = calculator.calculate_trade_value(giving, receiving)
        assert result.grade == expected_grade
