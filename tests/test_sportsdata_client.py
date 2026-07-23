"""Tests for the SportsDataIO client and row parsing (no network)."""

from __future__ import annotations

import pytest

from src.sportsdata_client import SportsDataClient, SportsDataError
from src.sportsdata_provider import (
    parse_player_news,
    parse_season_projections,
    parse_season_stats,
)


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class StubSession:
    """Captures the last request and returns a fixed payload."""

    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.calls: list[str] = []

    def get(self, url, timeout=30):
        self.calls.append(url)
        return StubResponse(self.payload)


def _client(payload):
    client = SportsDataClient("secret-key")
    client.session = StubSession(payload)
    return client


class TestClient:
    def test_requires_api_key(self):
        with pytest.raises(SportsDataError):
            SportsDataClient("")

    def test_key_sent_as_subscription_header(self):
        client = SportsDataClient("secret-key")
        assert client.session.headers["Ocp-Apim-Subscription-Key"] == "secret-key"

    def test_season_stats_endpoint(self):
        client = _client([{"PlayerID": 1}])
        client.get_player_season_stats(2024)
        assert client.session.calls == [
            "https://api.sportsdata.io/v3/nfl/stats/json/PlayerSeasonStats/2024"
        ]

    def test_projection_endpoint(self):
        client = _client([{"PlayerID": 1}])
        client.get_player_season_projections(2024)
        assert client.session.calls[-1].endswith(
            "/v3/nfl/projections/json/PlayerSeasonProjectionStats/2024"
        )

    def test_weekly_endpoint(self):
        client = _client([])
        client.get_player_game_stats_by_week(2024, 5)
        assert client.session.calls[-1].endswith(
            "/v3/nfl/stats/json/PlayerGameStatsByWeek/2024/5"
        )

    def test_news_by_player_endpoint(self):
        client = _client([])
        client.get_player_news_by_id(19801)
        assert client.session.calls[-1].endswith(
            "/v3/nfl/scores/json/NewsByPlayerID/19801"
        )


class TestParsing:
    def test_parse_season_stats(self):
        rows = [
            {
                "PlayerID": 21685,
                "Name": "Justin Jefferson",
                "Team": "MIN",
                "Position": "WR",
                "Season": 2024,
                "Played": 17,
                "ReceivingYards": 1400.0,
                "Receptions": 95.0,
                "FantasyPointsPPR": 290.0,
            }
        ]
        parsed = parse_season_stats(rows)
        assert len(parsed) == 1
        s = parsed[0]
        assert s.player_id == "21685"  # coerced to string for joining
        assert s.stats["ReceivingYards"] == 1400.0
        assert s.stats["FantasyPointsPPR"] == 290.0

    def test_parse_skips_rows_without_player_id(self):
        assert parse_season_stats([{"Name": "No ID"}]) == []

    def test_parse_projections(self):
        rows = [{"PlayerID": 1, "Name": "A", "FantasyPointsPPR": 250.0}]
        proj = parse_season_projections(rows)
        assert proj[0].projections["FantasyPointsPPR"] == 250.0

    def test_parse_news_newest_first_with_source_fallback(self):
        rows = [
            {
                "Title": "Older item",
                "Content": "…",
                "Source": "Rotoworld",
                "Url": "http://a",
                "Updated": "2026-07-20T10:00:00",
            },
            {
                # No Source/Url — fall back to the Original* fields.
                "Title": "Newer item",
                "Content": "…",
                "OriginalSource": "Beat writer",
                "OriginalSourceUrl": "http://b",
                "Updated": "2026-07-22T10:00:00",
            },
        ]
        news = parse_player_news(rows)
        assert [n.title for n in news] == ["Newer item", "Older item"]  # newest first
        assert news[0].source == "Beat writer"
        assert news[0].url == "http://b"

    def test_parse_news_empty(self):
        assert parse_player_news([]) == []
