"""Sidebar: connection settings, league format panel, value-model weights,
and data diagnostics."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

import streamlit as st

from ..league_settings import LeagueFormat
from ..value_engine import DEFAULT_SOURCE_WEIGHTS, ValueWeights

SOURCE_LABELS = {
    "fantasycalc": "FantasyCalc",
    "ktc": "KeepTradeCut",
    "draftsharks": "DraftSharks",
}


def render_connection_sidebar(
    fetch_user: Callable[[str], Optional[dict]],
    fetch_leagues: Callable[[str, str], list[dict]],
    default_api_key: str,
    current_season: str,
) -> dict:
    """API key, Sleeper username, season, and league selection."""
    st.sidebar.title("🏈 Dynasty Trade Finder")
    st.sidebar.markdown("---")

    api_key = st.sidebar.text_input(
        "Parse API key",
        value=default_api_key,
        type="password",
        help=(
            "Powers player values from FantasyCalc, KeepTradeCut, and "
            "DraftSharks. Also read from PARSE_API_KEY or "
            ".streamlit/secrets.toml ([parse] api_key). Leave blank if "
            "you've run `parse login`."
        ),
    )

    st.sidebar.markdown("---")

    username = st.sidebar.text_input(
        "Sleeper username",
        help="Your Sleeper username (not display name).",
    )
    season = st.sidebar.selectbox(
        "Season",
        options=[str(y) for y in range(int(current_season), int(current_season) - 3, -1)],
    )

    user_data: Optional[dict] = None
    leagues: list[dict] = []
    league_id: Optional[str] = None

    if username:
        user_data = fetch_user(username)
        if user_data is None:
            st.sidebar.error("Username not found.")
        else:
            leagues = fetch_leagues(user_data["user_id"], season)
            if not leagues:
                st.sidebar.warning("No leagues found for this season.")
            else:
                league_names = {
                    lg["league_id"]: lg.get("name", lg["league_id"]) for lg in leagues
                }
                league_id = st.sidebar.selectbox(
                    "League",
                    options=list(league_names),
                    format_func=lambda lid: league_names[lid],
                )

    return {
        "api_key": api_key,
        "username": username,
        "user_data": user_data,
        "leagues": leagues,
        "league_id": league_id,
        "season": season,
    }


def render_format_panel(fmt: LeagueFormat) -> LeagueFormat:
    """Show the detected league format with manual overrides."""
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Format:** {fmt.describe()}")

    with st.sidebar.expander("⚙️ Override format"):
        override = st.checkbox("Override detected format", key="fmt_override")
        if not override:
            return fmt
        num_qbs = st.selectbox(
            "Startable QBs", [1, 2], index=fmt.num_qbs - 1 if fmt.num_qbs <= 2 else 1
        )
        ppr = st.selectbox(
            "PPR", [1.0, 0.5, 0.0], index=[1.0, 0.5, 0.0].index(fmt.ppr)
        )
        te_premium = st.checkbox("TE premium", value=fmt.te_premium)
        return replace(fmt, num_qbs=num_qbs, ppr=ppr, te_premium=te_premium)


def render_value_model() -> ValueWeights:
    """Value-model weight sliders; returns the configured weights."""
    defaults = dict(DEFAULT_SOURCE_WEIGHTS)
    with st.sidebar.expander("⚖️ Value model", expanded=False):
        st.caption("Source blend — how much each market is trusted")
        source_weights = tuple(
            (
                source,
                st.slider(
                    SOURCE_LABELS.get(source, source),
                    0.0,
                    1.0,
                    defaults[source],
                    0.05,
                    key=f"w_{source}",
                ),
            )
            for source in defaults
        )

        st.caption("Adjustments — 0 disables, 1 applies fully")
        age = st.slider("Age curve", 0.0, 1.0, 0.5, 0.05, key="w_age")
        trend = st.slider("Trend momentum (30d)", 0.0, 1.0, 0.0, 0.05, key="w_trend")
        injury = st.slider("Injury risk", 0.0, 1.0, 0.0, 0.05, key="w_injury")
        adp = st.slider("ADP divergence", 0.0, 1.0, 0.0, 0.05, key="w_adp")

        if st.button("Reset to defaults", key="w_reset"):
            for key in ("w_age", "w_trend", "w_injury", "w_adp"):
                st.session_state.pop(key, None)
            for source in defaults:
                st.session_state.pop(f"w_{source}", None)
            st.rerun()

    return ValueWeights(
        source_weights=source_weights,
        age_weight=age,
        trend_weight=trend,
        injury_weight=injury,
        adp_divergence_weight=adp,
    )


def render_diagnostics(
    source_errors: dict[str, str],
    sources: dict[str, list],
    unmatched: list[str],
) -> None:
    """Data-health panel: per-source status and unmatched players."""
    with st.sidebar.expander("🩺 Data diagnostics"):
        for source, rows in sources.items():
            st.markdown(
                f"✅ **{SOURCE_LABELS.get(source, source)}** — {len(rows)} players"
            )
        for source, error in source_errors.items():
            st.markdown(f"⚠️ **{SOURCE_LABELS.get(source, source)}** — {error}")
        if unmatched:
            st.markdown(
                f"❓ **{len(unmatched)} rostered players without values** "
                "(shown at value 0)"
            )
            st.caption(", ".join(sorted(set(unmatched))[:20]))
