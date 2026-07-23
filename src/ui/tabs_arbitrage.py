"""Arbitrage tab: cross-source value disagreements."""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
import streamlit as st

from ..data_providers import NormalizedPlayerValue
from ..name_matching import normalize_name
from ..trade_analyzer import TradeAnalyzer
from .components import PlayerRef, select_player_from_table


def render_arbitrage(
    sources: dict[str, list[NormalizedPlayerValue]],
    analyzer: TradeAnalyzer,
    prefs=None,
    on_select_player: Optional[Callable[[PlayerRef], None]] = None,
) -> None:
    st.header("📊 Arbitrage Opportunities")
    st.markdown(
        "Players where value sources disagree significantly.  "
        "**Buy** candidates are undervalued by one source — try to acquire "
        "them from managers who follow it.  **Sell** candidates are "
        "overvalued somewhere — move them to a believer."
    )

    if len([s for s in sources.values() if s]) < 2:
        st.info("Arbitrage needs at least two working value sources.")
        return

    # Provider values are already normalized 0–100 within each source.
    normalised = {
        source: {normalize_name(r.name): r.value for r in rows}
        for source, rows in sources.items()
        if rows
    }

    threshold = st.slider(
        "Minimum spread threshold (%)",
        min_value=10,
        max_value=50,
        value=int(getattr(prefs, "arb_threshold", 0.20) * 100),
        step=5,
        key="arb_threshold",
    ) / 100.0

    opps = analyzer.find_arbitrage(normalised, spread_threshold=threshold)

    if not opps:
        st.info("No arbitrage opportunities found at this threshold.")
        return

    rec_filter = st.radio(
        "Show",
        options=["All", "Buy", "Sell"],
        horizontal=True,
        key="arb_rec_filter",
    )

    if rec_filter != "All":
        opps = [o for o in opps if o.recommendation == rec_filter.lower()]

    rows = []
    row_keys: list[PlayerRef] = []
    for o in opps:
        source_cols = {
            f"{src} value": round(val, 1) for src, val in o.values_by_source.items()
        }
        rows.append(
            {
                "Player": o.player_name,
                "Consensus": round(o.consensus_value, 1),
                "Spread": f"{o.spread_pct * 100:.0f}%",
                "High source": o.high_source,
                "Low source": o.low_source,
                "Rec.": o.recommendation.upper(),
                **source_cols,
            }
        )
        row_keys.append(PlayerRef(name=o.player_name))

    if not rows:
        st.info("No opportunities match the current filter.")
        return

    st.caption("💡 Click a player's row for full detail.")
    ref = select_player_from_table(pd.DataFrame(rows), row_keys, key="arb_table")
    if ref and on_select_player:
        on_select_player(ref)
