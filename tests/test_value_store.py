"""Tests for the SQLite value-history store."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.value_engine import PlayerValuation
from src.value_store import ValueStore


def valuation(key: str, adjusted: float, name: str = "", base: float = 0.0) -> PlayerValuation:
    return PlayerValuation(
        key=key,
        name=name or key,
        position="WR",
        base_value=base or adjusted,
        adjusted_value=adjusted,
        values_by_source={"fantasycalc": adjusted},
    )


@pytest.fixture()
def store(tmp_path) -> ValueStore:
    return ValueStore(db_path=tmp_path / "test.db")


class TestSnapshots:
    def test_round_trip(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 88.0)}, today)
        history = store.get_history("p1")
        assert history == [(today.isoformat(), 88.0)]

    def test_has_snapshot(self, store: ValueStore) -> None:
        today = date.today()
        assert not store.has_snapshot(today)
        store.save_snapshot({"p1": valuation("p1", 50.0)}, today)
        assert store.has_snapshot(today)
        assert not store.has_snapshot(today - timedelta(days=1))

    def test_same_day_save_is_idempotent(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 50.0)}, today)
        store.save_snapshot({"p1": valuation("p1", 55.0)}, today)
        history = store.get_history("p1")
        assert history == [(today.isoformat(), 55.0)]  # replaced, not duplicated

    def test_save_returns_row_count(self, store: ValueStore) -> None:
        n = store.save_snapshot(
            {"p1": valuation("p1", 1.0), "p2": valuation("p2", 2.0)}, date.today()
        )
        assert n == 2

    def test_snapshot_dates_sorted(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 2.0)}, today)
        store.save_snapshot({"p1": valuation("p1", 1.0)}, today - timedelta(days=5))
        assert store.snapshot_dates() == [
            (today - timedelta(days=5)).isoformat(),
            today.isoformat(),
        ]


class TestHistory:
    def test_history_window(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 1.0)}, today - timedelta(days=400))
        store.save_snapshot({"p1": valuation("p1", 2.0)}, today - timedelta(days=10))
        history = store.get_history("p1", days=30)
        assert len(history) == 1
        assert history[0][1] == 2.0

    def test_empty_history(self, store: ValueStore) -> None:
        assert store.get_history("nobody") == []


class TestDeltas:
    def test_delta_math(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot(
            {"up": valuation("up", 50.0), "down": valuation("down", 80.0)},
            today - timedelta(days=31),
        )
        store.save_snapshot(
            {"up": valuation("up", 60.0), "down": valuation("down", 70.0)}, today
        )
        deltas = store.get_deltas(days=30)
        assert deltas["up"] == pytest.approx(10.0)
        assert deltas["down"] == pytest.approx(-10.0)

    def test_short_history_falls_back_to_oldest(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 50.0)}, today - timedelta(days=3))
        store.save_snapshot({"p1": valuation("p1", 58.0)}, today)
        deltas = store.get_deltas(days=30)
        assert deltas["p1"] == pytest.approx(8.0)

    def test_single_snapshot_no_deltas(self, store: ValueStore) -> None:
        store.save_snapshot({"p1": valuation("p1", 50.0)}, date.today())
        assert store.get_deltas() == {}

    def test_player_missing_from_baseline_skipped(self, store: ValueStore) -> None:
        today = date.today()
        store.save_snapshot({"p1": valuation("p1", 50.0)}, today - timedelta(days=31))
        store.save_snapshot(
            {"p1": valuation("p1", 51.0), "rookie": valuation("rookie", 30.0)}, today
        )
        deltas = store.get_deltas(days=30)
        assert "rookie" not in deltas
        assert deltas["p1"] == pytest.approx(1.0)
