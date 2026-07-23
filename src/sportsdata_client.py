"""SportsDataIO NFL API client.

A thin ``requests`` client over the SportsDataIO v3 NFL API.  Authentication is
via the ``Ocp-Apim-Subscription-Key`` header (never the ``?key=`` query param,
so the secret stays out of URLs and logs).

On the **free tier** most numeric stat fields are scrambled ±5-20%; callers
validate them against nflverse data (see :mod:`src.stat_validation`).
"""

from __future__ import annotations

import requests

SPORTSDATA_BASE_URL = "https://api.sportsdata.io"


class SportsDataError(RuntimeError):
    """A SportsDataIO request failed (network, auth, or HTTP error)."""


class SportsDataClient:
    """Thin client for the SportsDataIO v3 NFL API (JSON format)."""

    def __init__(self, api_key: str, base_url: str = SPORTSDATA_BASE_URL) -> None:
        if not api_key:
            raise SportsDataError("A SportsDataIO API key is required.")
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": api_key,
            }
        )

    def _get(self, path: str) -> list | dict:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Stats (realized) — scrambled on the free tier
    # ------------------------------------------------------------------

    def get_player_season_stats(self, season: str | int) -> list[dict]:
        """Season-aggregate stats for every player in *season* (e.g. "2024")."""
        return self._get(f"/v3/nfl/stats/json/PlayerSeasonStats/{season}")

    def get_player_game_stats_by_week(
        self, season: str | int, week: int
    ) -> list[dict]:
        """Per-game stats for every player in *season* / *week*."""
        return self._get(f"/v3/nfl/stats/json/PlayerGameStatsByWeek/{season}/{week}")

    # ------------------------------------------------------------------
    # Projections (forward-looking) — scrambled and not nflverse-validatable
    # ------------------------------------------------------------------

    def get_player_season_projections(self, season: str | int) -> list[dict]:
        """Season projection stats for every player in *season*."""
        return self._get(
            f"/v3/nfl/projections/json/PlayerSeasonProjectionStats/{season}"
        )

    # ------------------------------------------------------------------
    # News — free text, not a scrambled numeric stat (no validation needed)
    # ------------------------------------------------------------------

    def get_player_news_by_id(self, player_id: str | int) -> list[dict]:
        """Recent news items for the player with SportsDataIO ``PlayerID``.

        The ``PlayerID`` equals nflverse's ``fantasy_data_id``.  This endpoint
        may not be included on the free tier — callers should degrade
        gracefully on :class:`requests.HTTPError`.
        """
        return self._get(f"/v3/nfl/scores/json/NewsByPlayerID/{player_id}")

    # ------------------------------------------------------------------
    # Reference
    # ------------------------------------------------------------------

    def get_players(self) -> list[dict]:
        """Active player metadata (IDs, team, position, …)."""
        return self._get("/v3/nfl/scores/json/Players")
