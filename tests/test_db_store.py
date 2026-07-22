"""Tests for the SQLModel-backed persistence store.

Uses a fresh file-backed SQLite engine per test (tmp_path) — no network.
The headline behaviour: cached data is served without re-calling the
fetcher, which is how the DB reduces API calls.
"""

from __future__ import annotations

import pytest
from sqlmodel import create_engine

from src.data_providers import NormalizedPlayerValue
from src.db.models import UserPreferences
from src.db.store import DtfStore, apply_weights_to_prefs, weights_from_prefs
from src.value_engine import PlayerValuation, ValueWeights


@pytest.fixture()
def store(tmp_path) -> DtfStore:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dtf.db'}",
        connect_args={"check_same_thread": False},
    )
    return DtfStore(engine)


class _Counter:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


# ---------------------------------------------------------------------------
# Cache-until-manual-refresh — the API-call reduction proof
# ---------------------------------------------------------------------------


class TestSleeperCaches:
    def test_user_cached_after_first_fetch(self, store: DtfStore) -> None:
        fetch = _Counter({"user_id": "123", "display_name": "Percules"})
        first = store.get_or_fetch_user("Percules", fetch)
        second = store.get_or_fetch_user("Percules", fetch)
        assert first == second
        assert fetch.calls == 1  # second call served from the DB

    def test_force_refetches(self, store: DtfStore) -> None:
        fetch = _Counter({"user_id": "123"})
        store.get_or_fetch_user("Percules", fetch)
        store.get_or_fetch_user("Percules", fetch, force=True)
        assert fetch.calls == 2

    def test_user_not_found_not_cached(self, store: DtfStore) -> None:
        fetch = _Counter(None)
        assert store.get_or_fetch_user("ghost", fetch) is None
        assert store.get_or_fetch_user("ghost", fetch) is None
        assert fetch.calls == 2  # None is never cached

    def test_league_data_round_trip(self, store: DtfStore) -> None:
        payload = {
            "league": {"name": "Anklebreaker78", "season": "2025"},
            "rosters": [{"roster_id": 1, "players": ["6786"]}],
            "users": [{"user_id": "123"}],
        }
        fetch = _Counter(payload)
        store.get_or_fetch_league_data("L1", fetch)
        got = store.get_or_fetch_league_data("L1", fetch)
        assert got == payload
        assert fetch.calls == 1

    def test_nfl_players_singleton(self, store: DtfStore) -> None:
        fetch = _Counter({"6786": {"full_name": "Justin Jefferson"}})
        store.get_or_fetch_nfl_players(fetch)
        got = store.get_or_fetch_nfl_players(fetch)
        assert got["6786"]["full_name"] == "Justin Jefferson"
        assert fetch.calls == 1


# ---------------------------------------------------------------------------
# Per-source value tables
# ---------------------------------------------------------------------------


def _npv(name, source, value, **kw):
    return NormalizedPlayerValue(
        name=name, position="WR", source=source, value=value, raw_value=value * 100, **kw
    )


class TestSourceValues:
    def test_round_trip_across_three_tables(self, store: DtfStore) -> None:
        payload = (
            {
                "fantasycalc": [_npv("Justin Jefferson", "fantasycalc", 100.0, sleeper_id="6786")],
                "draftsharks": [_npv("Bijan Robinson", "draftsharks", 90.0)],
            },
            {"ktc": "returned no players"},
        )
        fetch = _Counter(payload)
        s1, e1 = store.get_or_fetch_source_values("fmtA", fetch)
        s2, e2 = store.get_or_fetch_source_values("fmtA", fetch)
        assert fetch.calls == 1  # cached
        assert set(s2) == {"fantasycalc", "draftsharks"}
        assert s2["fantasycalc"][0].name == "Justin Jefferson"
        assert s2["fantasycalc"][0].sleeper_id == "6786"
        assert isinstance(s2["fantasycalc"][0], NormalizedPlayerValue)
        assert e2 == {"ktc": "returned no players"}

    def test_force_replaces_rows(self, store: DtfStore) -> None:
        fetch = _Counter(({"fantasycalc": [_npv("A", "fantasycalc", 50.0)]}, {}))
        store.get_or_fetch_source_values("fmtA", fetch)
        store.get_or_fetch_source_values("fmtA", fetch, force=True)
        assert fetch.calls == 2
        # No duplicate rows after a forced refetch
        s, _ = store.get_or_fetch_source_values("fmtA", fetch)
        assert len(s["fantasycalc"]) == 1

    def test_empty_format_not_refetched(self, store: DtfStore) -> None:
        # All sources errored — a SourceFetch row still records the attempt,
        # so we don't refetch forever.
        fetch = _Counter(({}, {"fantasycalc": "HTTP 502", "ktc": "unavailable"}))
        store.get_or_fetch_source_values("fmtEmpty", fetch)
        sources, errors = store.get_or_fetch_source_values("fmtEmpty", fetch)
        assert fetch.calls == 1
        assert sources == {}
        assert errors["fantasycalc"] == "HTTP 502"

    def test_distinct_formats_isolated(self, store: DtfStore) -> None:
        fetch_a = _Counter(({"fantasycalc": [_npv("A", "fantasycalc", 50.0)]}, {}))
        fetch_b = _Counter(({"fantasycalc": [_npv("B", "fantasycalc", 60.0)]}, {}))
        store.get_or_fetch_source_values("fmtA", fetch_a)
        store.get_or_fetch_source_values("fmtB", fetch_b)
        sa, _ = store.get_or_fetch_source_values("fmtA", fetch_a)
        sb, _ = store.get_or_fetch_source_values("fmtB", fetch_b)
        assert sa["fantasycalc"][0].name == "A"
        assert sb["fantasycalc"][0].name == "B"


class TestTradeHistory:
    def test_cached(self, store: DtfStore) -> None:
        fetch = _Counter([("2026-06-01", 9000.0), ("2026-07-01", 9500.0)])
        p1 = store.get_or_fetch_trade_history("401", "fmtA", fetch)
        p2 = store.get_or_fetch_trade_history("401", "fmtA", fetch)
        assert fetch.calls == 1
        assert p2 == [("2026-06-01", 9000.0), ("2026-07-01", 9500.0)]
        assert all(isinstance(pt, tuple) for pt in p2)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class TestPreferences:
    def test_defaults_when_absent(self, store: DtfStore) -> None:
        prefs = store.load_preferences("Percules")
        assert prefs.username == "Percules"
        assert prefs.age_weight == 0.5
        assert prefs.fairness_weight == 0.5

    def test_round_trip_with_weights_tuple(self, store: DtfStore) -> None:
        prefs = UserPreferences(username="Percules")
        weights = ValueWeights(
            source_weights=(("fantasycalc", 0.7), ("ktc", 0.2), ("draftsharks", 0.1)),
            age_weight=0.8,
            trend_weight=0.3,
        )
        apply_weights_to_prefs(prefs, weights)
        prefs.fairness_weight = 0.25
        prefs.mutual_only = False
        store.save_preferences(prefs)

        loaded = store.load_preferences("Percules")
        assert loaded.fairness_weight == 0.25
        assert loaded.mutual_only is False
        restored = weights_from_prefs(loaded)
        assert restored.source_weights == (
            ("fantasycalc", 0.7),
            ("ktc", 0.2),
            ("draftsharks", 0.1),
        )
        assert restored.age_weight == 0.8
        assert restored.trend_weight == 0.3

    def test_weights_from_empty_prefs_uses_defaults(self, store: DtfStore) -> None:
        weights = weights_from_prefs(UserPreferences(username="x"))
        assert weights.source_weights_dict()["fantasycalc"] == 0.5

    def test_save_is_idempotent_upsert(self, store: DtfStore) -> None:
        p = UserPreferences(username="Percules", age_weight=0.1)
        store.save_preferences(p)
        p2 = UserPreferences(username="Percules", age_weight=0.9)
        store.save_preferences(p2)
        assert store.load_preferences("Percules").age_weight == 0.9


# ---------------------------------------------------------------------------
# Snapshot parity with the ported ValueStore
# ---------------------------------------------------------------------------


def _valuation(key, adjusted):
    return PlayerValuation(
        key=key,
        name=key,
        position="WR",
        base_value=adjusted,
        adjusted_value=adjusted,
        values_by_source={"fantasycalc": adjusted},
    )


class TestSnapshots:
    def test_save_and_history(self, store: DtfStore) -> None:
        from datetime import date

        store.save_snapshot({"p1": _valuation("p1", 88.0)}, date.today())
        assert store.has_snapshot(date.today())
        history = store.get_history("p1")
        assert history == [(date.today().isoformat(), 88.0)]

    def test_deltas(self, store: DtfStore) -> None:
        from datetime import date, timedelta

        today = date.today()
        store.save_snapshot({"p": _valuation("p", 50.0)}, today - timedelta(days=31))
        store.save_snapshot({"p": _valuation("p", 60.0)}, today)
        assert store.get_deltas(days=30)["p"] == pytest.approx(10.0)
