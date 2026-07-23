"""Tests for the pure UI data-building helpers."""

from __future__ import annotations

import pytest

from src.ui.components import (
    build_name_map,
    build_player_lookup,
    build_user_lookup,
    format_components,
    nearest_player,
    roster_to_assets,
    roster_valuations,
    team_names_by_roster,
)
from src.value_engine import PlayerValuation


def valuation(
    name: str,
    value: float = 50.0,
    position: str = "WR",
    sleeper_id: str | None = None,
    components: dict | None = None,
) -> PlayerValuation:
    return PlayerValuation(
        key=sleeper_id or f"name:{name.lower()}",
        name=name,
        position=position,
        sleeper_id=sleeper_id,
        base_value=value,
        adjusted_value=value,
        components=components or {},
    )


NFL_PLAYERS = {
    "6786": {"full_name": "Justin Jefferson", "position": "WR", "team": "MIN", "age": 27},
    "4046": {"full_name": "Patrick Mahomes", "position": "QB", "team": "KC", "age": 30},
    "9999": {"full_name": "Some Kicker", "position": "K", "team": "DAL", "age": 28},
    "1111": {"full_name": "", "position": "WR", "team": "FA", "age": 24},
}


class TestLookups:
    def test_player_lookup_skill_positions_only(self) -> None:
        lookup = build_player_lookup(NFL_PLAYERS)
        assert set(lookup) == {"6786", "4046"}
        assert lookup["6786"]["name"] == "Justin Jefferson"

    def test_user_lookup_prefers_team_name(self) -> None:
        users = [
            {"user_id": 1, "display_name": "jack", "metadata": {"team_name": "Dynasty Dogs"}},
            {"user_id": 2, "display_name": "jill", "metadata": {}},
        ]
        lookup = build_user_lookup(users)
        assert lookup["1"]["team_name"] == "Dynasty Dogs"
        assert lookup["2"]["team_name"] == "jill"

    def test_team_names_by_roster(self) -> None:
        rosters = [
            {"roster_id": 1, "owner_id": 10},
            {"roster_id": 2, "owner_id": None},
        ]
        user_lookup = {"10": {"team_name": "Dogs", "display_name": "jack"}}
        names = team_names_by_roster(rosters, user_lookup)
        assert names == {1: "Dogs", 2: "Team 2"}


class TestRosterConversion:
    ROSTER = {"players": ["6786", "4046", "9999"]}
    LOOKUP = build_player_lookup(NFL_PLAYERS)

    def test_sleeper_id_join_wins(self) -> None:
        jj = valuation("J. Jefferson", 99.0, sleeper_id="6786")
        sleeper_map = {"6786": jj}
        assets, unmatched = roster_to_assets(self.ROSTER, self.LOOKUP, sleeper_map, {})
        by_name = {a.name: a for a in assets}
        # Sleeper display name kept; value from the sleeper_id-joined valuation
        assert by_name["Justin Jefferson"].value == pytest.approx(99.0)
        assert "Justin Jefferson" not in unmatched

    def test_name_fallback_when_no_sleeper_id(self) -> None:
        pm = valuation("Patrick Mahomes", 88.0, position="QB")
        name_map = build_name_map({"k": pm})
        assets, unmatched = roster_to_assets(self.ROSTER, self.LOOKUP, {}, name_map)
        by_name = {a.name: a for a in assets}
        assert by_name["Patrick Mahomes"].value == pytest.approx(88.0)

    def test_position_guard_blocks_wrong_player(self) -> None:
        # A same-named LB in the value pool must not price the QB
        impostor = valuation("Patrick Mahomes", 5.0, position="LB")
        name_map = build_name_map({"k": impostor})
        assets, unmatched = roster_to_assets(self.ROSTER, self.LOOKUP, {}, name_map)
        by_name = {a.name: a for a in assets}
        assert by_name["Patrick Mahomes"].value == 0.0
        assert "Patrick Mahomes" in unmatched

    def test_unmatched_player_zero_value(self) -> None:
        assets, unmatched = roster_to_assets(self.ROSTER, self.LOOKUP, {}, {})
        assert all(a.value == 0.0 for a in assets)
        assert set(unmatched) == {"Justin Jefferson", "Patrick Mahomes"}

    def test_non_skill_players_skipped(self) -> None:
        assets, _ = roster_to_assets(self.ROSTER, self.LOOKUP, {}, {})
        assert {a.name for a in assets} == {"Justin Jefferson", "Patrick Mahomes"}

    def test_roster_valuations_pairs(self) -> None:
        jj = valuation("Justin Jefferson", 99.0, sleeper_id="6786")
        pairs = roster_valuations(
            self.ROSTER, self.LOOKUP, {"6786": jj}, {}
        )
        matched = {a.name: v for a, v in pairs}
        assert matched["Justin Jefferson"] is jj
        assert matched["Patrick Mahomes"] is None


class TestFormatting:
    def test_format_components(self) -> None:
        v = valuation(
            "Someone", components={"age": -0.08, "trend": 0.03, "injury": 0.0}
        )
        badges = format_components(v)
        assert "age −8%" in badges
        assert "trend +3%" in badges
        assert "injury" not in badges

    def test_format_components_none(self) -> None:
        assert format_components(None) == ""

    def test_nearest_player(self) -> None:
        pool = {
            "a": valuation("Close Match", 52.0),
            "b": valuation("Far Match", 90.0),
        }
        assert nearest_player(50.0, pool).name == "Close Match"

    def test_nearest_player_none_outside_tolerance(self) -> None:
        pool = {"b": valuation("Far Match", 90.0)}
        assert nearest_player(20.0, pool) is None
