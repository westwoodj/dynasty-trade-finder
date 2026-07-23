"""My Roster tab: valued roster with adjustment explanations and needs."""

from __future__ import annotations

from typing import Callable, Optional

import polars as pl
import streamlit as st

from ..trade_calculator import TradeAsset
from ..value_engine import PlayerValuation
from .components import (
    POSITIONS,
    PlayerRef,
    format_components,
    select_player_from_table,
)


def render_my_roster(
    pairs: list[tuple[TradeAsset, Optional[PlayerValuation]]],
    my_picks: list[TradeAsset],
    headcount_need: dict[str, int],
    vor_need: Optional[dict[str, float]] = None,
    on_select_player: Optional[Callable[[PlayerRef], None]] = None,
) -> None:
    st.header("📋 My Roster")

    if not pairs:
        st.info("No skill-position players found on your roster.")
        return

    col1, col2 = st.columns([3, 1])

    with col1:
        rows = []
        row_keys: list[PlayerRef] = []
        for asset, valuation in sorted(pairs, key=lambda p: p[0].value, reverse=True):
            rows.append(
                {
                    "Player": asset.name,
                    "Pos": asset.position,
                    "Team": asset.team,
                    # Keep the column single-typed (str) — polars won't build a
                    # column that mixes an int age with the "—" placeholder.
                    "Age": str(int(asset.age)) if asset.age else "—",
                    "Base": round(valuation.base_value, 1) if valuation else 0.0,
                    "Value": round(asset.value, 1),
                    "Adjustments": format_components(valuation),
                }
            )
            row_keys.append(
                PlayerRef(
                    name=asset.name,
                    position=asset.position,
                    sleeper_id=valuation.sleeper_id if valuation else None,
                )
            )
        st.caption("💡 Click a player's row for full detail.")
        ref = select_player_from_table(
            pl.DataFrame(rows), row_keys, key="roster_table"
        )
        if ref and on_select_player:
            on_select_player(ref)

        if my_picks:
            st.subheader("Draft picks")
            pick_rows = [
                {"Pick": p.display_name, "Value": round(p.value, 1)}
                for p in sorted(my_picks, key=lambda x: x.value, reverse=True)
            ]
            st.dataframe(
                pl.DataFrame(pick_rows), use_container_width=True, hide_index=True
            )

    with col2:
        st.subheader("Positional needs")
        st.caption("Depth vs roster targets")
        for pos in POSITIONS:
            n = headcount_need.get(pos, 0)
            bar = "🟩" * min(n, 5) + "⬜" * max(0, 5 - n)
            st.markdown(f"**{pos}** {bar} ({n})")

        if vor_need is not None:
            st.caption(
                "Starter quality vs league replacement level "
                "(starter slots below replacement)"
            )
            for pos in POSITIONS:
                shortage = vor_need.get(pos, 0.0)
                icon = "🔴" if shortage >= 1.0 else "🟡" if shortage > 0.25 else "🟢"
                st.markdown(f"{icon} **{pos}** {shortage:.2f}")
