"""Tests for the player-detail assembler (no network, no Streamlit)."""

from __future__ import annotations

import pytest

from src.nfl_stats import PlayerPerformance
from src.player_detail import build_perf_index, build_player_detail, lookup_player
from src.value_engine import PlayerValuation


def _valuation(**kw) -> PlayerValuation:
    base = dict(
        key="6786",
        name="Justin Jefferson",
        position="WR",
        team="MIN",
        age=26,
        sleeper_id="6786",
        base_value=90.0,
        adjusted_value=95.0,
        values_by_source={"fantasycalc": 100.0, "ktc": 80.0, "draftsharks": 90.0},
        components={"age": -0.05, "production": 0.10},
    )
    base.update(kw)
    return PlayerValuation(**base)


def _perf(**kw) -> PlayerPerformance:
    base = dict(
        name="Justin Jefferson",
        position="WR",
        team="MIN",
        season=2024,
        sleeper_id="6786",
        fantasy_data_id="19801",
        games=15,
        targets=140,
        receptions=100,
        receiving_yards=1500,
        fantasy_points_ppr=310,
    )
    base.update(kw)
    return PlayerPerformance(**base)


class TestValueComparison:
    def test_consensus_and_spread(self):
        d = build_player_detail(
            name="Justin Jefferson", position="WR", valuation=_valuation()
        )
        assert d.consensus == pytest.approx(90.0)  # mean of 100/80/90
        assert d.spread_pct == pytest.approx((100 - 80) / 90)
        assert d.high_source == "fantasycalc"
        assert d.low_source == "ktc"

    def test_single_source_has_no_spread(self):
        d = build_player_detail(
            name="A",
            position="WR",
            valuation=_valuation(values_by_source={"fantasycalc": 70.0}),
        )
        assert d.consensus == pytest.approx(70.0)
        assert d.spread_pct == 0.0
        assert d.high_source is None and d.low_source is None

    def test_no_valuation_is_safe(self):
        d = build_player_detail(name="Rookie", position="RB", perf=_perf(name="Rookie"))
        assert d.adjusted_value == 0.0
        assert d.values_by_source == {}
        # identity still resolves from performance
        assert d.fantasy_data_id == "19801"
        assert d.team == "MIN"


class TestIdentityAndKeys:
    def test_ids_and_keys_populated(self):
        d = build_player_detail(
            name="Justin Jefferson",
            position="WR",
            sleeper_id="6786",
            valuation=_valuation(fc_player_id="fc42"),
            perf=_perf(),
        )
        assert d.sleeper_id == "6786"
        assert d.fantasy_data_id == "19801"  # from perf → the news key
        assert d.value_key == "6786"  # from valuation.key → local history key
        assert d.fc_player_id == "fc42"

    def test_position_backfilled_from_valuation_when_missing(self):
        # Arbitrage rows have no position; it should come from the valuation.
        d = build_player_detail(name="Justin Jefferson", valuation=_valuation())
        assert d.position == "WR"


class TestOwnership:
    def test_rostered(self):
        d = build_player_detail(
            name="Justin Jefferson",
            valuation=_valuation(),
            owner="Team Alpha",
            owner_timeline="contender",
        )
        assert d.rostered is True
        assert d.owner_team == "Team Alpha"

    def test_free_agent(self):
        d = build_player_detail(name="Justin Jefferson", valuation=_valuation())
        assert d.rostered is False
        assert d.owner_team is None


class TestPerfIndex:
    def test_lookup_by_sleeper_id_then_name(self):
        perf = _perf()
        index = build_perf_index([perf])
        assert lookup_player(index, "6786", "wrong name") is perf  # id wins
        assert lookup_player(index, None, "Justin Jefferson") is perf  # name fallback
        assert lookup_player(index, "9999", "Nobody") is None

    def test_name_match_is_normalized(self):
        perf = _perf()
        index = build_perf_index([perf])
        # Different punctuation/casing still resolves via normalize_name.
        assert lookup_player(index, None, "justin  jefferson") is perf
