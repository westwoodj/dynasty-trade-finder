"""Tests for the data provider layer.

No network: providers receive stub clients whose collection accessors
return canned rows shaped like the generated parse_apis resources.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from parse_sdk import PaginationLimitError

from src.data_providers import (
    DraftSharksProvider,
    FantasyCalcProvider,
    KTCProvider,
    ProviderUnavailableError,
    _as_float,
    _as_int,
    fetch_all_sources,
)
from src.league_settings import LeagueFormat

FMT = LeagueFormat()


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubPaginator:
    def __init__(self, items=None, error=None):
        self._items = items or []
        self._error = error

    def list(self):
        if self._error is not None:
            raise self._error
        return self._items


class StubCollection:
    """Records call kwargs and returns a fixed paginator."""

    def __init__(self, paginator: StubPaginator):
        self._paginator = paginator
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self._paginator

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self._paginator


def fc_ranking(
    name="Justin Jefferson",
    value=10000,
    sleeper_id="6786",
    position="WR",
    team="MIN",
    age=26.5,
    fc_id=401,
    rank=1,
    trend=250,
):
    return SimpleNamespace(
        value=value,
        overall_rank=rank,
        trend_30_day=trend,
        player=SimpleNamespace(
            id=fc_id,
            name=name,
            position=position,
            team=team,
            age=age,
            sleeper_id=sleeper_id,
        ),
    )


def ds_player(
    name="Justin Jefferson",
    ds_value="9500",
    position="WR",
    team="MIN",
    adp="3.2",
    injury_risk="Low",
    ds_projection="285.4",
    rank="1",
    player_id="123",
):
    return SimpleNamespace(
        id=player_id,
        name=name,
        ds_value=ds_value,
        position=position,
        team=team,
        adp=adp,
        injury_risk=injury_risk,
        ds_projection=ds_projection,
        rank=rank,
    )


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class TestCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (5, 5.0),
            ("5", 5.0),
            ("5.5", 5.5),
            ("1,234", 1234.0),
            (" 12 ", 12.0),
            ("negotiable", None),
            ("", None),
            (None, None),
            (True, None),
        ],
    )
    def test_as_float(self, raw, expected) -> None:
        assert _as_float(raw) == expected

    def test_as_int(self) -> None:
        assert _as_int("7") == 7
        assert _as_int("7.9") == 7
        assert _as_int("junk") is None


# ---------------------------------------------------------------------------
# FantasyCalcProvider
# ---------------------------------------------------------------------------


class TestFantasyCalcProvider:
    def _provider(self, items=None, error=None):
        rankings = StubCollection(StubPaginator(items, error))
        client = SimpleNamespace(rankings=rankings)
        return FantasyCalcProvider(client=client), rankings

    def test_values_normalized_to_100(self) -> None:
        provider, _ = self._provider(
            [fc_ranking(value=10000), fc_ranking(name="Bijan Robinson", value=5000)]
        )
        rows = provider.fetch_values(FMT)
        assert rows[0].value == pytest.approx(100.0)
        assert rows[1].value == pytest.approx(50.0)
        assert rows[0].raw_value == pytest.approx(10000.0)

    def test_sleeper_id_and_fc_id_passthrough(self) -> None:
        provider, _ = self._provider([fc_ranking(sleeper_id="6786", fc_id=401)])
        row = provider.fetch_values(FMT)[0]
        assert row.sleeper_id == "6786"
        assert row.source_player_id == "401"

    def test_missing_sleeper_id_is_none(self) -> None:
        provider, _ = self._provider([fc_ranking(sleeper_id=None)])
        assert provider.fetch_values(FMT)[0].sleeper_id is None

    def test_rows_without_name_or_value_skipped(self) -> None:
        provider, _ = self._provider(
            [
                fc_ranking(),
                fc_ranking(name=""),
                fc_ranking(name="No Value", value=None),
            ]
        )
        rows = provider.fetch_values(FMT)
        assert len(rows) == 1

    def test_string_value_cast(self) -> None:
        provider, _ = self._provider([fc_ranking(value="8000")])
        assert provider.fetch_values(FMT)[0].raw_value == pytest.approx(8000.0)

    def test_league_format_params_passed(self) -> None:
        provider, rankings = self._provider([fc_ranking()])
        provider.fetch_values(LeagueFormat(num_qbs=1, ppr=0.5, num_teams=10))
        call = rankings.calls[0]
        assert call["num_qbs"] == "1"
        assert call["ppr"] == "0.5"
        assert call["num_teams"] == "10"
        assert call["limit"] == 500

    def test_partial_items_used_on_pagination_limit(self) -> None:
        err = PaginationLimitError(
            "stopped",
            pages_fetched=1,
            items_yielded=1,
            partial_items=[fc_ranking()],
        )
        provider, _ = self._provider(error=err)
        rows = provider.fetch_values(FMT)
        assert len(rows) == 1

    def test_pagination_limit_without_partials_raises(self) -> None:
        err = PaginationLimitError("stopped", partial_items=None)
        provider, _ = self._provider(error=err)
        with pytest.raises(PaginationLimitError):
            provider.fetch_values(FMT)

    def test_trade_history(self) -> None:
        history = SimpleNamespace(
            historical_values=[
                SimpleNamespace(date="2026-06-01", value=9000),
                SimpleNamespace(date="2026-07-01", value="9500"),
                SimpleNamespace(date="", value=1),  # dropped
            ]
        )

        class StubHistories:
            def get(self, player_id, **kwargs):
                self.player_id = player_id
                return history

        client = SimpleNamespace(trade_histories=StubHistories())
        provider = FantasyCalcProvider(client=client)
        points = provider.fetch_trade_history("401", FMT)
        assert points == [("2026-06-01", 9000.0), ("2026-07-01", 9500.0)]
        assert client.trade_histories.player_id == "401"


# ---------------------------------------------------------------------------
# DraftSharksProvider
# ---------------------------------------------------------------------------


class TestDraftSharksProvider:
    def _provider(self, items=None, error=None):
        players = StubCollection(StubPaginator(items, error))
        client = SimpleNamespace(players=players)
        return DraftSharksProvider(client=client), players

    def test_all_string_fields_cast(self) -> None:
        provider, _ = self._provider([ds_player()])
        row = provider.fetch_values(FMT)[0]
        assert row.raw_value == pytest.approx(9500.0)
        assert row.adp == pytest.approx(3.2)
        assert row.projection == pytest.approx(285.4)
        assert row.overall_rank == 1
        assert row.injury_risk == "Low"

    def test_junk_value_rows_skipped_not_fatal(self) -> None:
        provider, _ = self._provider(
            [ds_player(), ds_player(name="Bad Row", ds_value="negotiable")]
        )
        rows = provider.fetch_values(FMT)
        assert len(rows) == 1

    def test_values_normalized_to_100(self) -> None:
        provider, _ = self._provider(
            [ds_player(ds_value="8000"), ds_player(name="Other Guy", ds_value="4000")]
        )
        rows = provider.fetch_values(FMT)
        assert rows[0].value == pytest.approx(100.0)
        assert rows[1].value == pytest.approx(50.0)

    def test_no_sleeper_id(self) -> None:
        provider, _ = self._provider([ds_player()])
        assert provider.fetch_values(FMT)[0].sleeper_id is None

    def test_rankings_depth_and_format_params(self) -> None:
        provider, players = self._provider([ds_player()])
        provider.fetch_values(LeagueFormat(num_qbs=2, ppr=1.0))
        call = players.calls[0]
        assert call["depth"] == "rankings"
        assert call["league_type"] == "superflex"
        assert call["scoring"] == "ppr"
        assert call["is_dynasty"] == "true"


# ---------------------------------------------------------------------------
# KTCProvider
# ---------------------------------------------------------------------------


def ktc_player(
    name="Justin Jefferson",
    value=9999,
    position="WR",
    team="MIN",
    age=26.5,
    slug="justin-jefferson-123",
    rank=1,
    trend=42,
):
    return SimpleNamespace(
        playerName=name,
        value=value,
        position=position,
        team=team,
        age=age,
        slug=slug,
        rank=rank,
        overallTrend=trend,
    )


class _FakeStrEnum:
    """Mimics the generated str-enum whose str() is 'Position.WR'."""

    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return f"Position.{self.value}"


class TestKTCProvider:
    def _provider(self, items=None, error=None):
        players = StubCollection(StubPaginator(items, error))
        client = SimpleNamespace(players=players)
        return KTCProvider(client=client), players

    def test_player_name_field_and_normalization(self) -> None:
        provider, _ = self._provider(
            [ktc_player(value=9999), ktc_player(name="Breece Hall", value=4999.5)]
        )
        rows = provider.fetch_values(FMT)
        assert rows[0].name == "Justin Jefferson"
        assert rows[0].value == pytest.approx(100.0)
        assert rows[1].value == pytest.approx(50.0, abs=0.1)

    def test_scoring_param_follows_league_format(self) -> None:
        provider, players = self._provider([ktc_player()])
        provider.fetch_values(LeagueFormat(num_qbs=2))
        assert players.calls[0]["scoring"] == "superflex"
        provider.fetch_values(LeagueFormat(num_qbs=1))
        assert players.calls[1]["scoring"] == "oneqb"

    def test_enum_position_unwrapped(self) -> None:
        provider, _ = self._provider([ktc_player(position=_FakeStrEnum("WR"))])
        assert provider.fetch_values(FMT)[0].position == "WR"

    def test_slug_trend_and_rank_carried(self) -> None:
        provider, _ = self._provider([ktc_player(slug="jj-123", rank=3, trend=-55)])
        row = provider.fetch_values(FMT)[0]
        assert row.source_player_id == "jj-123"
        assert row.overall_rank == 3
        assert row.trend_30_day == pytest.approx(-55.0)

    def test_rows_without_name_skipped(self) -> None:
        provider, _ = self._provider([ktc_player(), ktc_player(name="")])
        assert len(provider.fetch_values(FMT)) == 1


# ---------------------------------------------------------------------------
# fetch_all_sources
# ---------------------------------------------------------------------------


class _GoodProvider:
    SOURCE = "good"

    def fetch_values(self, fmt):
        return [
            __import__("src.data_providers", fromlist=["NormalizedPlayerValue"])
            .NormalizedPlayerValue(
                name="A Player", position="WR", source="good", value=100.0, raw_value=1.0
            )
        ]


class _FailingProvider:
    SOURCE = "bad"

    def fetch_values(self, fmt):
        raise RuntimeError("upstream exploded")


class _EmptyProvider:
    SOURCE = "empty"

    def fetch_values(self, fmt):
        return []


class TestFetchAllSources:
    def test_one_failure_does_not_sink_the_rest(self) -> None:
        results, errors = fetch_all_sources(
            FMT, providers=[_GoodProvider(), _FailingProvider()]
        )
        assert "good" in results
        assert len(results["good"]) == 1
        assert "upstream exploded" in errors["bad"]

    def test_empty_source_reported(self) -> None:
        results, errors = fetch_all_sources(FMT, providers=[_EmptyProvider()])
        assert results == {}
        assert "empty" in errors
