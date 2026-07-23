"""Tests for Sleeper league format detection."""

from __future__ import annotations

import pytest

from src.league_settings import (
    LeagueFormat,
    detect_league_format,
    draftsharks_params,
    fantasycalc_params,
    position_multipliers,
)


def _league(
    roster_positions: list[str],
    scoring: dict | None = None,
    total_rosters: int = 12,
    league_type: int = 2,
) -> dict:
    return {
        "roster_positions": roster_positions,
        "scoring_settings": scoring or {},
        "total_rosters": total_rosters,
        "settings": {"type": league_type},
    }


SUPERFLEX_SLOTS = [
    "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN",
]
ONE_QB_SLOTS = [
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN", "BN",
]


class TestDetectLeagueFormat:
    def test_superflex_full_ppr(self) -> None:
        fmt = detect_league_format(_league(SUPERFLEX_SLOTS, {"rec": 1.0}))
        assert fmt.num_qbs == 2
        assert fmt.is_superflex
        assert fmt.ppr == 1.0
        assert not fmt.te_premium
        assert fmt.is_dynasty
        assert fmt.num_teams == 12

    def test_one_qb_half_ppr(self) -> None:
        fmt = detect_league_format(
            _league(ONE_QB_SLOTS, {"rec": 0.5}, total_rosters=10)
        )
        assert fmt.num_qbs == 1
        assert not fmt.is_superflex
        assert fmt.ppr == 0.5
        assert fmt.num_teams == 10

    def test_te_premium_detected(self) -> None:
        fmt = detect_league_format(
            _league(SUPERFLEX_SLOTS, {"rec": 1.0, "bonus_rec_te": 0.5})
        )
        assert fmt.te_premium

    def test_redraft_detected(self) -> None:
        fmt = detect_league_format(_league(ONE_QB_SLOTS, league_type=0))
        assert not fmt.is_dynasty

    def test_starter_slots_flex_counts_toward_wr(self) -> None:
        fmt = detect_league_format(_league(SUPERFLEX_SLOTS))
        slots = fmt.slots_dict()
        assert slots["QB"] == 2  # QB + SUPER_FLEX
        assert slots["RB"] == 2
        assert slots["WR"] == 4  # 3 WR + 1 FLEX
        assert slots["TE"] == 1

    def test_missing_positions_get_defaults(self) -> None:
        fmt = detect_league_format(_league([]))
        assert fmt.num_qbs == 1
        assert fmt.slots_dict()["TE"] == 1

    def test_ppr_snaps_to_supported_values(self) -> None:
        fmt = detect_league_format(_league(ONE_QB_SLOTS, {"rec": 0.9}))
        assert fmt.ppr == 1.0

    def test_is_hashable_for_caching(self) -> None:
        fmt = detect_league_format(_league(SUPERFLEX_SLOTS))
        assert hash(fmt) == hash(detect_league_format(_league(SUPERFLEX_SLOTS)))
        assert isinstance(fmt.cache_key(), str)

    def test_describe(self) -> None:
        fmt = detect_league_format(_league(SUPERFLEX_SLOTS, {"rec": 1.0}))
        desc = fmt.describe()
        assert "12-team" in desc
        assert "Superflex" in desc
        assert "Full PPR" in desc


class TestQueryParams:
    def test_fantasycalc_superflex_full_ppr(self) -> None:
        fmt = LeagueFormat(num_teams=12, num_qbs=2, ppr=1.0)
        params = fantasycalc_params(fmt)
        assert params == {
            "ppr": "1",
            "num_qbs": "2",
            "num_teams": "12",
            "is_dynasty": True,
        }

    def test_fantasycalc_one_qb_non_ppr(self) -> None:
        fmt = LeagueFormat(num_teams=10, num_qbs=1, ppr=0.0)
        params = fantasycalc_params(fmt)
        assert params["ppr"] == "0"
        assert params["num_qbs"] == "1"
        assert params["num_teams"] == "10"

    def test_fantasycalc_team_count_snaps(self) -> None:
        fmt = LeagueFormat(num_teams=16, num_qbs=2)
        assert fantasycalc_params(fmt)["num_teams"] == "14"

    def test_draftsharks_superflex_ppr(self) -> None:
        # is_dynasty is forced to "false" regardless of fmt.is_dynasty: the
        # upstream DraftSharks dynasty-rankings endpoint currently 502s.
        fmt = LeagueFormat(num_qbs=2, ppr=1.0, is_dynasty=True)
        params = draftsharks_params(fmt)
        assert params == {
            "scoring": "ppr",
            "league_type": "superflex",
            "is_dynasty": "false",
        }

    def test_draftsharks_standard_half_ppr(self) -> None:
        fmt = LeagueFormat(num_qbs=1, ppr=0.5, is_dynasty=False)
        params = draftsharks_params(fmt)
        assert params["scoring"] == "half-ppr"
        assert params["league_type"] == "standard"
        assert params["is_dynasty"] == "false"


class TestPositionMultipliers:
    def test_superflex_qb_boost(self) -> None:
        mults = position_multipliers(LeagueFormat(num_qbs=2))
        assert mults["QB"] == pytest.approx(1.30)

    def test_one_qb_no_boost(self) -> None:
        mults = position_multipliers(LeagueFormat(num_qbs=1))
        assert mults["QB"] == pytest.approx(1.00)

    def test_te_premium_boost(self) -> None:
        assert position_multipliers(LeagueFormat(te_premium=True))["TE"] == pytest.approx(1.20)
        assert position_multipliers(LeagueFormat(te_premium=False))["TE"] == pytest.approx(1.10)
