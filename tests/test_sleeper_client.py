"""Tests for the Sleeper API client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.sleeper_client import SleeperClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> SleeperClient:
    return SleeperClient(base_url="https://api.sleeper.app/v1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(data: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Tests — get_user
# ---------------------------------------------------------------------------


class TestGetUser:
    def test_returns_user_dict(self, client: SleeperClient) -> None:
        user_payload = {"user_id": "12345", "username": "johndoe", "display_name": "JohnDoe"}
        with patch.object(client.session, "get", return_value=_mock_response(user_payload)):
            result = client.get_user("johndoe")
        assert result["username"] == "johndoe"
        assert result["user_id"] == "12345"

    def test_calls_correct_url(self, client: SleeperClient) -> None:
        with patch.object(client.session, "get", return_value=_mock_response({})) as mock_get:
            client.get_user("testuser")
        mock_get.assert_called_once_with(
            "https://api.sleeper.app/v1/user/testuser", timeout=30
        )


# ---------------------------------------------------------------------------
# Tests — get_user_leagues
# ---------------------------------------------------------------------------


class TestGetUserLeagues:
    def test_returns_list(self, client: SleeperClient) -> None:
        leagues = [{"league_id": "abc", "name": "My League"}]
        with patch.object(client.session, "get", return_value=_mock_response(leagues)):
            result = client.get_user_leagues("12345", "2024")
        assert isinstance(result, list)
        assert result[0]["league_id"] == "abc"

    def test_calls_correct_url(self, client: SleeperClient) -> None:
        with patch.object(client.session, "get", return_value=_mock_response([])) as mock_get:
            client.get_user_leagues("999", "2025", sport="nfl")
        mock_get.assert_called_once_with(
            "https://api.sleeper.app/v1/user/999/leagues/nfl/2025", timeout=30
        )


# ---------------------------------------------------------------------------
# Tests — get_league
# ---------------------------------------------------------------------------


class TestGetLeague:
    def test_returns_league_dict(self, client: SleeperClient) -> None:
        league_payload = {"league_id": "123", "name": "Dynasty League", "total_rosters": 12}
        with patch.object(client.session, "get", return_value=_mock_response(league_payload)):
            result = client.get_league("123")
        assert result["total_rosters"] == 12

    def test_calls_correct_url(self, client: SleeperClient) -> None:
        with patch.object(client.session, "get", return_value=_mock_response({})) as mock_get:
            client.get_league("456")
        mock_get.assert_called_once_with(
            "https://api.sleeper.app/v1/league/456", timeout=30
        )


# ---------------------------------------------------------------------------
# Tests — get_league_rosters
# ---------------------------------------------------------------------------


class TestGetLeagueRosters:
    def test_returns_list_of_rosters(self, client: SleeperClient) -> None:
        rosters = [
            {"roster_id": 1, "owner_id": "aaa", "players": ["1234", "5678"]},
            {"roster_id": 2, "owner_id": "bbb", "players": ["9012"]},
        ]
        with patch.object(client.session, "get", return_value=_mock_response(rosters)):
            result = client.get_league_rosters("league123")
        assert len(result) == 2
        assert result[0]["roster_id"] == 1

    def test_calls_correct_url(self, client: SleeperClient) -> None:
        with patch.object(client.session, "get", return_value=_mock_response([])) as mock_get:
            client.get_league_rosters("myLeague")
        mock_get.assert_called_once_with(
            "https://api.sleeper.app/v1/league/myLeague/rosters", timeout=30
        )


# ---------------------------------------------------------------------------
# Tests — get_nfl_state
# ---------------------------------------------------------------------------


class TestGetNflState:
    def test_returns_state_dict(self, client: SleeperClient) -> None:
        state = {"season": "2024", "week": 5, "season_type": "regular"}
        with patch.object(client.session, "get", return_value=_mock_response(state)):
            result = client.get_nfl_state()
        assert result["season"] == "2024"
        assert result["week"] == 5

    def test_calls_correct_url(self, client: SleeperClient) -> None:
        with patch.object(client.session, "get", return_value=_mock_response({})) as mock_get:
            client.get_nfl_state()
        mock_get.assert_called_once_with(
            "https://api.sleeper.app/v1/state/nfl", timeout=30
        )


# ---------------------------------------------------------------------------
# Tests — HTTP error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_http_error_propagates(self, client: SleeperClient) -> None:
        import requests

        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = requests.HTTPError("404")
        with patch.object(client.session, "get", return_value=err_resp):
            with pytest.raises(requests.HTTPError):
                client.get_user("nonexistent")
