"""Performance tab: validated realized stats, projections, and data quality.

Realized stats come from **nflverse** (nflreadpy) — the confirmed, unscrambled
source.  When a SportsDataIO key is configured, its free-tier stats (scrambled
±5-20%) are reconciled against nflverse and a data-quality panel reports how
much was corrected.  Projections come from SportsDataIO and are **approximate**
(scrambled, with no nflverse ground truth to validate against).
"""

from __future__ import annotations

from typing import Callable, Optional

import polars as pl
import streamlit as st

from .. import nfl_stats
from ..nfl_stats import PlayerPerformance
from ..sportsdata_provider import SportsDataProjection, SportsDataStat
from ..stat_validation import ValidationReport, reconcile

POSITION_FILTER = ["All", "QB", "RB", "WR", "TE"]
SEASON_LOOKBACK = 10  # years of history offered in the season dropdown
EARLIEST_SEASON = 1999  # nflverse's earliest available season


def _season_options(default_season: int) -> list[int]:
    upper = max(default_season, nfl_stats.current_season())
    lower = max(upper - SEASON_LOOKBACK + 1, EARLIEST_SEASON)
    return list(range(upper, lower - 1, -1))


def render_performance(
    fetch_perf: Callable[[int], list[PlayerPerformance]],
    fetch_weekly: Callable[[int], list[dict]],
    fetch_sdio_stats: Callable[[int], list[SportsDataStat]],
    fetch_projections: Callable[[int], list[SportsDataProjection]],
    default_season: int,
    staleness: Optional[Callable[[int], str]] = None,
) -> None:
    st.header("📊 Player Performance")
    st.markdown(
        "Realized production from **nflverse** (confirmed, unscrambled). "
        "SportsDataIO free-tier stats are scrambled ±5-20% and corrected "
        "against nflverse below; its projections are shown separately and "
        "flagged **approximate**."
    )

    seasons = _season_options(default_season)
    default_index = seasons.index(default_season) if default_season in seasons else 0
    season = st.selectbox("Season", seasons, index=default_index, key="perf_season")

    with st.spinner(f"Loading {season} performance data…"):
        try:
            perf = fetch_perf(season)
        except Exception as err:  # nflverse download failure — don't crash the app
            # nflverse publishes a season's stats file only once games are played;
            # until then the download 404s (and the prior-season fallback in
            # nfl_stats only triggers on an *empty* frame, not a raise). Degrade
            # gracefully, mirroring the main-flow guard in app.py.
            perf = []
            st.caption(f"nflverse fetch failed: {err}")

    if not perf:
        st.info(
            f"No nflverse performance data available yet for {season} "
            "(games may not have been played). Try a previous season."
        )
        return

    # fetch_perf can silently fall back to the prior season (e.g. before
    # Week 1 of a new season); tell the user which season is actually shown.
    actual_season = perf[0].season
    if actual_season != season:
        st.caption(f"No data for {season} yet — showing {actual_season}.")

    if staleness is not None:
        cap = staleness(actual_season)
        if cap:
            st.caption(f"Performance {cap}")

    with st.spinner("Loading weekly & projection data…"):
        weekly = fetch_weekly(actual_season)
        sdio_stats = fetch_sdio_stats(actual_season)
        report = reconcile(sdio_stats, perf)[1] if sdio_stats else None
        projections = fetch_projections(actual_season)

    if report is not None:
        _render_quality_panel(report)

    tab_season, tab_weekly, tab_proj = st.tabs(
        ["Season stats", "Weekly", "Projections (approx.)"]
    )
    with tab_season:
        _render_season_table(perf)
    with tab_weekly:
        _render_weekly(weekly)
    with tab_proj:
        _render_projections(projections)


def _render_quality_panel(report: ValidationReport) -> None:
    with st.expander("🩺 Data quality — SportsDataIO vs nflverse", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Match rate", f"{report.match_rate * 100:.0f}%")
        c2.metric("Matched", f"{report.matched}/{report.total}")
        c3.metric("Fields corrected", report.corrected_fields)
        c4.metric("Players corrected", report.players_corrected)
        st.caption(
            f"Matched by ID: {report.matched_by_id} · by name: "
            f"{report.matched_by_name} · unmatched: {report.unmatched}. "
            "Scrambled realized stats were replaced with nflverse values."
        )
        if report.unmatched_names:
            sample = ", ".join(sorted(set(report.unmatched_names))[:15])
            st.caption(f"Unmatched (kept as-is, unvalidated): {sample}")


def _total_tds(p: PlayerPerformance) -> float:
    return p.passing_tds + p.rushing_tds + p.receiving_tds


def _render_season_table(perf: list[PlayerPerformance]) -> None:
    pos = st.radio("Position", POSITION_FILTER, horizontal=True, key="perf_pos_filter")
    rows = []
    for p in sorted(perf, key=lambda x: x.fantasy_points_ppr, reverse=True):
        if pos != "All" and p.position.upper() != pos:
            continue
        rows.append(
            {
                "Player": p.name,
                "Pos": p.position,
                "Team": p.team,
                "G": round(p.games),
                "Tgt": round(p.targets),
                "Rec": round(p.receptions),
                "RecYds": round(p.receiving_yards),
                "RushYds": round(p.rushing_yards),
                "PassYds": round(p.passing_yards),
                "TD": round(_total_tds(p)),
                "FP (PPR)": round(p.fantasy_points_ppr, 1),
                "PPG (PPR)": round(p.fantasy_points_ppr_per_game, 1),
            }
        )
    if not rows:
        st.info("No players at this position.")
        return
    st.dataframe(pl.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_weekly(weekly: list[dict]) -> None:
    if not weekly:
        st.info("No weekly data available.")
        return
    df = pl.DataFrame(weekly)
    name_col = "player_display_name"
    if name_col not in df.columns or "week" not in df.columns:
        st.info("Weekly data is unavailable in the expected shape.")
        return
    # Rank players by total PPR to seed the selector with relevant names.
    totals = (
        df.group_by(name_col)
        .agg(pl.col("fantasy_points_ppr").sum())
        .sort("fantasy_points_ppr", descending=True)
    )
    names = totals[name_col].to_list()
    chosen = st.selectbox("Player", names, key="perf_weekly_player")
    sub = df.filter(pl.col(name_col) == chosen).sort("week")
    # Polars has no index, so drive the chart x-axis explicitly.
    st.line_chart(sub, x="week", y="fantasy_points_ppr")
    show_cols = [
        c
        for c in ["week", "fantasy_points_ppr", "targets", "receptions",
                  "receiving_yards", "rushing_yards", "passing_yards"]
        if c in sub.columns
    ]
    st.dataframe(sub.select(show_cols), use_container_width=True, hide_index=True)


def _render_projections(projections: list[SportsDataProjection]) -> None:
    if not projections:
        st.info(
            "No projections loaded. Add a SportsDataIO API key (sidebar / .env) "
            "to pull season projections."
        )
        return
    st.warning(
        "⚠️ Projections are from SportsDataIO's free tier — **scrambled ±5-20%** "
        "and **not validated** (there is no nflverse ground truth for the "
        "future). Treat them as approximate."
    )
    rows = []
    for p in sorted(
        projections, key=lambda x: x.projections.get("FantasyPointsPPR", 0.0), reverse=True
    ):
        pr = p.projections
        rows.append(
            {
                "Player": p.name,
                "Pos": p.position,
                "Team": p.team,
                "Proj FP (PPR)": round(pr.get("FantasyPointsPPR", 0.0), 1),
                "Proj PassYds": round(pr.get("PassingYards", 0.0)),
                "Proj RushYds": round(pr.get("RushingYards", 0.0)),
                "Proj RecYds": round(pr.get("ReceivingYards", 0.0)),
                "Proj Rec": round(pr.get("Receptions", 0.0)),
            }
        )
    st.dataframe(pl.DataFrame(rows), use_container_width=True, hide_index=True)
