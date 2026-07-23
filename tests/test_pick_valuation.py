"""Tests for draft-pick ownership and valuation."""

from __future__ import annotations

import pytest

from src.league_settings import LeagueFormat
from src.pick_valuation import (
    DEFAULT_PICK_VALUES,
    OwnedPick,
    build_pick_assets,
    estimate_slot_tier,
    future_seasons,
    pick_display_name,
    pick_value,
    resolve_pick_ownership,
)

SF = LeagueFormat(num_qbs=2, num_teams=12)
ONE_QB = LeagueFormat(num_qbs=1, num_teams=12)

ROSTERS = [{"roster_id": rid} for rid in (1, 2, 3)]


def _find(picks: list[OwnedPick], season: str, rnd: int, orig: int) -> OwnedPick:
    return next(
        p
        for p in picks
        if p.season == season and p.round == rnd and p.original_roster_id == orig
    )


class TestResolvePickOwnership:
    def test_base_state_own_picks(self) -> None:
        picks = resolve_pick_ownership(ROSTERS, [], ["2027"], rounds=2)
        assert len(picks) == 6  # 3 rosters × 2 rounds
        assert all(p.owner_roster_id == p.original_roster_id for p in picks)

    def test_traded_pick_reassigned(self) -> None:
        traded = [{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2}]
        picks = resolve_pick_ownership(ROSTERS, traded, ["2027"], rounds=2)
        assert _find(picks, "2027", 1, 1).owner_roster_id == 2
        # Round 2 untouched
        assert _find(picks, "2027", 2, 1).owner_roster_id == 1

    def test_retraded_pick_last_entry_wins(self) -> None:
        traded = [
            {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2},
            {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 3},
        ]
        picks = resolve_pick_ownership(ROSTERS, traded, ["2027"], rounds=1)
        assert _find(picks, "2027", 1, 1).owner_roster_id == 3

    def test_unknown_traded_pick_ignored(self) -> None:
        traded = [{"season": "2031", "round": 9, "roster_id": 99, "owner_id": 2}]
        picks = resolve_pick_ownership(ROSTERS, traded, ["2027"], rounds=1)
        assert len(picks) == 3
        assert all(p.owner_roster_id == p.original_roster_id for p in picks)

    def test_future_seasons(self) -> None:
        assert future_seasons("2026") == ["2027", "2028", "2029"]
        assert future_seasons(2026, years=1) == ["2027"]


class TestEstimateSlotTier:
    # rank 1 = strongest of 12
    RANKS = {rid: rid for rid in range(1, 13)}

    def test_weakest_third_drafts_early(self) -> None:
        assert estimate_slot_tier(12, self.RANKS, 12) == "early"
        assert estimate_slot_tier(9, self.RANKS, 12) == "early"

    def test_strongest_third_drafts_late(self) -> None:
        assert estimate_slot_tier(1, self.RANKS, 12) == "late"
        assert estimate_slot_tier(4, self.RANKS, 12) == "late"

    def test_middle_drafts_mid(self) -> None:
        assert estimate_slot_tier(5, self.RANKS, 12) == "mid"
        assert estimate_slot_tier(8, self.RANKS, 12) == "mid"

    def test_unknown_roster_defaults_mid(self) -> None:
        assert estimate_slot_tier(99, self.RANKS, 12) == "mid"


class TestPickValue:
    def test_base_table_lookup(self) -> None:
        assert pick_value(1, "early", ONE_QB) == pytest.approx(90.0)
        assert pick_value(3, "late", ONE_QB) == pytest.approx(8.0)

    def test_superflex_premium(self) -> None:
        assert pick_value(1, "early", SF) == pytest.approx(90.0 * 1.10)

    def test_future_year_discount(self) -> None:
        assert pick_value(1, "mid", ONE_QB, year_offset=2) == pytest.approx(
            78.0 * 0.9**2
        )

    def test_unknown_round_minimal_value(self) -> None:
        assert pick_value(7, "mid", ONE_QB) == pytest.approx(1.0)

    def test_first_rounds_worth_more(self) -> None:
        for tier in ("early", "mid", "late"):
            values = [DEFAULT_PICK_VALUES[(r, tier)] for r in (1, 2, 3, 4)]
            assert values == sorted(values, reverse=True)


class TestBuildPickAssets:
    STRENGTH = {1: 1, 2: 6, 3: 12}  # roster 1 strongest, roster 3 weakest
    TEAMS = {1: "Juggernauts", 2: "Middlers", 3: "Tankers"}

    def test_grouped_by_owner_with_values(self) -> None:
        picks = resolve_pick_ownership(ROSTERS, [], ["2027"], rounds=1)
        assets = build_pick_assets(picks, self.STRENGTH, SF, "2026", self.TEAMS)
        assert set(assets) == {1, 2, 3}
        # Tanker's own 1st projects early and is worth more than the
        # juggernaut's late 1st.
        assert assets[3][0].value > assets[1][0].value

    def test_acquired_pick_priced_by_original_owner(self) -> None:
        traded = [{"season": "2027", "round": 1, "roster_id": 3, "owner_id": 1}]
        picks = resolve_pick_ownership(ROSTERS, traded, ["2027"], rounds=1)
        assets = build_pick_assets(picks, self.STRENGTH, SF, "2026", self.TEAMS)
        owned = {a.display_name: a for a in assets[1]}
        via = next(n for n in owned if "via Tankers" in n)
        own = next(n for n in owned if "via" not in n)
        # The acquired pick (from the weakest team) beats team 1's own late 1st
        assert owned[via].value > owned[own].value
        assert owned[via].value == pytest.approx(90.0 * 1.10)

    def test_pick_asset_fields(self) -> None:
        picks = resolve_pick_ownership([{"roster_id": 1}], [], ["2028"], rounds=2)
        assets = build_pick_assets(picks, {1: 6}, SF, "2026", None)
        a = assets[1][0]
        assert a.is_pick
        assert a.position == "PICK"
        assert a.pick_year == 2028
        assert a.pick_round in (1, 2)
        assert "2028" in a.display_name

    def test_display_name_distinguishes_same_round_picks(self) -> None:
        traded = [{"season": "2027", "round": 1, "roster_id": 2, "owner_id": 1}]
        picks = resolve_pick_ownership(ROSTERS, traded, ["2027"], rounds=1)
        assets = build_pick_assets(picks, self.STRENGTH, SF, "2026", self.TEAMS)
        names = [a.display_name for a in assets[1]]
        assert len(names) == len(set(names)) == 2

    def test_rounds_beyond_max_skipped(self) -> None:
        picks = resolve_pick_ownership([{"roster_id": 1}], [], ["2027"], rounds=6)
        assets = build_pick_assets(picks, {1: 6}, SF, "2026", None, max_round=4)
        assert len(assets[1]) == 4


class TestPickDisplayName:
    def test_own_pick(self) -> None:
        pick = OwnedPick("2027", 2, 1, 1)
        assert pick_display_name(pick) == "2027 2nd Round Pick"

    def test_acquired_pick_shows_origin(self) -> None:
        pick = OwnedPick("2027", 1, 3, 1)
        assert pick_display_name(pick, {3: "Tankers"}) == (
            "2027 1st Round Pick (via Tankers)"
        )

    def test_fourth_round_suffix(self) -> None:
        pick = OwnedPick("2027", 4, 1, 1)
        assert "4th" in pick_display_name(pick)
