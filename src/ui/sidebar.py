"""Sidebar: connection settings, league format panel, value-model weights,
and data diagnostics."""

from __future__ import annotations

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
    current_season: str,
) -> dict:
    """Sleeper username, season, and league selection."""
    st.sidebar.title("🏈 Dynasty Trade Finder")
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
        with st.sidebar:
            with st.spinner("Looking up your Sleeper account…"):
                user_data = fetch_user(username)
                leagues = (
                    fetch_leagues(user_data["user_id"], season) if user_data else []
                )
        if user_data is None:
            st.sidebar.error("Username not found.")
        else:
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
        "username": username,
        "user_data": user_data,
        "leagues": leagues,
        "league_id": league_id,
        "season": season,
    }


def render_format_panel(fmt: LeagueFormat, prefs=None) -> LeagueFormat:
    """Show the detected league format with manual overrides.

    *prefs* (a UserPreferences) seeds the override widgets so a saved
    override is restored across sessions.
    """
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Format:** {fmt.describe()}")

    ppr_opts = [1.0, 0.5, 0.0]
    with st.sidebar.expander("⚙️ Override format"):
        default_override = bool(getattr(prefs, "override_enabled", False))
        override = st.checkbox(
            "Override detected format", value=default_override, key="fmt_override"
        )
        if not override:
            return fmt
        d_qbs = getattr(prefs, "num_qbs", None) or fmt.num_qbs
        num_qbs = st.selectbox(
            "Startable QBs", [1, 2], index=0 if d_qbs <= 1 else 1, key="pref_num_qbs"
        )
        d_ppr = getattr(prefs, "ppr", None)
        d_ppr = d_ppr if d_ppr in ppr_opts else fmt.ppr
        ppr = st.selectbox(
            "PPR",
            ppr_opts,
            index=ppr_opts.index(d_ppr) if d_ppr in ppr_opts else 0,
            key="pref_ppr",
        )
        d_tep = getattr(prefs, "te_premium", None)
        te_premium = st.checkbox(
            "TE premium",
            value=bool(fmt.te_premium if d_tep is None else d_tep),
            key="pref_te_premium",
        )
        return fmt.model_copy(
            update={"num_qbs": num_qbs, "ppr": ppr, "te_premium": te_premium}
        )


def render_value_model(saved: Optional[ValueWeights] = None) -> ValueWeights:
    """Value-model weight sliders; returns the configured weights.

    *saved* seeds the sliders from the user's persisted preferences.
    """
    saved = saved or ValueWeights()
    saved_sources = saved.source_weights_dict()
    base = dict(DEFAULT_SOURCE_WEIGHTS)
    with st.sidebar.expander("⚖️ Value model", expanded=False):
        st.caption("Source blend — how much each market is trusted")
        source_weights = tuple(
            (
                source,
                st.slider(
                    SOURCE_LABELS.get(source, source),
                    0.0,
                    1.0,
                    float(saved_sources.get(source, base[source])),
                    0.05,
                    key=f"w_{source}",
                ),
            )
            for source in base
        )

        st.caption("Adjustments — 0 disables, 1 applies fully")
        age = st.slider("Age curve", 0.0, 1.0, saved.age_weight, 0.05, key="w_age")
        trend = st.slider(
            "Trend momentum (30d)", 0.0, 1.0, saved.trend_weight, 0.05, key="w_trend"
        )
        injury = st.slider(
            "Injury risk", 0.0, 1.0, saved.injury_weight, 0.05, key="w_injury"
        )
        adp = st.slider(
            "ADP divergence", 0.0, 1.0, saved.adp_divergence_weight, 0.05, key="w_adp"
        )
        production = st.slider(
            "Recent production",
            0.0,
            1.0,
            saved.production_weight,
            0.05,
            key="w_production",
            help=(
                "Tilt values toward players' validated on-field fantasy "
                "production (nflverse, ±5-20% scramble corrected)."
            ),
        )

        if st.button("Reset to defaults", key="w_reset"):
            for key in ("w_age", "w_trend", "w_injury", "w_adp", "w_production"):
                st.session_state.pop(key, None)
            for source in base:
                st.session_state.pop(f"w_{source}", None)
            st.rerun()

    return ValueWeights(
        source_weights=source_weights,
        age_weight=age,
        trend_weight=trend,
        injury_weight=injury,
        adp_divergence_weight=adp,
        production_weight=production,
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
        if "draftsharks" in sources or "draftsharks" in source_errors:
            st.caption(
                "ℹ️ DraftSharks' dynasty rankings are currently broken upstream "
                "(502 on every dynasty request), so values shown are pulled "
                "from their non-dynasty rankings instead."
            )
        if unmatched:
            st.markdown(
                f"❓ **{len(unmatched)} rostered players without values** "
                "(shown at value 0)"
            )
            st.caption(", ".join(sorted(set(unmatched))[:20]))
