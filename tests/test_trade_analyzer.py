"""Tests for the trade analyzer (arbitrage + best-trade finder)."""

from __future__ import annotations

import pytest

from src.trade_analyzer import ArbitrageOpportunity, TradeAnalyzer, _enrich
from src.trade_calculator import TradeAsset, TradeCalculator, TradeResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def analyzer() -> TradeAnalyzer:
    return TradeAnalyzer(calculator=TradeCalculator())


def _asset(name: str, position: str, value: float = 0.0) -> TradeAsset:
    return TradeAsset(name=name, position=position, team="TEST", value=value)


# ---------------------------------------------------------------------------
# _enrich helper
# ---------------------------------------------------------------------------


class TestEnrich:
    def test_fills_value_from_lookup(self) -> None:
        assets = [_asset("Player A", "WR", 0.0)]
        enriched = _enrich(assets, {"Player A": 75.0})
        assert enriched[0].value == 75.0

    def test_keeps_existing_value_when_not_in_lookup(self) -> None:
        assets = [_asset("Player B", "RB", 50.0)]
        enriched = _enrich(assets, {})
        assert enriched[0].value == 50.0

    def test_preserves_other_fields(self) -> None:
        asset = TradeAsset(
            name="QB1", position="QB", team="KC", age=28.0, value=90.0
        )
        enriched = _enrich([asset], {"QB1": 95.0})
        result = enriched[0]
        assert result.position == "QB"
        assert result.team == "KC"
        assert result.age == 28.0
        assert result.value == 95.0


# ---------------------------------------------------------------------------
# ArbitrageOpportunity — properties
# ---------------------------------------------------------------------------


class TestArbitrageOpportunity:
    def test_high_source(self) -> None:
        opp = ArbitrageOpportunity(
            player_name="WR1",
            position="WR",
            team="SF",
            values_by_source={"ktc": 90.0, "fantasycalc": 50.0},
            consensus_value=70.0,
            max_value=90.0,
            min_value=50.0,
            spread_pct=0.57,
            recommendation="sell",
        )
        assert opp.high_source == "ktc"

    def test_low_source(self) -> None:
        opp = ArbitrageOpportunity(
            player_name="WR1",
            position="WR",
            team="SF",
            values_by_source={"ktc": 90.0, "fantasycalc": 50.0},
            consensus_value=70.0,
            max_value=90.0,
            min_value=50.0,
            spread_pct=0.57,
            recommendation="sell",
        )
        assert opp.low_source == "fantasycalc"

    def test_empty_sources(self) -> None:
        opp = ArbitrageOpportunity(
            player_name="X",
            position="WR",
            team="",
            values_by_source={},
            consensus_value=0.0,
            max_value=0.0,
            min_value=0.0,
            spread_pct=0.0,
            recommendation="buy",
        )
        assert opp.high_source == ""
        assert opp.low_source == ""


# ---------------------------------------------------------------------------
# TradeAnalyzer — find_arbitrage
# ---------------------------------------------------------------------------


class TestFindArbitrage:
    def test_flags_high_spread_player(self, analyzer: TradeAnalyzer) -> None:
        values_by_source = {
            "ktc": {"CeeDee Lamb": 95.0, "Common WR": 50.0},
            "fantasycalc": {"CeeDee Lamb": 60.0, "Common WR": 48.0},
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.20)
        names = [o.player_name for o in opps]
        assert "CeeDee Lamb" in names

    def test_ignores_low_spread_player(self, analyzer: TradeAnalyzer) -> None:
        values_by_source = {
            "ktc": {"Common WR": 51.0},
            "fantasycalc": {"Common WR": 49.0},
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.20)
        assert opps == []

    def test_sorted_by_spread_descending(self, analyzer: TradeAnalyzer) -> None:
        values_by_source = {
            "ktc": {"PlayerA": 100.0, "PlayerB": 80.0},
            "fantasycalc": {"PlayerA": 50.0, "PlayerB": 60.0},
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.0)
        assert opps[0].spread_pct >= opps[1].spread_pct

    def test_skips_player_with_only_one_source(self, analyzer: TradeAnalyzer) -> None:
        values_by_source = {
            "ktc": {"Solo Player": 90.0},
            "fantasycalc": {},
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.0)
        assert opps == []

    def test_buy_recommendation_when_min_well_below_consensus(
        self, analyzer: TradeAnalyzer
    ) -> None:
        values_by_source = {
            "ktc": {"BuyMe": 100.0},
            "fantasycalc": {"BuyMe": 40.0},  # min = 40, consensus = 70, min < 70*0.85
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.20)
        buy_opps = [o for o in opps if o.player_name == "BuyMe"]
        assert len(buy_opps) == 1
        assert buy_opps[0].recommendation == "buy"

    def test_sell_recommendation_when_spread_moderate(
        self, analyzer: TradeAnalyzer
    ) -> None:
        values_by_source = {
            "ktc": {"SellMe": 90.0},
            "fantasycalc": {"SellMe": 70.0},  # min = 70 ≥ consensus*0.85
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.10)
        sell_opps = [o for o in opps if o.player_name == "SellMe"]
        assert len(sell_opps) == 1
        assert sell_opps[0].recommendation == "sell"

    def test_empty_inputs_returns_empty(self, analyzer: TradeAnalyzer) -> None:
        assert analyzer.find_arbitrage({}) == []

    def test_consensus_value_is_average(self, analyzer: TradeAnalyzer) -> None:
        values_by_source = {
            "ktc": {"PlayerX": 100.0},
            "fantasycalc": {"PlayerX": 60.0},
        }
        opps = analyzer.find_arbitrage(values_by_source, spread_threshold=0.0)
        assert opps[0].consensus_value == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# TradeAnalyzer — find_best_trades
# ---------------------------------------------------------------------------


class TestFindBestTrades:
    def _build_roster(
        self, names_positions_values: list[tuple[str, str, float]]
    ) -> list[TradeAsset]:
        return [
            _asset(name, pos, val) for name, pos, val in names_positions_values
        ]

    def test_returns_only_favourable_trades(self, analyzer: TradeAnalyzer) -> None:
        my_roster = self._build_roster([("MyQB", "QB", 95.0)])
        all_rosters = {
            "Team2": self._build_roster([("TheirRB", "RB", 30.0)])
        }
        values = {"MyQB": 95.0, "TheirRB": 30.0}
        proposals = analyzer.find_best_trades(my_roster, all_rosters, values)
        # Giving up QB (95) for RB (30) is not favorable
        assert all(p.result.is_favorable for p in proposals)

    def test_identifies_good_trade(self, analyzer: TradeAnalyzer) -> None:
        my_roster = self._build_roster([("MyRB", "RB", 30.0)])
        all_rosters = {
            "Team2": self._build_roster([("TheirWR", "WR", 80.0)])
        }
        values = {"MyRB": 30.0, "TheirWR": 80.0}
        proposals = analyzer.find_best_trades(my_roster, all_rosters, values)
        assert len(proposals) >= 1
        assert proposals[0].result.is_favorable

    def test_respects_top_n(self, analyzer: TradeAnalyzer) -> None:
        my_roster = self._build_roster(
            [(f"MyWR{i}", "WR", 40.0) for i in range(5)]
        )
        their_players = [(f"TheirWR{i}", "WR", 70.0) for i in range(5)]
        all_rosters = {"Team2": self._build_roster(their_players)}
        values = {
            **{f"MyWR{i}": 40.0 for i in range(5)},
            **{f"TheirWR{i}": 70.0 for i in range(5)},
        }
        proposals = analyzer.find_best_trades(
            my_roster, all_rosters, values, top_n=3
        )
        assert len(proposals) <= 3

    def test_no_proposals_when_all_trades_unfavorable(
        self, analyzer: TradeAnalyzer
    ) -> None:
        my_roster = self._build_roster([("MyQB", "QB", 95.0)])
        all_rosters = {
            "Team2": self._build_roster([("TheirK", "K", 5.0)])
        }
        values = {"MyQB": 95.0, "TheirK": 5.0}
        proposals = analyzer.find_best_trades(my_roster, all_rosters, values)
        assert proposals == []

    def test_sorted_by_score_descending(self, analyzer: TradeAnalyzer) -> None:
        my_roster = self._build_roster(
            [("MyWR1", "WR", 40.0), ("MyWR2", "WR", 45.0)]
        )
        all_rosters = {
            "Team2": self._build_roster(
                [("TheirWR1", "WR", 70.0), ("TheirWR2", "WR", 90.0)]
            )
        }
        values = {
            "MyWR1": 40.0, "MyWR2": 45.0,
            "TheirWR1": 70.0, "TheirWR2": 90.0,
        }
        proposals = analyzer.find_best_trades(my_roster, all_rosters, values)
        scores = [p.score for p in proposals]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# TradeAnalyzer — rank_trade_targets
# ---------------------------------------------------------------------------


class TestRankTradeTargets:
    def test_excludes_own_players(self, analyzer: TradeAnalyzer) -> None:
        my_roster = [_asset("SharedPlayer", "WR", 80.0)]
        all_rosters = {"Team2": [_asset("SharedPlayer", "WR", 80.0)]}
        values = {"SharedPlayer": 80.0}
        targets = analyzer.rank_trade_targets(my_roster, all_rosters, values)
        assert all(a.name != "SharedPlayer" for a, _, _ in targets)

    def test_sorted_by_score_descending(self, analyzer: TradeAnalyzer) -> None:
        my_roster = []
        all_rosters = {
            "Team2": [
                _asset("LowWR", "WR", 0.0),
                _asset("HighWR", "WR", 0.0),
            ]
        }
        values = {"LowWR": 30.0, "HighWR": 90.0}
        targets = analyzer.rank_trade_targets(my_roster, all_rosters, values)
        scores = [score for _, _, score in targets]
        assert scores == sorted(scores, reverse=True)

    def test_positional_need_boosts_score(self, analyzer: TradeAnalyzer) -> None:
        my_roster = []
        all_rosters = {
            "Team2": [
                _asset("QB1", "QB", 0.0),
                _asset("WR1", "WR", 0.0),
            ]
        }
        values = {"QB1": 50.0, "WR1": 50.0}
        # High need for WR should push WR above QB even at equal base value
        need = {"WR": 5, "QB": 0}
        targets = analyzer.rank_trade_targets(
            my_roster, all_rosters, values, positional_need=need
        )
        names_in_order = [a.name for a, _, _ in targets]
        assert names_in_order[0] == "WR1"

    def test_empty_rosters_returns_empty(self, analyzer: TradeAnalyzer) -> None:
        targets = analyzer.rank_trade_targets([], {}, {})
        assert targets == []
