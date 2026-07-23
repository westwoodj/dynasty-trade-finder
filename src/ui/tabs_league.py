"""League Overview tab: roster leaderboard with timeline profiling."""

from __future__ import annotations

import polars as pl
import streamlit as st

from ..insights import TeamProfile
from ..trade_calculator import TradeAsset

_TIMELINE_BADGES = {
    "contender": "🏆 Contender",
    "rebuilder": "🌱 Rebuilder",
    "balanced": "⚖️ Balanced",
}


def render_league_overview(
    league: dict,
    profiles: dict[int, TeamProfile],
    roster_assets: dict[int, list[TradeAsset]],
) -> None:
    st.header("🏟️ League Overview")
    st.markdown(
        f"**League:** {league.get('name')} &nbsp;|&nbsp; "
        f"**Season:** {league.get('season')} &nbsp;|&nbsp; "
        f"**Teams:** {league.get('total_rosters')}"
    )

    rows = []
    for rid, profile in profiles.items():
        assets = roster_assets.get(rid, [])
        pos_counts: dict[str, int] = {}
        for a in assets:
            pos_counts[a.position] = pos_counts.get(a.position, 0) + 1
        rows.append(
            {
                "Team": profile.team_name,
                "Timeline": _TIMELINE_BADGES.get(
                    profile.classification, profile.classification
                ),
                "W": profile.wins,
                "L": profile.losses,
                "Total Value": round(profile.total_value, 1),
                "Win-Now": round(profile.win_now_value, 1),
                "Future": round(profile.future_value, 1),
                "Picks": round(profile.pick_value, 1),
                "QB": pos_counts.get("QB", 0),
                "RB": pos_counts.get("RB", 0),
                "WR": pos_counts.get("WR", 0),
                "TE": pos_counts.get("TE", 0),
            }
        )

    if not rows:
        st.info("No rosters found.")
        return

    df = pl.DataFrame(rows).sort("Total Value", descending=True)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Competitive timelines")
    st.caption(
        "Win-now capital (age 27+) vs future capital (age ≤ 24 + picks). "
        "Contenders should be trading with rebuilders — vets flow one way, "
        "youth and picks the other."
    )
    scatter_df = pl.DataFrame(
        {
            "Team": [p.team_name for p in profiles.values()],
            "Win-Now Value": [p.win_now_value for p in profiles.values()],
            "Future Value": [p.future_value for p in profiles.values()],
            "Timeline": [p.classification for p in profiles.values()],
        }
    )
    st.scatter_chart(
        scatter_df, x="Win-Now Value", y="Future Value", color="Timeline"
    )
