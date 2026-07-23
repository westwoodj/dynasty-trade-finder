"""Trade Explorer and Best Trades tabs."""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
import streamlit as st

from ..insights import TeamProfile, timeline_fit_bonus
from ..trade_analyzer import TradeAnalyzer
from ..trade_calculator import TradeAsset, TradeCalculator, TradeResult
from ..value_engine import PlayerValuation
from .components import POSITIONS, PlayerRef, nearest_player, select_table_row

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


def _asset_label(asset: TradeAsset) -> str:
    """Selection label with position and value, e.g. ``"Ja'Marr Chase (WR) — 85.2"``."""
    return f"{asset.display_name} ({asset.position}) — {asset.value:.1f}"


def _asset_options(
    pairs: list[tuple[TradeAsset, Optional[PlayerValuation]]]
) -> dict[str, TradeAsset]:
    """Map each asset's selection label back to the asset itself."""
    return {_asset_label(asset): asset for asset, _ in pairs}


def _render_roster_picker(
    pairs: list[tuple[TradeAsset, Optional[PlayerValuation]]],
    side_key: str,
    pos_filter: list[str],
    *,
    key: str,
) -> None:
    """Roster table (players and picks inline) whose rows add an asset to a side.

    Clicking a row queues the asset into ``side_key`` for the next run.  The
    position filter only narrows what's *shown* — it never touches the trade, so
    a filtered-out asset already in the deal stays in it.
    """
    selected = set(st.session_state.get(side_key, []))
    rows = []
    labels: list[str] = []
    for asset, valuation in sorted(pairs, key=lambda p: p[0].value, reverse=True):
        if asset.position not in pos_filter:
            continue
        label = _asset_label(asset)
        rows.append(
            {
                "In deal": "✅" if label in selected else "",
                "Player": asset.display_name,
                "Pos": asset.position,
                "Team": asset.team or "—",
                # Picks carry no age; keep the column all-string so Arrow doesn't
                # choke on a mix of numbers and the "—" placeholder.
                "Age": str(int(asset.age)) if asset.age else "—",
                "Base": round(valuation.base_value, 1)
                if valuation
                else round(asset.value, 1),
                "Value": round(asset.value, 1),
            }
        )
        labels.append(label)

    if not rows:
        st.caption("No assets match the position filter.")
        return

    idx = select_table_row(pd.DataFrame(rows), key=key)
    if idx is not None and 0 <= idx < len(labels):
        st.session_state["_te_pending"] = (side_key, labels[idx])
        st.rerun()


def render_trade_explorer(
    my_pairs: list[tuple[TradeAsset, Optional[PlayerValuation]]],
    their_pairs_by_team: dict[
        str, list[tuple[TradeAsset, Optional[PlayerValuation]]]
    ],
    calculator: TradeCalculator,
    positional_need: dict[str, int],
    counterparty_needs: dict[str, dict[str, float]],
    valuations: dict[str, PlayerValuation],
) -> None:
    st.header("🔄 Trade Explorer")
    st.markdown(
        "Pick a team, compare rosters side-by-side, and build a trade — players "
        "**and draft picks**. Click a roster row to add an asset; use the "
        "selectors below to remove one."
    )

    teams = sorted(their_pairs_by_team)
    if not teams:
        st.info("No other teams found in this league.")
        return
    if not my_pairs:
        st.info("No assets found on your roster.")
        return

    partner = st.selectbox("Trade with", options=teams, key="te_partner")

    # Their assets change with the partner, so drop any stale receiving picks.
    if st.session_state.get("_te_partner_prev") != partner:
        st.session_state["_te_partner_prev"] = partner
        st.session_state["te_recv"] = []

    # A roster-row click on the previous run queued an asset — toggle it now,
    # before the multiselects below read (and lock) their widget state.
    pending = st.session_state.pop("_te_pending", None)
    if pending is not None:
        side_key, label = pending
        bucket = list(st.session_state.get(side_key, []))
        if label in bucket:
            bucket.remove(label)
        else:
            bucket.append(label)
        st.session_state[side_key] = bucket

    my_options = _asset_options(my_pairs)
    their_pairs = their_pairs_by_team[partner]
    their_options = _asset_options(their_pairs)

    # Keep only still-valid selections (the roster or partner may have changed)
    # so seeding the multiselects can't raise on an out-of-options value.
    st.session_state["te_give"] = [
        lbl for lbl in st.session_state.get("te_give", []) if lbl in my_options
    ]
    st.session_state["te_recv"] = [
        lbl for lbl in st.session_state.get("te_recv", []) if lbl in their_options
    ]

    positions = POSITIONS + ["PICK"]
    st.multiselect(
        "Show positions",
        options=positions,
        default=positions,
        key="te_pos_filter",
        help="Narrows the roster tables below — it doesn't affect the trade.",
    )
    pos_filter = st.session_state.get("te_pos_filter", positions)

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Your roster")
        _render_roster_picker(my_pairs, "te_give", pos_filter, key="te_my_table")
    with col2:
        st.subheader(f"{partner}'s roster")
        _render_roster_picker(
            their_pairs, "te_recv", pos_filter, key="te_their_table"
        )

    st.markdown("---")
    gcol, rcol = st.columns(2)
    with gcol:
        give_labels = st.multiselect(
            "You give", options=list(my_options), key="te_give"
        )
    with rcol:
        recv_labels = st.multiselect(
            "You receive", options=list(their_options), key="te_recv"
        )

    giving = [my_options[lbl] for lbl in give_labels]
    receiving = [their_options[lbl] for lbl in recv_labels]

    if giving and receiving:
        result = calculator.calculate_trade_value(giving, receiving, positional_need)
        their_result = calculator.calculate_trade_value(
            receiving, giving, counterparty_needs.get(partner)
        )
        render_trade_result(result, their_result.grade)

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
    prefs=None,
    on_select_player: Optional[Callable[[PlayerRef], None]] = None,
) -> None:
    st.header("⚡ Best Trades")
    st.markdown(
        "Automatically generated proposals that improve your team, ranked by "
        "value gain, mutual benefit, positional need, and timeline fit."
    )

    # A trade row bundles multiple players, so selecting one opens a breakdown
    # dialog with a button per player; that button routes here via session state.
    pending = st.session_state.pop("_bt_pending_player", None)
    if pending is not None and on_select_player is not None:
        on_select_player(pending)

    c1, c2 = st.columns([1, 2])
    include_picks = c1.checkbox(
        "Include draft picks",
        value=bool(getattr(prefs, "include_picks", True)),
        key="pref_include_picks",
    )
    balance = c2.slider(
        "Trade balance",
        min_value=0,
        max_value=100,
        value=int(getattr(prefs, "fairness_weight", 0.5) * 100),
        step=5,
        format="%d%%",
        key="pref_fairness",
        help="0% ranks by your value gain alone (may surface lopsided trades "
        "the other manager would never accept). 100% ranks by mutual "
        "benefit — trades that fill both teams' positional needs. 50% "
        "balances the two.",
    )
    fairness_weight = balance / 100.0

    c3, c4 = st.columns(2)
    mutual_only = c3.checkbox(
        "Only trades they might accept",
        value=bool(getattr(prefs, "mutual_only", True)),
        key="pref_mutual_only",
        help="Evaluates every proposal from the counterparty's perspective "
        "(their positional needs included) and drops anything they'd grade "
        "below C+.",
    )
    timeline_fit = c4.checkbox(
        "Boost timeline-fit trades",
        value=bool(getattr(prefs, "timeline_fit", True)),
        key="pref_timeline_fit",
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
    shown = []
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
        shown.append(p)

    if not rows:
        st.info("No proposals match the selected position filter.")
        return

    st.caption("💡 Click a trade row to break it down and inspect each player.")
    idx = select_table_row(pd.DataFrame(rows), key="best_trades_table")
    if idx is not None and 0 <= idx < len(shown):
        _open_trade_breakdown(shown[idx])


def _open_trade_breakdown(proposal) -> None:
    """Pop-up listing a proposal's players as buttons that open player detail."""

    @st.dialog("Trade breakdown", width="large")
    def _dialog() -> None:
        st.caption(f"With **{proposal.their_team}** · your grade {proposal.result.grade}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**You give**")
            _player_buttons(proposal.giving, "give")
        with c2:
            st.markdown("**You receive**")
            _player_buttons(proposal.receiving, "recv")
        st.caption("Click a player to see full detail.")

    _dialog()


def _player_buttons(assets: list[TradeAsset], prefix: str) -> None:
    for i, asset in enumerate(assets):
        if asset.is_pick:
            st.markdown(f"• {asset.display_name} _(pick)_")
            continue
        if st.button(f"🔍 {asset.display_name}", key=f"bt_{prefix}_{i}_{asset.name}"):
            # Route to the shared player-detail modal on the next full rerun,
            # which also dismisses this breakdown dialog.
            st.session_state["_bt_pending_player"] = PlayerRef(
                name=asset.name, position=asset.position
            )
            st.rerun()
