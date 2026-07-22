"""Trade Explorer and Best Trades tabs."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from ..insights import TeamProfile, timeline_fit_bonus
from ..trade_analyzer import TradeAnalyzer
from ..trade_calculator import TradeAsset, TradeCalculator, TradeResult
from ..value_engine import PlayerValuation
from .components import POSITIONS, nearest_player

GRADE_COLORS = {
    "A+": "green", "A": "green", "B+": "green",
    "B": "orange",
    "C+": "red", "C": "red", "D": "red",
}


def render_trade_result(
    result: TradeResult, their_grade: Optional[str] = None
) -> None:
    color = GRADE_COLORS.get(result.grade, "grey")

    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Giving value", f"{result.giving_value:.1f}")
    c2.metric("Receiving value", f"{result.receiving_value:.1f}")
    delta_label = (
        f"+{result.value_delta:.1f}"
        if result.value_delta >= 0
        else f"{result.value_delta:.1f}"
    )
    c3.metric("Value delta", delta_label)
    c4.markdown(
        f"<h2 style='color:{color}; text-align:center'>{result.grade}</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(f"**{result.summary}**")
    if their_grade:
        st.caption(
            f"From the other side of the table this trade grades **{their_grade}** "
            "(their positional needs included) — a mutual-benefit check on "
            "whether they'd plausibly accept."
        )


def render_trade_explorer(
    my_assets: list[TradeAsset],
    all_rosters: dict[str, list[TradeAsset]],
    calculator: TradeCalculator,
    positional_need: dict[str, int],
    counterparty_needs: dict[str, dict[str, float]],
    valuations: dict[str, PlayerValuation],
) -> None:
    st.header("🔄 Trade Explorer")
    st.markdown("Build a trade manually — players **and draft picks** — and see how it scores.")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("I give…")
        my_options = {
            a.display_name: a
            for a in sorted(my_assets, key=lambda x: x.value, reverse=True)
        }
        giving_names = st.multiselect(
            "Select assets from your roster",
            options=list(my_options),
            key="giving",
        )
        giving = [my_options[n] for n in giving_names]

    with col2:
        st.subheader("I receive…")
        their_options: dict[str, tuple[TradeAsset, str]] = {}
        for team, assets in all_rosters.items():
            for a in assets:
                their_options[f"{a.display_name} — {team}"] = (a, team)
        receiving_names = st.multiselect(
            "Select assets from other teams",
            options=sorted(
                their_options, key=lambda n: their_options[n][0].value, reverse=True
            ),
            key="receiving",
        )
        receiving = [their_options[n][0] for n in receiving_names]
        receiving_teams = {their_options[n][1] for n in receiving_names}

    if giving and receiving:
        result = calculator.calculate_trade_value(giving, receiving, positional_need)

        their_grade: Optional[str] = None
        if len(receiving_teams) == 1:
            team = next(iter(receiving_teams))
            their_result = calculator.calculate_trade_value(
                receiving, giving, counterparty_needs.get(team)
            )
            their_grade = their_result.grade

        render_trade_result(result, their_grade)

        for asset in giving + receiving:
            if asset.is_pick:
                comp = nearest_player(asset.value, valuations)
                if comp:
                    st.caption(
                        f"💡 {asset.display_name} ≈ **{comp.name}** "
                        f"({comp.position}, value {comp.adjusted_value:.1f})"
                    )
    else:
        st.info("Select at least one asset on each side to evaluate a trade.")


def render_best_trades(
    my_assets: list[TradeAsset],
    all_rosters: dict[str, list[TradeAsset]],
    player_values: dict[str, float],
    positional_need: dict[str, int],
    analyzer: TradeAnalyzer,
    counterparty_needs: dict[str, dict[str, float]],
    profiles_by_team: dict[str, TeamProfile],
    my_classification: str,
) -> None:
    st.header("⚡ Best Trades")
    st.markdown(
        "Automatically generated proposals that improve your team, ranked by "
        "value gain, mutual benefit, positional need, and timeline fit."
    )

    c1, c2 = st.columns([1, 2])
    include_picks = c1.checkbox("Include draft picks", value=True)
    balance = c2.slider(
        "Trade balance",
        min_value=0,
        max_value=100,
        value=50,
        step=5,
        format="%d%%",
        help="0% ranks by your value gain alone (may surface lopsided trades "
        "the other manager would never accept). 100% ranks by mutual "
        "benefit — trades that fill both teams' positional needs. 50% "
        "balances the two.",
    )
    fairness_weight = balance / 100.0

    c3, c4 = st.columns(2)
    mutual_only = c3.checkbox(
        "Only trades they might accept",
        value=True,
        help="Evaluates every proposal from the counterparty's perspective "
        "(their positional needs included) and drops anything they'd grade "
        "below C+.",
    )
    timeline_fit = c4.checkbox(
        "Boost timeline-fit trades",
        value=True,
        help="Prefers contender↔rebuilder trades: win-now assets flow to "
        "contenders, youth and picks to rebuilders.",
    )

    mine = my_assets if include_picks else [a for a in my_assets if not a.is_pick]
    theirs = (
        all_rosters
        if include_picks
        else {t: [a for a in r if not a.is_pick] for t, r in all_rosters.items()}
    )

    bonus_scorer = None
    if timeline_fit:

        def bonus_scorer(team_name, giving, receiving):  # noqa: ANN001
            profile = profiles_by_team.get(team_name)
            if profile is None:
                return 0.0
            return timeline_fit_bonus(
                my_classification, profile.classification, giving, receiving
            )

    with st.spinner("Analysing trade opportunities…"):
        proposals = analyzer.find_best_trades(
            my_roster=mine,
            all_rosters=theirs,
            player_values=player_values,
            positional_need=positional_need,
            max_assets_per_side=2,
            top_n=25,
            counterparty_needs=counterparty_needs,
            min_their_grade="C+" if mutual_only else None,
            max_per_team=3,
            fairness_weight=fairness_weight,
            bonus_scorer=bonus_scorer,
        )

    if not proposals:
        st.info(
            "No matching trades found. Try relaxing the mutual-benefit filter "
            "or refreshing player values."
        )
        return

    pos_filter = st.multiselect(
        "Filter by position received",
        options=POSITIONS + ["PICK"],
        default=POSITIONS + ["PICK"],
        key="best_trades_pos_filter",
    )

    rows = []
    for p in proposals:
        if not any(a.position in pos_filter for a in p.receiving):
            continue
        row = {
            "Give": " + ".join(a.display_name for a in p.giving),
            "Receive": " + ".join(a.display_name for a in p.receiving),
            "From": p.their_team,
            "Give Value": round(p.result.giving_value, 1),
            "Receive Value": round(p.result.receiving_value, 1),
            "Delta": round(p.result.value_delta, 1),
            "My Grade": p.result.grade,
        }
        if p.their_grade is not None:
            row["Their Grade"] = p.their_grade
        rows.append(row)

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No proposals match the selected position filter.")
