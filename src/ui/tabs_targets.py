"""Trade Targets tab: ranked acquisition candidates on other rosters."""

from __future__ import annotations

from typing import Callable, Optional

import polars as pl
import streamlit as st

from ..name_matching import normalize_name
from ..trade_analyzer import TradeAnalyzer
from ..trade_calculator import TradeAsset
from ..value_engine import PlayerValuation
from .components import (
    POSITIONS,
    PlayerRef,
    format_components,
    select_player_from_table,
)


def render_trade_targets(
    my_assets: list[TradeAsset],
    all_rosters: dict[str, list[TradeAsset]],
    player_values: dict[str, float],
    positional_need: dict[str, int],
    analyzer: TradeAnalyzer,
    name_map: dict[str, PlayerValuation],
    on_select_player: Optional[Callable[[PlayerRef], None]] = None,
) -> None:
    st.header("🎯 Trade Targets")
    st.markdown(
        "Players on other rosters ranked by fit for **your** team — value "
        "weighted by your positional needs. Start trade talks here."
    )

    players_only = {
        team: [a for a in roster if not a.is_pick]
        for team, roster in all_rosters.items()
    }
    targets = analyzer.rank_trade_targets(
        my_roster=[a for a in my_assets if not a.is_pick],
        all_rosters=players_only,
        player_values=player_values,
        positional_need=positional_need,
    )

    pos_filter = st.multiselect(
        "Positions",
        options=POSITIONS,
        default=POSITIONS,
        key="targets_pos_filter",
    )
    top_n = st.slider("Show top", 10, 100, 30, 10, key="targets_top_n")

    rows = []
    row_keys: list[PlayerRef] = []
    for asset, team_name, score in targets:
        if asset.position not in pos_filter:
            continue
        valuation = name_map.get(normalize_name(asset.name))
        rows.append(
            {
                "Player": asset.name,
                "Pos": asset.position,
                "NFL": asset.team,
                # Single-typed (str) so polars won't choke on int-age + "—" mix.
                "Age": str(int(asset.age)) if asset.age else "—",
                "Owner": team_name,
                "Value": round(player_values.get(asset.name, asset.value), 1),
                "Fit Score": round(score, 1),
                "Why": format_components(valuation),
            }
        )
        row_keys.append(
            PlayerRef(
                name=asset.name,
                position=asset.position,
                sleeper_id=valuation.sleeper_id if valuation else None,
            )
        )
        if len(rows) >= top_n:
            break

    if not rows:
        st.info("No targets match the current filters.")
        return

    st.caption("💡 Click a player's row for full detail.")
    ref = select_player_from_table(
        pl.DataFrame(rows), row_keys, key="targets_table"
    )
    if ref and on_select_player:
        on_select_player(ref)
