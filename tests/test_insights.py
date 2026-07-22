"""Tests for smart analytics: buy-low/sell-high, team profiling,
timeline fit, VOR, and the analyzer's mutual-benefit filtering."""

from __future__ import annotations

import pytest

from src.insights import (
    classify_teams,
    find_buy_low_sell_high,
    profile_team,
    replacement_baselines,
    timeline_fit_bonus,
    value_based_need,
)
from src.league_settings import LeagueFormat
from src.trade_analyzer import TradeAnalyzer
from src.trade_calculator import TradeAsset, grade_at_least
from src.value_engine import PlayerValuation


def valuation(
    name: str,
    value: float = 50.0,
    position: str = "WR",
    age: float | None = 25.0,
    trend_frac: float | None = None,
    sources: dict[str, float] | None = None,
) -> PlayerValuation:
    return PlayerValuation(
        key=f"name:{name.lower()}",
        name=name,
        position=position,
        age=age,
        base_value=value,
        adjusted_value=value,
        trend_frac=trend_frac,
        values_by_source=sources or {"fantasycalc": value},
    )


def asset(name: str, position: str = "WR", value: float = 50.0, age: float | None = 25.0, **kw) -> TradeAsset:
    return TradeAsset(name=name, position=position, value=value, age=age, **kw)


# ---------------------------------------------------------------------------
# grade_at_least
# ---------------------------------------------------------------------------


class TestGradeAtLeast:
    def test_better_grade_passes(self) -> None:
        assert grade_at_least("A", "B")
        assert grade_at_least("C+", "C+")

    def test_worse_grade_fails(self) -> None:
        assert not grade_at_least("C", "C+")
        assert not grade_at_least("D", "B")

    def test_na_never_passes(self) -> None:
        assert not grade_at_least("N/A", "D")


# ---------------------------------------------------------------------------
# Buy low / sell high
# ---------------------------------------------------------------------------


class TestBuyLowSellHigh:
    def test_falling_young_player_is_buy_low(self) -> None:
        vals = {"a": valuation("Young Faller", age=23.0, trend_frac=-0.10)}
        insights = find_buy_low_sell_high(vals)
        assert len(insights) == 1
        assert insights[0].kind == "buy_low"
        assert "down" in insights[0].reason.lower()

    def test_falling_old_rb_not_buy_low(self) -> None:
        # RB at 29 has age multiplier 0.65 — the decline is real, not noise
        vals = {"a": valuation("Aging RB", position="RB", age=29.0, trend_frac=-0.10)}
        assert find_buy_low_sell_high(vals) == []

    def test_rising_aging_player_is_sell_high(self) -> None:
        vals = {"a": valuation("Old Riser", position="WR", age=29.0, trend_frac=0.10)}
        insights = find_buy_low_sell_high(vals)
        assert len(insights) == 1
        assert insights[0].kind == "sell_high"

    def test_rising_young_player_not_flagged(self) -> None:
        vals = {"a": valuation("Young Riser", age=22.0, trend_frac=0.10)}
        assert find_buy_low_sell_high(vals) == []

    def test_low_value_players_ignored(self) -> None:
        vals = {"a": valuation("Roster Clogger", value=5.0, age=23.0, trend_frac=-0.2)}
        assert find_buy_low_sell_high(vals) == []

    def test_local_delta_triggers_without_trend(self) -> None:
        vals = {"k": valuation("Local Faller", age=23.0)}
        vals["k"].key = "k"
        insights = find_buy_low_sell_high(vals, deltas={"k": -5.0})
        assert len(insights) == 1
        assert insights[0].kind == "buy_low"

    def test_sorted_by_score_and_capped(self) -> None:
        vals = {
            f"p{i}": valuation(f"Faller {i}", age=23.0, trend_frac=-0.05 - i * 0.01)
            for i in range(20)
        }
        insights = find_buy_low_sell_high(vals, top_n=5)
        assert len(insights) == 5
        scores = [i.score for i in insights]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Team profiling
# ---------------------------------------------------------------------------


class TestProfileTeam:
    def test_value_split(self) -> None:
        assets = [
            asset("Old Vet", age=30.0, value=40.0),
            asset("Prime Guy", age=26.0, value=30.0),
            asset("Youngster", age=22.0, value=20.0),
        ]
        picks = [TradeAsset(name="Pick", position="PICK", is_pick=True, value=10.0)]
        p = profile_team(1, "Team", assets, picks)
        assert p.total_value == pytest.approx(100.0)
        assert p.win_now_value == pytest.approx(40.0)
        assert p.core_value == pytest.approx(30.0)
        assert p.future_value == pytest.approx(30.0)  # youngster + pick
        assert p.pick_value == pytest.approx(10.0)

    def test_old_winning_team_is_contender(self) -> None:
        assets = [asset(f"Vet {i}", age=29.0, value=30.0) for i in range(5)]
        p = profile_team(1, "Vets", assets, wins=8, losses=2)
        assert p.classification == "contender"

    def test_young_team_with_picks_is_rebuilder(self) -> None:
        assets = [asset(f"Kid {i}", age=22.0, value=25.0) for i in range(4)]
        picks = [
            TradeAsset(name=f"Pick {i}", position="PICK", is_pick=True, value=50.0)
            for i in range(2)
        ]
        p = profile_team(2, "Youth", assets, picks)
        assert p.classification == "rebuilder"

    def test_mixed_team_is_balanced(self) -> None:
        assets = [
            asset("Old", age=29.0, value=50.0),
            asset("Young", age=22.0, value=50.0),
            asset("Core", age=25.5, value=50.0),
        ]
        p = profile_team(3, "Mixed", assets)
        assert p.classification == "balanced"

    def test_record_breaks_ties(self) -> None:
        assets = [asset("Core Player", age=25.5, value=50.0)]
        assert profile_team(1, "T", assets, wins=9, losses=3).classification == "contender"
        assert profile_team(1, "T", assets, wins=3, losses=9).classification == "rebuilder"

    def test_unknown_age_counts_as_core(self) -> None:
        p = profile_team(1, "T", [asset("Mystery", age=None, value=60.0)])
        assert p.core_value == pytest.approx(60.0)
        assert p.win_now_value == 0.0


# ---------------------------------------------------------------------------
# League-relative classification
# ---------------------------------------------------------------------------


class TestClassifyTeams:
    def _young_team(self, rid: int, win_now: float, future: float) -> object:
        # Build a roster whose value splits hit the target shares
        assets = []
        if win_now:
            assets.append(asset(f"Vet{rid}", age=30.0, value=win_now))
        if future:
            assets.append(asset(f"Kid{rid}", age=22.0, value=future))
        assets.append(asset(f"Core{rid}", age=25.5, value=20.0))
        return profile_team(rid, f"Team {rid}", assets)

    def test_spreads_labels_across_league(self) -> None:
        # 12 future-heavy teams with a graduated win-now tilt:
        # team 0 least win-now, team 11 most.
        profiles = {
            rid: self._young_team(rid, win_now=rid * 5.0, future=60.0)
            for rid in range(12)
        }
        classify_teams(profiles)
        labels = {p.classification for p in profiles.values()}
        # A relative classifier must produce all three tiers, not one label
        assert labels == {"contender", "balanced", "rebuilder"}
        # The most win-now team is a contender, the least a rebuilder
        assert profiles[11].classification == "contender"
        assert profiles[0].classification == "rebuilder"

    def test_record_influences_when_present(self) -> None:
        # Two rosters identical in construction; records decide
        base = lambda rid: [asset(f"P{rid}", age=25.5, value=100.0)]
        profiles = {
            i: profile_team(
                i, f"Team {i}", base(i),
                wins=(9 if i == 0 else 0), losses=(0 if i == 0 else 9),
            )
            for i in range(6)
        }
        classify_teams(profiles)
        assert profiles[0].classification == "contender"

    def test_small_league_all_balanced(self) -> None:
        profiles = {i: self._young_team(i, 10.0, 50.0) for i in range(2)}
        classify_teams(profiles)
        assert all(p.classification == "balanced" for p in profiles.values())

    def test_empty_is_safe(self) -> None:
        assert classify_teams({}) == {}


# ---------------------------------------------------------------------------
# Timeline fit
# ---------------------------------------------------------------------------


class TestTimelineFitBonus:
    GIVING = (asset("My Pick", position="PICK", value=40.0, age=None, is_pick=True),)
    RECEIVING = (asset("Their Vet", age=29.0, value=45.0),)

    def test_contender_buying_win_now_from_rebuilder(self) -> None:
        bonus = timeline_fit_bonus("contender", "rebuilder", self.GIVING, self.RECEIVING)
        assert bonus == pytest.approx(0.05)  # 0.03 receive vet + 0.02 give pick

    def test_same_classification_no_bonus(self) -> None:
        assert timeline_fit_bonus("contender", "contender", self.GIVING, self.RECEIVING) == 0.0

    def test_rebuilder_selling_vets_for_youth(self) -> None:
        giving = (asset("My Vet", age=30.0, value=45.0),)
        receiving = (asset("Their Rookie", age=21.0, value=40.0),)
        bonus = timeline_fit_bonus("rebuilder", "contender", giving, receiving)
        assert bonus == pytest.approx(0.05)

    def test_bonus_capped(self) -> None:
        receiving = tuple(asset(f"Vet {i}", age=30.0) for i in range(10))
        bonus = timeline_fit_bonus("contender", "rebuilder", (), receiving)
        assert bonus == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# Value over replacement
# ---------------------------------------------------------------------------


class TestVOR:
    def _valuations(self, position: str, values: list[float]) -> dict:
        return {
            f"{position}{i}": valuation(f"{position} {i}", value=v, position=position)
            for i, v in enumerate(values)
        }

    def test_baseline_is_first_non_starter(self) -> None:
        fmt = LeagueFormat(num_teams=2, starter_slots=(("QB", 1),))
        vals = self._valuations("QB", [90.0, 80.0, 70.0, 60.0])
        baselines = replacement_baselines(vals, fmt)
        # 2 teams × 1 slot = 2 starters; replacement is the 3rd QB
        assert baselines["QB"] == pytest.approx(70.0)

    def test_thin_position_baseline_zero(self) -> None:
        fmt = LeagueFormat(num_teams=12, starter_slots=(("TE", 1),))
        vals = self._valuations("TE", [50.0, 40.0])
        assert replacement_baselines(vals, fmt)["TE"] == 0.0

    def test_value_based_need_below_replacement(self) -> None:
        baselines = {"RB": 40.0}
        roster = [asset("Bad RB1", position="RB", value=30.0),
                  asset("Bad RB2", position="RB", value=20.0)]
        need = value_based_need(roster, baselines, {"RB": 2})
        # shortage = (40-30) + (40-20) = 30 → 0.75 replacement players short
        assert need["RB"] == pytest.approx(0.75)

    def test_no_need_when_starters_clear_baseline(self) -> None:
        baselines = {"WR": 40.0}
        roster = [asset("Stud WR", position="WR", value=90.0),
                  asset("Good WR", position="WR", value=55.0)]
        assert value_based_need(roster, baselines, {"WR": 2})["WR"] == 0.0

    def test_empty_position_maximal_need(self) -> None:
        baselines = {"QB": 50.0}
        need = value_based_need([], baselines, {"QB": 2})
        assert need["QB"] == pytest.approx(2.0)  # two full slots short

    def test_bad_depth_still_registers_unlike_headcount(self) -> None:
        # Three RBs all below replacement: headcount says fine, VOR says need
        baselines = {"RB": 40.0}
        roster = [asset(f"JAG {i}", position="RB", value=15.0) for i in range(3)]
        need = value_based_need(roster, baselines, {"RB": 2})
        assert need["RB"] > 1.0


# ---------------------------------------------------------------------------
# Mutual-benefit trade filtering (TradeAnalyzer extension)
# ---------------------------------------------------------------------------


class TestMutualBenefit:
    def _setup(self):
        my_roster = [asset("My Guy", value=50.0)]
        their_roster = {"Rivals": [asset("Their Guy", value=60.0)]}
        values = {"My Guy": 50.0, "Their Guy": 60.0}
        return TradeAnalyzer(), my_roster, their_roster, values

    def test_lopsided_trade_filtered_by_their_grade(self) -> None:
        analyzer, mine, theirs, values = self._setup()
        # +20% for me = -16.7% for them = their grade "C" — below C+
        proposals = analyzer.find_best_trades(
            mine, theirs, values, min_their_grade="C+"
        )
        assert proposals == []

    def test_kept_when_min_grade_lowered(self) -> None:
        analyzer, mine, theirs, values = self._setup()
        proposals = analyzer.find_best_trades(
            mine, theirs, values, min_their_grade="C"
        )
        assert len(proposals) == 1
        assert proposals[0].their_grade == "C"

    def test_counterparty_need_makes_trade_mutual(self) -> None:
        analyzer, mine, theirs, values = self._setup()
        # Their WR need boosts what they'd receive: 50 * (1 + 4*0.05) = 60 → B
        proposals = analyzer.find_best_trades(
            mine,
            theirs,
            values,
            counterparty_needs={"Rivals": {"WR": 4}},
            min_their_grade="C+",
        )
        assert len(proposals) == 1
        assert proposals[0].their_grade == "B"

    def test_default_behavior_unchanged(self) -> None:
        analyzer, mine, theirs, values = self._setup()
        proposals = analyzer.find_best_trades(mine, theirs, values)
        assert len(proposals) == 1
        assert proposals[0].their_grade is None

    def test_max_per_team_caps_and_diversifies(self) -> None:
        analyzer = TradeAnalyzer()
        mine = [asset("My Guy", value=50.0)]
        # One very exploitable team plus a modest one
        theirs = {
            "Fleeceable": [asset(f"Overpriced {i}", value=60.0 + i) for i in range(5)],
            "Fair": [asset("Decent", value=55.0)],
        }
        values = {"My Guy": 50.0}
        values.update({f"Overpriced {i}": 60.0 + i for i in range(5)})
        values["Decent"] = 55.0

        capped = analyzer.find_best_trades(
            mine, theirs, values, top_n=10, max_per_team=2
        )
        from collections import Counter

        counts = Counter(p.their_team for p in capped)
        assert counts["Fleeceable"] <= 2
        # The modest team still surfaces despite lower scores
        assert "Fair" in counts

    def test_bonus_scorer_reorders(self) -> None:
        analyzer = TradeAnalyzer()
        mine = [asset("My Guy", value=50.0)]
        theirs = {
            "Small Win": [asset("Small Upgrade", value=55.0)],
            "Big Win": [asset("Big Upgrade", value=70.0)],
        }
        values = {"My Guy": 50.0, "Small Upgrade": 55.0, "Big Upgrade": 70.0}
        proposals = analyzer.find_best_trades(
            mine,
            theirs,
            values,
            bonus_scorer=lambda team, g, r: 5.0 if team == "Small Win" else 0.0,
        )
        assert proposals[0].their_team == "Small Win"
