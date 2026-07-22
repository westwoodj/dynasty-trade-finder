"""Player-name normalization and cross-source matching.

Sources spell players differently ("Marquise Brown" vs "Hollywood Brown",
"Kenneth Walker III" vs "Ken Walker").  Matching is attempted in order of
confidence: exact normalized match → known alias → conservative fuzzy match.

Prefer joining on ``sleeper_id`` when a source provides it (FantasyCalc
does); name matching is the fallback for sources that don't.
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Iterable, Optional


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


# Known cross-source mismatches that normalization alone can't bridge.
# Both keys and values are in normalized form; matching checks both
# directions.  Seed small — grow as real mismatches surface.
ALIAS_MAP: dict[str, str] = {
    "marquise brown": "hollywood brown",
    "gabriel davis": "gabe davis",
    "joshua palmer": "josh palmer",
    "kenneth walker": "ken walker",
    "cameron ward": "cam ward",
    "chigoziem okonkwo": "chig okonkwo",
    "nathaniel dell": "tank dell",
}


def match_name(name: str, candidates: Iterable[str]) -> Optional[str]:
    """Match a raw player name against *candidates* (normalized names).

    Returns the matching candidate, or ``None`` when no confident match
    exists.  The fuzzy cutoff is deliberately conservative (0.85) — a wrong
    match silently misprices a player, which is worse than no match.
    """
    candidate_set = set(candidates)
    norm = normalize_name(name)
    if norm in candidate_set:
        return norm

    alias = ALIAS_MAP.get(norm)
    if alias and alias in candidate_set:
        return alias
    for key, value in ALIAS_MAP.items():
        if value == norm and key in candidate_set:
            return key

    matches = get_close_matches(norm, candidate_set, n=1, cutoff=0.85)
    return matches[0] if matches else None
