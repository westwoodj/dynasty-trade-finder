"""Shared UI data-building helpers (pure functions, unit-tested)."""

from __future__ import annotations

from typing import Optional

from ..name_matching import match_name, normalize_name
from ..trade_calculator import TradeAsset
from ..value_engine import PlayerValuation

POSITIONS = ["QB", "RB", "WR", "TE"]

POSITION_COLORS: dict[str, str] = {
    "QB": "#FF6B6B",
    "RB": "#4ECDC4",
    "WR": "#45B7D1",
    "TE": "#96CEB4",
}

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}


def build_player_lookup(nfl_players: dict) -> dict[str, dict]:
    """Return {player_id: {name, position, team, age}} for skill players."""
    return {
        pid: {
            "name": p.get("full_name", ""),
            "position": p.get("position", ""),
            "team": p.get("team", "") or "FA",
            "age": p.get("age"),
        }
        for pid, p in nfl_players.items()
        if p.get("position") in SKILL_POSITIONS and p.get("full_name")
    }


def build_user_lookup(users: list[dict]) -> dict[str, dict]:
    """Return {user_id: {display_name, team_name}}."""
    return {
        str(u["user_id"]): {
            "display_name": u.get("display_name", "Unknown"),
            "team_name": u.get("metadata", {}).get("team_name")
            or u.get("display_name", "Unknown"),
        }
        for u in users
    }


def team_names_by_roster(
    rosters: list[dict], user_lookup: dict[str, dict]
) -> dict[int, str]:
    """Return {roster_id: team display name}."""
    names: dict[int, str] = {}
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None:
            continue
        user = user_lookup.get(str(roster.get("owner_id", "")), {})
        names[rid] = user.get("team_name") or f"Team {rid}"
    return names


def build_name_map(
    valuations: dict[str, PlayerValuation]
) -> dict[str, PlayerValuation]:
    """{normalized_name: valuation} for name-based fallback joins."""
    name_map: dict[str, PlayerValuation] = {}
    for v in valuations.values():
        name_map.setdefault(normalize_name(v.name), v)
    return name_map


def roster_valuations(
    roster: dict,
    player_lookup: dict[str, dict],
    sleeper_map: dict[str, PlayerValuation],
    name_map: dict[str, PlayerValuation],
) -> list[tuple[TradeAsset, Optional[PlayerValuation]]]:
    """Convert a Sleeper roster to ``[(TradeAsset, valuation-or-None), …]``.

    Join order: sleeper_id (exact) → name match (alias/fuzzy, with a
    position guard).  Unmatched players get value 0 and a ``None``
    valuation so the caller can count and surface them.
    """
    pairs: list[tuple[TradeAsset, Optional[PlayerValuation]]] = []
    for pid in roster.get("players") or []:
        player = player_lookup.get(pid)
        if not player or not player["name"]:
            continue
        valuation = sleeper_map.get(str(pid))
        if valuation is None:
            matched = match_name(player["name"], name_map.keys())
            if matched:
                candidate = name_map[matched]
                if not candidate.position or candidate.position == player["position"]:
                    valuation = candidate
        pairs.append(
            (
                TradeAsset(
                    name=player["name"],
                    position=player["position"],
                    team=player["team"],
                    age=player.get("age"),
                    value=valuation.adjusted_value if valuation else 0.0,
                ),
                valuation,
            )
        )
    return pairs


def roster_to_assets(
    roster: dict,
    player_lookup: dict[str, dict],
    sleeper_map: dict[str, PlayerValuation],
    name_map: dict[str, PlayerValuation],
) -> tuple[list[TradeAsset], list[str]]:
    """Convert a Sleeper roster to assets; also return unmatched names."""
    pairs = roster_valuations(roster, player_lookup, sleeper_map, name_map)
    assets = [asset for asset, _ in pairs]
    unmatched = [asset.name for asset, valuation in pairs if valuation is None]
    return assets, unmatched


def format_components(valuation: Optional[PlayerValuation]) -> str:
    """Human-readable adjustment badges, e.g. ``age −8% · trend +3%``."""
    if valuation is None:
        return ""
    parts = []
    for label, frac in valuation.components.items():
        if abs(frac) >= 0.005:
            sign = "+" if frac > 0 else "−"
            parts.append(f"{label} {sign}{abs(frac) * 100:.0f}%")
    return " · ".join(parts)


def nearest_player(
    value: float,
    valuations: dict[str, PlayerValuation],
    tolerance: float = 0.25,
) -> Optional[PlayerValuation]:
    """The player whose adjusted value is closest to *value* (within
    ``tolerance`` as a fraction), for pick ≈ player equivalence notes."""
    best: Optional[PlayerValuation] = None
    best_gap = float("inf")
    for v in valuations.values():
        gap = abs(v.adjusted_value - value)
        if gap < best_gap:
            best, best_gap = v, gap
    if best is None or value <= 0 or best_gap / max(value, 1.0) > tolerance:
        return None
    return best
