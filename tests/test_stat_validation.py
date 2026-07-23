"""Tests for the scrambled-stat reconciliation layer.

No network: nflverse ground truth and SportsDataIO rows are constructed
directly. The headline behaviour — a scrambled realized field is *replaced*
with the nflverse value and the divergence recorded — is the core of the
free-tier validation requirement.
"""

from __future__ import annotations

from src.nfl_stats import PlayerPerformance
from src.sportsdata_provider import SportsDataStat
from src.stat_validation import ValidationReport, reconcile


def _nfl(name, pos, fdid, sleeper=None, **stats):
    return PlayerPerformance(
        gsis_id=f"gsis-{name}",
        sleeper_id=sleeper,
        fantasy_data_id=fdid,
        name=name,
        position=pos,
        team=stats.pop("team", "MIN"),
        season=2024,
        **stats,
    )


def _sdio(pid, name, pos, team="MIN", **stats):
    return SportsDataStat(
        player_id=pid, name=name, team=team, position=pos, season=2024, stats=stats
    )


class TestReconcileCorrection:
    def test_scrambled_field_corrected_to_nflverse(self):
        nfl = [_nfl("Justin Jefferson", "WR", "21685", sleeper="6794",
                    receiving_yards=1533.0, receptions=103.0, fantasy_points_ppr=317.5)]
        sdio = [_sdio("21685", "Justin Jefferson", "WR",
                      ReceivingYards=1333.0, Receptions=103.0, FantasyPointsPPR=290.0)]
        players, report = reconcile(sdio, nfl)
        p = players[0]
        assert p.matched and p.match_method == "id"
        # Corrected to the nflverse truth.
        assert p.stats["ReceivingYards"] == 1533.0
        assert p.stats["FantasyPointsPPR"] == 317.5
        # Receptions matched exactly -> no correction recorded for it.
        fields = {c.field for c in p.corrections}
        assert "ReceivingYards" in fields and "FantasyPointsPPR" in fields
        assert "Receptions" not in fields
        assert report.corrected_fields == 2 and report.players_corrected == 1

    def test_correction_records_pct_off(self):
        nfl = [_nfl("A", "RB", "1", rushing_yards=1000.0)]
        sdio = [_sdio("1", "A", "RB", RushingYards=1200.0)]
        players, _ = reconcile(sdio, nfl)
        corr = next(c for c in players[0].corrections if c.field == "RushingYards")
        assert corr.sportsdata_value == 1200.0 and corr.nfl_value == 1000.0
        assert abs(corr.pct_off - 0.2) < 1e-9

    def test_sleeper_id_carried_from_nflverse(self):
        nfl = [_nfl("A", "WR", "1", sleeper="999", receiving_yards=10.0)]
        players, _ = reconcile([_sdio("1", "A", "WR", ReceivingYards=9.0)], nfl)
        assert players[0].sleeper_id == "999"


class TestReconcileMatching:
    def test_name_fallback_when_no_id_match(self):
        nfl = [_nfl("Christian McCaffrey", "RB", "18877", rushing_yards=1000.0)]
        # SportsDataIO PlayerID does not match fantasy_data_id -> name fallback.
        sdio = [_sdio("99999", "Christian McCaffrey", "RB", RushingYards=1100.0)]
        players, report = reconcile(sdio, nfl)
        assert players[0].matched and players[0].match_method == "name"
        assert report.matched_by_name == 1 and report.matched_by_id == 0

    def test_position_guard_blocks_wrong_name_match(self):
        # Same normalized name, different position -> not a match.
        nfl = [_nfl("Mike Williams", "WR", "1", receiving_yards=800.0)]
        sdio = [_sdio("2", "Mike Williams", "DT", ReceivingYards=5.0)]
        players, report = reconcile(sdio, nfl)
        assert players[0].matched is False
        assert report.unmatched == 1

    def test_unmatched_kept_as_is_and_reported(self):
        nfl = [_nfl("Real Player", "WR", "1", receiving_yards=500.0)]
        sdio = [_sdio("777", "Ghost Player", "WR", ReceivingYards=42.0)]
        players, report = reconcile(sdio, nfl)
        assert players[0].matched is False
        assert players[0].stats["ReceivingYards"] == 42.0  # untouched
        assert report.unmatched_names == ["Ghost Player"]
        assert report.match_rate == 0.0


class TestReport:
    def test_match_rate_and_counts(self):
        nfl = [
            _nfl("A", "WR", "1", receiving_yards=100.0),
            _nfl("B", "RB", "2", rushing_yards=200.0),
        ]
        sdio = [
            _sdio("1", "A", "WR", ReceivingYards=110.0),   # id match + correction
            _sdio("2", "B", "RB", RushingYards=200.0),      # id match, no correction
            _sdio("3", "C", "TE", ReceivingYards=5.0),      # unmatched
        ]
        _, report = reconcile(sdio, nfl)
        assert isinstance(report, ValidationReport)
        assert report.total == 3
        assert report.matched == 2 and report.unmatched == 1
        assert abs(report.match_rate - 2 / 3) < 1e-9
        assert report.players_corrected == 1
