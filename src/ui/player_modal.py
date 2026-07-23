"""Player-detail pop-up modal (``st.dialog``).

Opened by clicking a player's row in any of the ranked tables.  Consolidates,
in one overlay, what the app already knows about a player: cross-source value
comparison, nflverse realized production, value-history trends, recent news, and
who rosters them in the league.

The dialog receives a fully-assembled :class:`~src.player_detail.PlayerDetail`
plus lazy fetcher callables for the network/DB-backed pieces (weekly stats,
value history, news), so it stays decoupled from ``app.py``'s data layer.
"""

from __future__ import annotations

from typing import Callable

import polars as pl
import streamlit as st

from ..player_detail import PlayerDetail
from ..sportsdata_provider import NewsItem
from .components import format_component_badges

# Position-aware stat lines shown on the Performance tab: (label, attribute).
_STAT_LINES: dict[str, list[tuple[str, str]]] = {
    "QB": [
        ("Games", "games"), ("Pass Yds", "passing_yards"), ("Pass TD", "passing_tds"),
        ("INT", "passing_interceptions"), ("Rush Yds", "rushing_yards"),
        ("Rush TD", "rushing_tds"), ("FP (PPR)", "fantasy_points_ppr"),
    ],
    "RB": [
        ("Games", "games"), ("Carries", "carries"), ("Rush Yds", "rushing_yards"),
        ("Rush TD", "rushing_tds"), ("Rec", "receptions"), ("Rec Yds", "receiving_yards"),
        ("FP (PPR)", "fantasy_points_ppr"),
    ],
    "WR": [
        ("Games", "games"), ("Targets", "targets"), ("Rec", "receptions"),
        ("Rec Yds", "receiving_yards"), ("Rec TD", "receiving_tds"),
        ("FP (PPR)", "fantasy_points_ppr"),
    ],
    "TE": [
        ("Games", "games"), ("Targets", "targets"), ("Rec", "receptions"),
        ("Rec Yds", "receiving_yards"), ("Rec TD", "receiving_tds"),
        ("FP (PPR)", "fantasy_points_ppr"),
    ],
}


def open_player_detail_dialog(
    detail: PlayerDetail,
    *,
    fetch_weekly: Callable[[int], list[dict]],
    fetch_local_history: Callable[[str], list[tuple[str, float]]],
    fetch_market_history: Callable[[str], list[tuple[str, float]]],
    fetch_news: Callable[[str], list[NewsItem]],
) -> None:
    """Open the player-detail modal for *detail* (defines + invokes the dialog)."""
    title = f"{detail.name} · {detail.position}" if detail.position else detail.name

    @st.dialog(title, width="large")
    def _dialog() -> None:
        _render_body(
            detail,
            fetch_weekly=fetch_weekly,
            fetch_local_history=fetch_local_history,
            fetch_market_history=fetch_market_history,
            fetch_news=fetch_news,
        )

    _dialog()


def _render_body(
    detail: PlayerDetail,
    *,
    fetch_weekly: Callable[[int], list[dict]],
    fetch_local_history: Callable[[str], list[tuple[str, float]]],
    fetch_market_history: Callable[[str], list[tuple[str, float]]],
    fetch_news: Callable[[str], list[NewsItem]],
) -> None:
    subtitle = " · ".join(
        part
        for part in (
            detail.team or None,
            f"Age {detail.age:.0f}" if detail.age else None,
        )
        if part
    )
    if subtitle:
        st.caption(subtitle)

    c1, c2, c3 = st.columns(3)
    c1.metric("Adjusted value", f"{detail.adjusted_value:.1f}")
    delta = detail.adjusted_value - detail.base_value
    c2.metric("Base value", f"{detail.base_value:.1f}", f"{delta:+.1f}")
    if detail.consensus:
        c3.metric("Consensus", f"{detail.consensus:.1f}")

    tab_value, tab_perf, tab_trends, tab_news = st.tabs(
        ["Value", "Performance", "Trends", "News"]
    )
    with tab_value:
        _render_value(detail)
    with tab_perf:
        _render_performance(detail, fetch_weekly)
    with tab_trends:
        _render_trends(detail, fetch_local_history, fetch_market_history)
    with tab_news:
        _render_news(detail, fetch_news)

    st.markdown("---")
    if detail.rostered:
        timeline = f" ({detail.owner_timeline})" if detail.owner_timeline else ""
        st.markdown(f"🏈 Rostered by **{detail.owner_team}**{timeline}")
    else:
        st.markdown("🆓 Not rostered in this league (free agent).")


def _render_value(detail: PlayerDetail) -> None:
    if not detail.values_by_source:
        st.info("No market value sources for this player.")
        return
    st.markdown("**Value by source** (0–100, per source)")
    df = pl.DataFrame(
        {
            "source": list(detail.values_by_source.keys()),
            "value": [round(v, 1) for v in detail.values_by_source.values()],
        }
    )
    st.bar_chart(df, x="source", y="value")

    if detail.high_source and detail.low_source:
        st.caption(
            f"Spread **{detail.spread_pct * 100:.0f}%** — highest on "
            f"**{detail.high_source}**, lowest on **{detail.low_source}**. "
            "A wide spread is an arbitrage signal (buy from the low source, "
            "sell to the high one)."
        )

    badges = format_component_badges(detail.components)
    if badges:
        st.markdown(f"**Adjustments:** {badges}")
    extras = []
    if detail.injury_risk:
        extras.append(f"Injury risk: {detail.injury_risk}")
    if detail.adp is not None:
        extras.append(f"ADP: {detail.adp:.1f}")
    if extras:
        st.caption(" · ".join(extras))


def _render_performance(
    detail: PlayerDetail, fetch_weekly: Callable[[int], list[dict]]
) -> None:
    perf = detail.perf
    if perf is None:
        st.info("No nflverse performance data matched for this player.")
        return

    st.caption(f"Realized {perf.season} regular season (nflverse, unscrambled)")
    lines = _STAT_LINES.get(detail.position.upper(), _STAT_LINES["WR"])
    cols = st.columns(len(lines))
    for col, (label, attr) in zip(cols, lines):
        val = getattr(perf, attr, 0.0) or 0.0
        col.metric(label, f"{val:.1f}" if attr.startswith("fantasy") else f"{val:.0f}")

    with st.spinner("Loading weekly production…"):
        weekly = fetch_weekly(perf.season)
    _render_weekly_chart(weekly, perf)


def _render_weekly_chart(weekly: list[dict], perf) -> None:
    if not weekly:
        return
    df = pl.DataFrame(weekly)
    if "player_id" in df.columns and perf.gsis_id:
        sub = df.filter(pl.col("player_id") == perf.gsis_id)
    elif "player_display_name" in df.columns:
        sub = df.filter(pl.col("player_display_name") == perf.name)
    else:
        return
    if sub.is_empty() or "week" not in sub.columns or "fantasy_points_ppr" not in sub.columns:
        return
    st.markdown("**Weekly fantasy points (PPR)**")
    st.line_chart(sub.sort("week"), x="week", y="fantasy_points_ppr")


def _render_trends(
    detail: PlayerDetail,
    fetch_local_history: Callable[[str], list[tuple[str, float]]],
    fetch_market_history: Callable[[str], list[tuple[str, float]]],
) -> None:
    if detail.trend_frac is not None:
        st.metric("30-day trend", f"{detail.trend_frac * 100:+.1f}%")

    frames = []
    local = fetch_local_history(detail.value_key) if detail.value_key else []
    if local:
        frames.append(
            pl.DataFrame(local, schema=["date", "Local snapshots"], orient="row")
        )
    if detail.fc_player_id:
        try:
            market = fetch_market_history(detail.fc_player_id)
        except Exception as exc:  # network/auth issues shouldn't break the modal
            market = []
            st.caption(f"FantasyCalc history unavailable: {exc}")
        if market:
            df = pl.DataFrame(
                market, schema=["date", "FantasyCalc value"], orient="row"
            )
            peak = df["FantasyCalc value"].max()
            if peak and peak > 0:  # rescale raw series to the 0–100 display scale
                df = df.with_columns(
                    (pl.col("FantasyCalc value") / peak * 100.0).alias(
                        "FantasyCalc value"
                    )
                )
            frames.append(df)

    if frames:
        # Polars has no index: outer-join the series on the shared "date" column
        # (they can cover different date ranges) and drive the x-axis explicitly.
        chart = frames[0]
        for extra in frames[1:]:
            chart = chart.join(extra, on="date", how="full", coalesce=True)
        st.line_chart(chart.sort("date"), x="date")
    else:
        st.info(
            "No value history yet — local snapshots build up daily, and "
            "FantasyCalc history needs a working Parse API key."
        )


def _render_news(
    detail: PlayerDetail, fetch_news: Callable[[str], list[NewsItem]]
) -> None:
    if not detail.fantasy_data_id:
        st.info(
            "No SportsDataIO player id for this player, so news can't be looked up."
        )
        return
    with st.spinner("Loading news…"):
        items = fetch_news(detail.fantasy_data_id)
    if not items:
        st.info(
            "No recent news — the SportsDataIO news endpoint returned nothing "
            "(it may not be included on your plan). Add/upgrade a key in `.env`."
        )
        return
    for item in items[:10]:
        st.markdown(f"**{item.title}**")
        meta = " · ".join(p for p in (item.source, item.updated) if p)
        if meta:
            st.caption(meta)
        if item.content:
            st.write(item.content)
        if item.url:
            st.markdown(f"[Read more]({item.url})")
        st.markdown("---")
