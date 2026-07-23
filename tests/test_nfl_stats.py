"""Tests for the nflverse performance source (no network).

nflreadpy loaders are monkeypatched to return canned **polars** frames — the
same type the real loaders return — which ``nfl_stats`` consumes natively.
"""

from __future__ import annotations

import polars as pl
import pytest

from src import nfl_stats
from src.nfl_stats import (
    PlayerPerformance,
    fetch_season_stats,
    production_signal_map,
)


@pytest.fixture
def fake_nflverse(monkeypatch):
    stats = pl.DataFrame(
        [
            {
                "player_id": "00-0036322", "player_display_name": "Justin Jefferson",
                "player_name": "J.Jefferson", "position": "WR", "recent_team": "MIN",
                "games": 17, "receptions": 103, "targets": 154, "receiving_yards": 1533,
                "receiving_tds": 10, "fantasy_points_ppr": 317.5, "target_share": 0.3,
                "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0,
                "passing_interceptions": 0, "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
                "fantasy_points": 214.5,
            },
        ]
    )
    crosswalk = pl.DataFrame(
        [{"gsis_id": "00-0036322", "sleeper_id": 6794.0, "fantasy_data_id": 21685.0}]
    )
    monkeypatch.setattr(
        nfl_stats.nfl, "load_player_stats", lambda *a, **k: stats
    )
    monkeypatch.setattr(
        nfl_stats.nfl, "load_ff_playerids", lambda *a, **k: crosswalk
    )


class TestFetchSeasonStats:
    def test_builds_performance_with_crosswalk_ids(self, fake_nflverse):
        perf = fetch_season_stats(2024)
        assert len(perf) == 1
        p = perf[0]
        assert p.name == "Justin Jefferson" and p.position == "WR"
        # crosswalk float IDs coerced to clean strings
        assert p.sleeper_id == "6794"
        assert p.fantasy_data_id == "21685"
        assert p.gsis_id == "00-0036322"
        assert p.receiving_yards == 1533.0
        assert p.fantasy_points_ppr == 317.5

    def test_ppg_derived(self, fake_nflverse):
        p = fetch_season_stats(2024)[0]
        assert abs(p.fantasy_points_ppr_per_game - 317.5 / 17) < 1e-9


class TestProductionSignal:
    def _wr(self, name, ppr, sleeper, games=16):
        return PlayerPerformance(
            name=name, position="WR", sleeper_id=sleeper, games=games,
            fantasy_points_ppr=ppr,
        )

    def test_top_and_bottom_span_full_range(self):
        perf = [
            self._wr("Best", 300.0, "1"),
            self._wr("Mid", 150.0, "2"),
            self._wr("Worst", 30.0, "3"),
        ]
        sig = production_signal_map(perf)
        assert sig["1"] == pytest.approx(1.0)   # top producer
        assert sig["3"] == pytest.approx(-1.0)  # bottom producer
        assert sig["2"] == pytest.approx(0.0)   # median
        # keyed by sleeper_id AND normalized name
        assert sig["name:best"] == pytest.approx(1.0)

    def test_low_sample_players_excluded(self):
        perf = [
            self._wr("Regular", 200.0, "1", games=16),
            self._wr("Cameo", 40.0, "2", games=1),  # below min_games
        ]
        sig = production_signal_map(perf, min_games=4)
        assert "2" not in sig and "name:cameo" not in sig
