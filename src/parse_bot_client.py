"""Parse.bot API client for extracting dynasty fantasy football player values.

Parse.bot is an AI-powered web-extraction service.  Given a URL and a
natural-language description of what to extract it returns structured JSON.
This module uses it to pull player values from trusted dynasty sources such as
KeepTradeCut (KTC) and FantasyCalc.

API key configuration
---------------------
Set the ``PARSE_BOT_API_KEY`` environment variable, or (in a Streamlit
deployment) add it to ``.streamlit/secrets.toml``:

    [parse_bot]
    api_key = "your-key-here"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests

PARSE_BOT_BASE_URL = "https://api.parse.bot"

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCES: dict[str, dict] = {
    "ktc": {
        "url": "https://keeptradecut.com/dynasty-rankings?format=2",
        "instructions": (
            "Extract the dynasty superflex player rankings table.  "
            "For each player return: name (full name as shown), position "
            "(one of QB/RB/WR/TE), team (NFL team abbreviation), age (years, "
            "numeric), and value (the KTC dynasty value numeric score)."
        ),
        "schema": {
            "players": {
                "type": "array",
                "items": {
                    "properties": {
                        "name": {"type": "string"},
                        "position": {"type": "string"},
                        "team": {"type": "string"},
                        "age": {"type": "number"},
                        "value": {"type": "number"},
                    },
                    "required": ["name", "value"],
                },
            }
        },
    },
    "fantasycalc": {
        "url": "https://fantasycalc.com/rankings/dynasty/superflex/ppr",
        "instructions": (
            "Extract the dynasty superflex PPR player rankings table.  "
            "For each player return: name (full name), position "
            "(QB/RB/WR/TE), team (NFL team abbreviation), age (numeric), "
            "and value (FantasyCalc dynasty value or score, numeric)."
        ),
        "schema": {
            "players": {
                "type": "array",
                "items": {
                    "properties": {
                        "name": {"type": "string"},
                        "position": {"type": "string"},
                        "team": {"type": "string"},
                        "age": {"type": "number"},
                        "value": {"type": "number"},
                    },
                    "required": ["name", "value"],
                },
            }
        },
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PlayerValue:
    """Player value as returned by a single source."""

    name: str
    position: str
    team: str
    value: float
    age: Optional[float] = None
    source: str = ""

    # ------------------------------------------------------------------

    def normalized_value(self, max_value: float) -> float:
        """Scale this value to the 0-100 range relative to *max_value*."""
        if max_value <= 0:
            return 0.0
        return (self.value / max_value) * 100.0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ParseBotClient:
    """Client for the Parse.bot extraction API."""

    def __init__(
        self, api_key: str, base_url: str = PARSE_BOT_BASE_URL
    ) -> None:
        if not api_key:
            raise ValueError("A Parse.bot API key is required.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Low-level extraction
    # ------------------------------------------------------------------

    def extract(
        self,
        url: str,
        instructions: str,
        schema: Optional[dict] = None,
    ) -> dict:
        """
        Extract structured data from *url* using Parse.bot.

        Args:
            url: The page to scrape.
            instructions: Natural-language description of what to extract.
            schema: Optional JSON Schema describing the expected output shape.

        Returns:
            Parsed JSON dict as returned by Parse.bot.

        Raises:
            requests.HTTPError: If the API returns a non-2xx status.
        """
        payload: dict = {"url": url, "instructions": instructions}
        if schema is not None:
            payload["schema"] = schema

        response = self.session.post(
            f"{self.base_url}/extract",
            json=payload,
            timeout=90,
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Dynasty-specific helpers
    # ------------------------------------------------------------------

    def get_player_values(self, source: str) -> list[PlayerValue]:
        """
        Fetch and parse player values from a named source.

        Args:
            source: One of the keys in :data:`SOURCES` (e.g. ``"ktc"``).

        Returns:
            List of :class:`PlayerValue` objects.

        Raises:
            ValueError: If *source* is not recognised.
        """
        if source not in SOURCES:
            raise ValueError(
                f"Unknown source '{source}'. Available: {sorted(SOURCES)}"
            )
        cfg = SOURCES[source]
        result = self.extract(
            url=cfg["url"],
            instructions=cfg["instructions"],
            schema=cfg.get("schema"),
        )
        raw_players: list[dict] = result.get("players", [])
        players = []
        for p in raw_players:
            name = p.get("name", "").strip()
            raw_val = p.get("value")
            if not name or raw_val is None:
                continue
            try:
                value = float(raw_val)
            except (TypeError, ValueError):
                continue
            players.append(
                PlayerValue(
                    name=name,
                    position=p.get("position", ""),
                    team=p.get("team", ""),
                    age=_to_float(p.get("age")),
                    value=value,
                    source=source,
                )
            )
        return players

    def get_all_player_values(self) -> dict[str, list[PlayerValue]]:
        """Return player values from every configured source."""
        return {src: self.get_player_values(src) for src in SOURCES}

    # ------------------------------------------------------------------
    # Consensus helpers
    # ------------------------------------------------------------------

    def get_consensus_values(
        self, values_by_source: dict[str, list[PlayerValue]]
    ) -> dict[str, float]:
        """
        Compute per-player consensus values (0–100 scale) by normalising
        each source independently, then averaging across sources.

        Args:
            values_by_source: ``{source_name: [PlayerValue, …]}``.

        Returns:
            ``{normalised_player_name: consensus_value}`` where names are
            lower-cased and stripped via :func:`normalize_name`.
        """
        bucket: dict[str, list[float]] = {}
        for players in values_by_source.values():
            if not players:
                continue
            max_val = max(p.value for p in players)
            for player in players:
                key = normalize_name(player.name)
                bucket.setdefault(key, []).append(
                    player.normalized_value(max_val)
                )
        return {
            name: sum(vals) / len(vals) for name, vals in bucket.items()
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """
    Normalise a player name for cross-source matching.

    Converts to lower case, strips common suffixes (Jr., Sr., II, III, IV),
    removes punctuation, and collapses whitespace.
    """
    name = name.strip().lower()
    # Remove common generational suffixes
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name)
    # Remove non-alphanumeric characters (except spaces)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _to_float(value: object) -> Optional[float]:
    """Convert *value* to float, returning ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
