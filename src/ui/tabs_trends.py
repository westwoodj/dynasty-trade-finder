"""Trends tab: market movers, buy-low/sell-high flags, and per-player
value history charts."""

from __future__ import annotations

from typing import Callable, Optional

import polars as pl
import streamlit as st

from ..insights import Insight
from ..value_engine import PlayerValuation
from ..value_store import ValueStore
from .components import PlayerRef, select_player_from_table

_KIND_BADGES = {"buy_low": "🟢 Buy low", "sell_high": "🔴 Sell high"}


def render_trends(
    valuations: dict[str, PlayerValuation],
    store: ValueStore,
    insights: list[Insight],
    fetch_history: Optional[Callable[[str], list[tuple[str, float]]]] = None,
    on_select_player: Optional[Callable[[PlayerRef], None]] = None,
) -> None:
    st.header("📈 Trends & Market Signals")

    if not valuations:
        st.info("Trends need player values — add a Parse API key in the sidebar.")
        return

    # ------------------------------------------------------------------
    # Buy low / sell high
    # ------------------------------------------------------------------
    st.subheader("Buy low / sell high")
    st.caption(
        "Players whose market price is moving against their profile — "
        "30-day trends, local snapshot history, and cross-source spreads."
    )
    if insights:
        rows = [
            {
                "Signal": _KIND_BADGES.get(i.kind, i.kind),
                "Player": i.player.name,
                "Pos": i.player.position,
                # Single-typed (str) so polars won't choke on int-age + "—" mix.
                "Age": str(int(i.player.age)) if i.player.age else "—",
                "Value": round(i.player.adjusted_value, 1),
                "Why": i.reason,
            }
            for i in insights
        ]
        keys = [
            PlayerRef(
                name=i.player.name,
                position=i.player.position,
                sleeper_id=i.player.sleeper_id,
            )
            for i in insights
        ]
        ref = select_player_from_table(
            pl.DataFrame(rows), keys, key="trends_insights"
        )
        if ref and on_select_player:
            on_select_player(ref)
    else:
        st.info("No strong buy-low / sell-high signals right now.")

    # ------------------------------------------------------------------
    # Risers and fallers
    # ------------------------------------------------------------------
    st.subheader("30-day market movers")
    movers = [v for v in valuations.values() if v.trend_frac is not None]
    if movers:
        col1, col2 = st.columns(2)
        risers = sorted(movers, key=lambda v: v.trend_frac or 0, reverse=True)[:10]
        fallers = sorted(movers, key=lambda v: v.trend_frac or 0)[:10]
        with col1:
            st.markdown("**📈 Risers**")
            ref = select_player_from_table(
                pl.DataFrame(
                    [
                        {
                            "Player": v.name,
                            "Pos": v.position,
                            "Value": round(v.adjusted_value, 1),
                            "30d": f"+{(v.trend_frac or 0) * 100:.1f}%",
                        }
                        for v in risers
                    ]
                ),
                [
                    PlayerRef(name=v.name, position=v.position, sleeper_id=v.sleeper_id)
                    for v in risers
                ],
                key="trends_risers",
            )
            if ref and on_select_player:
                on_select_player(ref)
        with col2:
            st.markdown("**📉 Fallers**")
            ref = select_player_from_table(
                pl.DataFrame(
                    [
                        {
                            "Player": v.name,
                            "Pos": v.position,
                            "Value": round(v.adjusted_value, 1),
                            "30d": f"{(v.trend_frac or 0) * 100:.1f}%",
                        }
                        for v in fallers
                    ]
                ),
                [
                    PlayerRef(name=v.name, position=v.position, sleeper_id=v.sleeper_id)
                    for v in fallers
                ],
                key="trends_fallers",
            )
            if ref and on_select_player:
                on_select_player(ref)
    else:
        st.info("No trend data available from the value sources.")

    # ------------------------------------------------------------------
    # Per-player history
    # ------------------------------------------------------------------
    st.subheader("Player value history")
    ordered = sorted(
        valuations.values(), key=lambda v: v.adjusted_value, reverse=True
    )
    options = {v.name: v for v in ordered}
    selected = st.selectbox(
        "Player", options=list(options), key="trends_player"
    )
    if not selected:
        return
    valuation = options[selected]

    frames = []
    local = store.get_history(valuation.key)
    if local:
        frames.append(
            pl.DataFrame(local, schema=["date", "Local snapshots"], orient="row")
        )
    if fetch_history is not None and valuation.fc_player_id:
        try:
            market = fetch_history(valuation.fc_player_id)
        except Exception as exc:  # network/auth issues shouldn't kill the tab
            market = []
            st.caption(f"FantasyCalc history unavailable: {exc}")
        if market:
            df = pl.DataFrame(
                market, schema=["date", "FantasyCalc value"], orient="row"
            )
            # Rescale the raw FantasyCalc series to the 0–100 display scale
            peak = df["FantasyCalc value"].max()
            if peak and peak > 0:
                df = df.with_columns(
                    (pl.col("FantasyCalc value") / peak * 100.0).alias(
                        "FantasyCalc value"
                    )
                )
            frames.append(df)

    if frames:
        # Polars has no index: outer-join the series on the shared "date" column
        # (they can cover different date ranges) and drive the x-axis explicitly.
        chart_df = frames[0]
        for extra in frames[1:]:
            chart_df = chart_df.join(extra, on="date", how="full", coalesce=True)
        st.line_chart(chart_df.sort("date"), x="date")
        n_local = len(local)
        st.caption(
            f"Local snapshots accumulate each day you open the app "
            f"({n_local} so far); the FantasyCalc series shows the market's "
            "full history rescaled to the 0–100 display scale."
        )
    else:
        st.info(
            "No history yet for this player — local snapshots build up daily, "
            "and FantasyCalc history needs a working API key."
        )
