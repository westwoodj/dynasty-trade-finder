"""Registry of SportsDataIO scrambled fields and their nflverse counterparts.

On the SportsDataIO free tier, essentially every numeric stat is scrambled
±5-20% (per the published data dictionary — in the ``PlayerSeason`` table every
yardage/reception/touchdown/fantasy-point field is flagged ``Scrambled=True``,
while identity fields like ``PlayerID``/``Name``/``Team``/``Position`` are not).

This module is the **single source of truth** for the reconciliation layer:

* :data:`REALIZED_FIELD_MAP` maps each scrambled *realized-stat* field
  (SportsDataIO CamelCase) to its :class:`~src.nfl_stats.PlayerPerformance`
  attribute, so a scrambled value can be replaced with the confirmed nflverse
  value.
* :data:`PROJECTION_FIELDS` are the projection fields we surface — also
  scrambled, but forward-looking, so they have **no** nflverse ground truth and
  are always flagged *approximate* rather than corrected.

If nflverse column names drift, fixing them here fixes the whole pipeline.
"""

from __future__ import annotations

# SportsDataIO realized-stat field (CamelCase) -> PlayerPerformance attribute.
# Every key is a scrambled field with a confirmed nflverse counterpart.
REALIZED_FIELD_MAP: dict[str, str] = {
    "PassingAttempts": "attempts",
    "PassingCompletions": "completions",
    "PassingYards": "passing_yards",
    "PassingTouchdowns": "passing_tds",
    "PassingInterceptions": "passing_interceptions",
    "RushingAttempts": "carries",
    "RushingYards": "rushing_yards",
    "RushingTouchdowns": "rushing_tds",
    "Receptions": "receptions",
    "ReceivingTargets": "targets",
    "ReceivingYards": "receiving_yards",
    "ReceivingTouchdowns": "receiving_tds",
    "FantasyPoints": "fantasy_points",
    "FantasyPointsPPR": "fantasy_points_ppr",
}

# The scrambled realized-stat fields we validate/correct (SportsDataIO names).
SCRAMBLED_REALIZED_FIELDS: frozenset[str] = frozenset(REALIZED_FIELD_MAP)

# Projection fields we surface from SportsDataIO. All are scrambled and cannot
# be validated against nflverse (they describe the future), so they are shown
# as approximate.
PROJECTION_FIELDS: tuple[str, ...] = (
    "FantasyPoints",
    "FantasyPointsPPR",
    "PassingYards",
    "PassingTouchdowns",
    "RushingYards",
    "RushingTouchdowns",
    "Receptions",
    "ReceivingYards",
    "ReceivingTouchdowns",
)
