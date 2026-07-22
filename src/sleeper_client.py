"""Sleeper fantasy football API client.

Sleeper is a free fantasy football platform with a public REST API that
requires no authentication for read-only operations.  Full documentation:
https://docs.sleeper.com/
"""

from __future__ import annotations

import requests

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"


class SleeperClient:
    """Thin client for the Sleeper public REST API."""

    def __init__(self, base_url: str = SLEEPER_BASE_URL) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict | list:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # User endpoints
    # ------------------------------------------------------------------

    def get_user(self, username: str) -> dict:
        """Return Sleeper user data for *username* (or a numeric user ID)."""
        return self._get(f"/user/{username}")

    def get_user_leagues(
        self, user_id: str, season: str, sport: str = "nfl"
    ) -> list[dict]:
        """Return all leagues a user is in for the given *season*."""
        return self._get(f"/user/{user_id}/leagues/{sport}/{season}")

    # ------------------------------------------------------------------
    # League endpoints
    # ------------------------------------------------------------------

    def get_league(self, league_id: str) -> dict:
        """Return metadata for *league_id*."""
        return self._get(f"/league/{league_id}")

    def get_league_rosters(self, league_id: str) -> list[dict]:
        """Return all rosters in the league, including player_ids and picks."""
        return self._get(f"/league/{league_id}/rosters")

    def get_league_users(self, league_id: str) -> list[dict]:
        """Return all users (managers) in the league."""
        return self._get(f"/league/{league_id}/users")

    def get_league_transactions(self, league_id: str, week: int) -> list[dict]:
        """Return all transactions (trades, waivers, FA) for the given week."""
        return self._get(f"/league/{league_id}/transactions/{week}")

    def get_league_traded_picks(self, league_id: str) -> list[dict]:
        """Return all traded future draft picks in the league."""
        return self._get(f"/league/{league_id}/traded_picks")

    def get_league_drafts(self, league_id: str) -> list[dict]:
        """Return all drafts associated with the league."""
        return self._get(f"/league/{league_id}/drafts")

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        """Return matchup data for *week*."""
        return self._get(f"/league/{league_id}/matchups/{week}")

    # ------------------------------------------------------------------
    # NFL state / player endpoints
    # ------------------------------------------------------------------

    def get_nfl_state(self) -> dict:
        """Return current NFL state (season, week, season_type, …)."""
        return self._get("/state/nfl")

    def get_nfl_players(self) -> dict:
        """
        Return all NFL players keyed by player_id.

        This is a large response (~5 MB).  Cache the result in your
        application rather than calling it repeatedly.
        """
        return self._get("/players/nfl")
