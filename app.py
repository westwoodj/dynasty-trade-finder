"""Dynasty Trade Finder — Streamlit web application.

Usage
-----
Run locally::

    streamlit run app.py

Configuration
-------------
Player values come from the typed Parse API clients (FantasyCalc,
KeepTradeCut, DraftSharks).  Provide your Parse API key via the sidebar,
the ``PARSE_API_KEY`` environment variable, or
``.streamlit/secrets.toml``::

    [parse]
    api_key = "your-key-here"

On a fresh checkout the generated clients must be synced first::

    uv run parse sync
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests
import streamlit as st

from src.config import get_parse_api_key, set_parse_api_key_env
from src.data_providers import (
    FantasyCalcProvider,
    NormalizedPlayerValue,
    fetch_all_sources,
)
from src.insights import (
    classify_teams,
    find_buy_low_sell_high,
    profile_team,
    replacement_baselines,
    value_based_need,
)
from src.league_settings import (
    LeagueFormat,
    detect_league_format,
    position_multipliers,
)
from src.pick_valuation import (
    build_pick_assets,
    future_seasons,
    resolve_pick_ownership,
)
from src.sleeper_client import SleeperClient
from src.trade_analyzer import TradeAnalyzer
from src.trade_calculator import TradeCalculator
from src.ui.components import (
    build_name_map,
    build_player_lookup,
    build_user_lookup,
    roster_valuations,
    team_names_by_roster,
)
from src.ui.sidebar import (
    render_connection_sidebar,
    render_diagnostics,
    render_format_panel,
    render_value_model,
)
from src.ui.tabs_arbitrage import render_arbitrage
from src.ui.tabs_league import render_league_overview
from src.ui.tabs_roster import render_my_roster
from src.ui.tabs_targets import render_trade_targets
from src.ui.tabs_trades import render_best_trades, render_trade_explorer
from src.ui.tabs_trends import render_trends
from src.value_engine import ValueEngine
from src.value_store import ValueStore

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Dynasty Trade Finder",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="expanded",
)

CURRENT_SEASON = str(datetime.now().year)


# ---------------------------------------------------------------------------
# Cached fetchers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner="Fetching Sleeper data…")
def fetch_user(username: str) -> Optional[dict]:
    try:
        return SleeperClient().get_user(username)
    except requests.HTTPError:
        return None


@st.cache_data(ttl=300, show_spinner="Loading leagues…")
def fetch_leagues(user_id: str, season: str) -> list[dict]:
    try:
        return SleeperClient().get_user_leagues(user_id, season) or []
    except requests.HTTPError:
        return []


@st.cache_data(ttl=120, show_spinner="Loading league data…")
def fetch_league_data(league_id: str) -> dict:
    client = SleeperClient()
    league = client.get_league(league_id)
    rosters = client.get_league_rosters(league_id)
    users = client.get_league_users(league_id)
    return {"league": league, "rosters": rosters, "users": users}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_draft_data(league_id: str) -> dict:
    client = SleeperClient()
    try:
        drafts = client.get_league_drafts(league_id) or []
    except Exception:
        drafts = []
    try:
        traded_picks = client.get_league_traded_picks(league_id) or []
    except Exception:
        traded_picks = []
    return {"drafts": drafts, "traded_picks": traded_picks}


@st.cache_data(ttl=3600, show_spinner="Downloading NFL player list (~5 MB)…")
def fetch_nfl_players() -> dict:
    try:
        return SleeperClient().get_nfl_players()
    except Exception:
        return {}


@st.cache_data(ttl=1800, show_spinner="Fetching player values…")
def fetch_source_values(
    fmt_key: str, api_key: str, _fmt: LeagueFormat
) -> tuple[dict[str, list[NormalizedPlayerValue]], dict[str, str]]:
    """Fetch every value source once per league format per 30 minutes.

    ``fmt_key``/``api_key`` are the cache keys; ``_fmt`` (underscored, so
    unhashed) carries the actual format object.
    """
    return fetch_all_sources(_fmt, api_key=api_key or None)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_player_history(
    fc_player_id: str, fmt_key: str, api_key: str, _fmt: LeagueFormat
) -> list[tuple[str, float]]:
    provider = FantasyCalcProvider(api_key=api_key or None)
    return provider.fetch_trade_history(fc_player_id, _fmt)


# ---------------------------------------------------------------------------
# Welcome page
# ---------------------------------------------------------------------------


def render_welcome() -> None:
    st.title("🏈 Dynasty Trade Finder")
    st.markdown(
        """
Welcome to **Dynasty Trade Finder** — a market-driven trade analysis tool
for dynasty fantasy football leagues on Sleeper.

### Getting started
1. Enter your **Sleeper username** in the sidebar.
2. Select your **season** and **league** — scoring format (superflex, PPR,
   TE premium) is detected automatically.
3. Add your **Parse API key** to pull live player values from
   FantasyCalc, KeepTradeCut, and DraftSharks.

### Features
| Tab | Description |
|-----|-------------|
| 🏟️ League Overview | Roster values, contender/rebuilder timelines for every team |
| 📋 My Roster | Your players' blended values with age/trend/injury adjustments |
| 🔄 Trade Explorer | Build trades with players **and picks**, see both sides' grades |
| ⚡ Best Trades | Auto-generated proposals filtered to trades the other side might accept |
| 🎯 Trade Targets | Acquisition candidates ranked by fit for your roster |
| 📊 Arbitrage | Players the value sources disagree on — buy low, sell high |
| 📈 Trends | 30-day market movers, buy-low/sell-high flags, value history charts |

Tune the **value model** in the sidebar: source blend weights plus age,
trend, injury, and ADP adjustments.
        """
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    cfg = render_connection_sidebar(
        fetch_user, fetch_leagues, get_parse_api_key() or "", CURRENT_SEASON
    )

    user_data: Optional[dict] = cfg["user_data"]
    league_id: Optional[str] = cfg["league_id"]
    api_key: str = cfg["api_key"]

    if not user_data or not league_id:
        render_welcome()
        return

    # ---- League data ----
    with st.spinner("Loading league data…"):
        league_data = fetch_league_data(league_id)
        nfl_players = fetch_nfl_players()
    league = league_data["league"]
    rosters = league_data["rosters"]

    fmt = render_format_panel(detect_league_format(league))
    weights = render_value_model()

    # ---- Player values ----
    if api_key:
        set_parse_api_key_env(api_key)
    sources, source_errors = fetch_source_values(fmt.cache_key(), api_key, fmt)
    engine = ValueEngine(weights)
    valuations = engine.blend(sources)

    if not valuations:
        st.warning(
            "No player values available. Add a Parse API key in the sidebar "
            "(or run `uv run parse login` / `uv run parse sync`). Rosters "
            "will show with zero values."
        )

    # One local snapshot per day powers the Trends tab
    store = ValueStore()
    if valuations and not store.has_snapshot():
        store.save_snapshot(valuations)

    # ---- Lookups and roster conversion ----
    player_lookup = build_player_lookup(nfl_players)
    user_lookup = build_user_lookup(league_data["users"])
    team_names = team_names_by_roster(rosters, user_lookup)
    sleeper_map = engine.sleeper_value_map(valuations)
    name_map = build_name_map(valuations)
    name_values = engine.name_value_map(valuations)

    my_user_id = user_data["user_id"]
    my_roster_raw = next(
        (r for r in rosters if str(r.get("owner_id")) == str(my_user_id)),
        rosters[0] if rosters else {},
    )
    my_rid = my_roster_raw.get("roster_id")

    roster_pairs: dict[int, list] = {}
    roster_assets: dict[int, list] = {}
    unmatched: list[str] = []
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None:
            continue
        pairs = roster_valuations(roster, player_lookup, sleeper_map, name_map)
        roster_pairs[rid] = pairs
        roster_assets[rid] = [asset for asset, _ in pairs]
        unmatched.extend(a.name for a, v in pairs if v is None)

    render_diagnostics(source_errors, sources, unmatched if valuations else [])

    # ---- Draft picks (strength ranks project pick slots) ----
    totals = {rid: sum(a.value for a in assets) for rid, assets in roster_assets.items()}
    strength_rank = {
        rid: position + 1
        for position, (rid, _) in enumerate(
            sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        )
    }
    draft_data = fetch_draft_data(league_id)
    draft_rounds = 4
    if draft_data["drafts"]:
        draft_rounds = int(
            (draft_data["drafts"][0].get("settings") or {}).get("rounds", 4)
        )
    league_season = league.get("season", CURRENT_SEASON)
    owned_picks = resolve_pick_ownership(
        rosters,
        draft_data["traded_picks"],
        future_seasons(league_season),
        rounds=draft_rounds,
    )
    picks_by_roster = build_pick_assets(
        owned_picks, strength_rank, fmt, league_season, team_names
    )

    # ---- Team profiles ----
    profiles: dict[int, object] = {}
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None:
            continue
        settings = roster.get("settings") or {}
        profiles[rid] = profile_team(
            rid,
            team_names.get(rid, f"Team {rid}"),
            roster_assets.get(rid, []),
            picks_by_roster.get(rid, []),
            wins=settings.get("wins", 0),
            losses=settings.get("losses", 0),
        )
    classify_teams(profiles)  # league-relative contender/rebuilder labels
    profiles_by_team = {p.team_name: p for p in profiles.values()}
    my_profile = profiles.get(my_rid)
    my_classification = my_profile.classification if my_profile else "balanced"

    # ---- Trade engine ----
    calculator = TradeCalculator(position_multipliers(fmt))
    analyzer = TradeAnalyzer(calculator)

    my_players = roster_assets.get(my_rid, [])
    my_picks = picks_by_roster.get(my_rid, [])
    my_assets = my_players + my_picks

    headcount_need = calculator.calculate_positional_need(my_players, fmt.slots_dict())
    vor_need = None
    if valuations:
        baselines = replacement_baselines(valuations, fmt)
        vor_need = value_based_need(my_players, baselines, fmt.slots_dict())

    all_rosters: dict[str, list] = {}
    counterparty_needs: dict[str, dict] = {}
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None or rid == my_rid:
            continue
        team = team_names.get(rid, f"Team {rid}")
        all_rosters[team] = roster_assets.get(rid, []) + picks_by_roster.get(rid, [])
        counterparty_needs[team] = calculator.calculate_positional_need(
            roster_assets.get(rid, []), fmt.slots_dict()
        )

    market_insights = find_buy_low_sell_high(valuations, store.get_deltas(30))

    # ---- Tabs ----
    tabs = st.tabs(
        [
            "🏟️ League Overview",
            "📋 My Roster",
            "🔄 Trade Explorer",
            "⚡ Best Trades",
            "🎯 Trade Targets",
            "📊 Arbitrage",
            "📈 Trends",
        ]
    )

    with tabs[0]:
        render_league_overview(league, profiles, roster_assets)
    with tabs[1]:
        render_my_roster(
            roster_pairs.get(my_rid, []), my_picks, headcount_need, vor_need
        )
    with tabs[2]:
        render_trade_explorer(
            my_assets, all_rosters, calculator, headcount_need,
            counterparty_needs, valuations,
        )
    with tabs[3]:
        render_best_trades(
            my_assets, all_rosters, name_values, headcount_need, analyzer,
            counterparty_needs, profiles_by_team, my_classification,
        )
    with tabs[4]:
        render_trade_targets(
            my_assets, all_rosters, name_values, headcount_need, analyzer, name_map
        )
    with tabs[5]:
        render_arbitrage(sources, analyzer)
    with tabs[6]:
        render_trends(
            valuations,
            store,
            market_insights,
            fetch_history=lambda pid: fetch_player_history(
                pid, fmt.cache_key(), api_key, fmt
            ),
        )


if __name__ == "__main__":
    main()
