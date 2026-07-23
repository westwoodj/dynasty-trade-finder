"""Dynasty Trade Finder — Streamlit web application.

Usage
-----
Run locally::

    streamlit run app.py

Configuration
-------------
Provide your Parse.bot API key via the sidebar input or by adding it to
``.streamlit/secrets.toml``::

    [parse_bot]
    api_key = "your-key-here"
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
import streamlit as st

from src.parse_bot_client import ParseBotClient, PlayerValue, normalize_name
from src.sleeper_client import SleeperClient
from src.trade_analyzer import ArbitrageOpportunity, TradeAnalyzer, TradeProposal
from src.trade_calculator import TradeAsset, TradeCalculator, TradeResult

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Dynasty Trade Finder",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_SEASON = str(datetime.now().year)

# Default superflex PPR starter slots
SUPERFLEX_STARTER_SLOTS: dict[str, int] = {
    "QB": 2,
    "RB": 2,
    "WR": 3,
    "TE": 1,
}

# Draft pick consensus values (0-100 normalised scale)
PICK_VALUES: dict[tuple[int, int], float] = {
    (1, 1): 90.0, (1, 2): 78.0, (1, 3): 65.0,
    (2, 1): 42.0, (2, 2): 32.0, (2, 3): 25.0,
    (3, 1): 16.0, (3, 2): 11.0, (3, 3): 8.0,
    (4, 1): 5.0,  (4, 2): 3.5,  (4, 3): 2.5,
}

POSITION_COLORS: dict[str, str] = {
    "QB": "#FF6B6B",
    "RB": "#4ECDC4",
    "WR": "#45B7D1",
    "TE": "#96CEB4",
}

POSITIONS = ["QB", "RB", "WR", "TE"]


# ---------------------------------------------------------------------------
# Sleeper API helpers (cached)
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


@st.cache_data(ttl=3600, show_spinner="Downloading NFL player list (~5 MB)…")
def fetch_nfl_players() -> dict:
    try:
        return SleeperClient().get_nfl_players()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Parse.bot helpers (cached)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=1800, show_spinner="Fetching player values from trusted sources…")
def fetch_player_values(api_key: str) -> dict[str, list[PlayerValue]]:
    client = ParseBotClient(api_key=api_key)
    return client.get_all_player_values()


@st.cache_data(ttl=1800)
def fetch_consensus_values(api_key: str) -> dict[str, float]:
    values_by_source = fetch_player_values(api_key)
    client = ParseBotClient(api_key=api_key)
    return client.get_consensus_values(values_by_source)


# ---------------------------------------------------------------------------
# Data-building helpers
# ---------------------------------------------------------------------------


def build_player_lookup(nfl_players: dict) -> dict[str, dict]:
    """Return {player_id: {full_name, position, team, age}} for skill players."""
    skill_positions = {"QB", "RB", "WR", "TE"}
    return {
        pid: {
            "name": p.get("full_name", ""),
            "position": p.get("position", ""),
            "team": p.get("team", "") or "FA",
            "age": p.get("age"),
        }
        for pid, p in nfl_players.items()
        if p.get("position") in skill_positions and p.get("full_name")
    }


def build_user_lookup(users: list[dict]) -> dict[str, dict]:
    """Return {roster_id: {display_name, team_name}}."""
    return {
        str(u["user_id"]): {
            "display_name": u.get("display_name", "Unknown"),
            "team_name": u.get("metadata", {}).get("team_name")
            or u.get("display_name", "Unknown"),
        }
        for u in users
    }


def roster_to_assets(
    roster: dict,
    player_lookup: dict[str, dict],
    consensus_values: dict[str, float],
) -> list[TradeAsset]:
    """Convert a Sleeper roster dict to a list of :class:`TradeAsset`."""
    assets: list[TradeAsset] = []
    for pid in roster.get("players") or []:
        player = player_lookup.get(pid)
        if not player or not player["name"]:
            continue
        key = normalize_name(player["name"])
        val = consensus_values.get(key, 0.0)
        assets.append(
            TradeAsset(
                name=player["name"],
                position=player["position"],
                team=player["team"],
                age=player.get("age"),
                value=val,
            )
        )
    return assets


def pick_value(year_offset: int, round_num: int) -> float:
    """Estimate consensus value for a future draft pick."""
    key = (round_num, min(year_offset + 1, 3))
    return PICK_VALUES.get(key, 1.0)


# ---------------------------------------------------------------------------
# Sidebar — setup
# ---------------------------------------------------------------------------


def render_sidebar() -> dict:
    """Render the sidebar and return collected configuration."""
    st.sidebar.title("🏈 Dynasty Trade Finder")
    st.sidebar.markdown("---")

    # Parse.bot API key
    api_key = st.sidebar.text_input(
        "Parse.bot API key",
        value=st.secrets.get("parse_bot", {}).get("api_key", ""),
        type="password",
        help="Required to fetch player values from KTC and FantasyCalc.",
    )

    st.sidebar.markdown("---")

    # Sleeper username
    username = st.sidebar.text_input(
        "Sleeper username",
        help="Your Sleeper username (not display name).",
    )
    season = st.sidebar.selectbox(
        "Season",
        options=[str(y) for y in range(int(CURRENT_SEASON), int(CURRENT_SEASON) - 3, -1)],
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
                    lg["league_id"]: lg.get("name", lg["league_id"])
                    for lg in leagues
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


# ---------------------------------------------------------------------------
# Tab 1 — League overview
# ---------------------------------------------------------------------------


def render_league_overview(
    league_data: dict,
    user_lookup: dict,
    player_lookup: dict,
    consensus_values: dict[str, float],
) -> None:
    st.header("🏟️ League Overview")
    rosters = league_data["rosters"]
    league = league_data["league"]

    st.markdown(f"**League:** {league.get('name')} &nbsp;|&nbsp; "
                f"**Season:** {league.get('season')} &nbsp;|&nbsp; "
                f"**Teams:** {league.get('total_rosters')}")

    rows = []
    for roster in rosters:
        uid = str(roster.get("owner_id", ""))
        user = user_lookup.get(uid, {})
        team_name = user.get("team_name", user.get("display_name", f"Team {roster['roster_id']}"))
        settings = roster.get("settings") or {}
        wins = settings.get("wins", 0)
        losses = settings.get("losses", 0)
        pts = settings.get("fpts", 0)

        assets = roster_to_assets(roster, player_lookup, consensus_values)
        roster_value = sum(a.value for a in assets)
        pos_counts = {}
        for a in assets:
            pos_counts[a.position] = pos_counts.get(a.position, 0) + 1

        rows.append(
            {
                "Team": team_name,
                "W": wins,
                "L": losses,
                "PF": pts,
                "Roster Value": round(roster_value, 1),
                "QB": pos_counts.get("QB", 0),
                "RB": pos_counts.get("RB", 0),
                "WR": pos_counts.get("WR", 0),
                "TE": pos_counts.get("TE", 0),
                "Total": len(assets),
            }
        )

    df = pd.DataFrame(rows).sort_values("Roster Value", ascending=False).reset_index(drop=True)
    st.dataframe(df, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab 2 — My roster
# ---------------------------------------------------------------------------


def render_my_roster(
    my_roster_raw: dict,
    player_lookup: dict,
    consensus_values: dict[str, float],
    calculator: TradeCalculator,
) -> list[TradeAsset]:
    st.header("📋 My Roster")
    assets = roster_to_assets(my_roster_raw, player_lookup, consensus_values)

    if not assets:
        st.info("No skill-position players found on your roster.")
        return []

    need = calculator.calculate_positional_need(assets, SUPERFLEX_STARTER_SLOTS)

    col1, col2 = st.columns([3, 1])

    with col1:
        rows = [
            {
                "Player": a.name,
                "Pos": a.position,
                "Team": a.team,
                "Age": a.age or "—",
                "Value": round(a.value, 1),
            }
            for a in sorted(assets, key=lambda x: x.value, reverse=True)
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col2:
        st.subheader("Positional Needs")
        for pos in POSITIONS:
            n = need.get(pos, 0)
            bar = "🟩" * min(n, 5) + "⬜" * max(0, 5 - n)
            st.markdown(f"**{pos}** {bar} ({n})")

    return assets


# ---------------------------------------------------------------------------
# Tab 3 — Trade explorer
# ---------------------------------------------------------------------------


def render_trade_explorer(
    my_assets: list[TradeAsset],
    all_rosters: dict[str, list[TradeAsset]],
    calculator: TradeCalculator,
    positional_need: dict[str, int],
) -> None:
    st.header("🔄 Trade Explorer")
    st.markdown("Build a trade manually and see how it scores.")

    all_other_players = [a for assets in all_rosters.values() for a in assets]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("I give…")
        giving_names = st.multiselect(
            "Select players from your roster",
            options=[a.name for a in sorted(my_assets, key=lambda x: x.value, reverse=True)],
            key="giving",
        )
        giving = [a for a in my_assets if a.name in giving_names]

    with col2:
        st.subheader("I receive…")
        receiving_names = st.multiselect(
            "Select players from other teams",
            options=[a.name for a in sorted(all_other_players, key=lambda x: x.value, reverse=True)],
            key="receiving",
        )
        receiving = [a for a in all_other_players if a.name in receiving_names]

    if giving and receiving:
        result = calculator.calculate_trade_value(giving, receiving, positional_need)
        _render_trade_result(result)
    else:
        st.info("Select at least one player on each side to evaluate a trade.")


def _render_trade_result(result: TradeResult) -> None:
    grade_colors = {
        "A+": "green", "A": "green", "B+": "green",
        "B": "orange",
        "C+": "red", "C": "red", "D": "red", "F": "red",
    }
    color = grade_colors.get(result.grade, "grey")

    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Giving value", f"{result.giving_value:.1f}")
    c2.metric("Receiving value", f"{result.receiving_value:.1f}")
    delta_label = f"+{result.value_delta:.1f}" if result.value_delta >= 0 else f"{result.value_delta:.1f}"
    c3.metric("Value delta", delta_label)
    c4.markdown(
        f"<h2 style='color:{color}; text-align:center'>{result.grade}</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(f"**{result.summary}**")


# ---------------------------------------------------------------------------
# Tab 4 — Best trades
# ---------------------------------------------------------------------------


def render_best_trades(
    my_assets: list[TradeAsset],
    all_rosters: dict[str, list[TradeAsset]],
    consensus_values: dict[str, float],
    positional_need: dict[str, int],
    analyzer: TradeAnalyzer,
) -> None:
    st.header("⚡ Best Trades")
    st.markdown(
        "Automatically calculated trade proposals that improve your team's value. "
        "Proposals are ranked by value gain, adjusted for positional need."
    )

    with st.spinner("Analysing trade opportunities…"):
        proposals = analyzer.find_best_trades(
            my_roster=my_assets,
            all_rosters=all_rosters,
            player_values=consensus_values,
            positional_need=positional_need,
            max_assets_per_side=2,
            top_n=25,
        )

    if not proposals:
        st.info("No favourable trades found. Try refreshing player values.")
        return

    pos_filter = st.multiselect(
        "Filter by position received",
        options=POSITIONS,
        default=POSITIONS,
        key="best_trades_pos_filter",
    )

    filtered = [
        p for p in proposals
        if any(a.position in pos_filter for a in p.receiving)
    ]

    rows = []
    for p in filtered:
        giving_str = " + ".join(a.display_name for a in p.giving)
        receiving_str = " + ".join(a.display_name for a in p.receiving)
        rows.append(
            {
                "Give": giving_str,
                "Receive": receiving_str,
                "From": p.their_team,
                "Give Value": round(p.result.giving_value, 1),
                "Receive Value": round(p.result.receiving_value, 1),
                "Delta": round(p.result.value_delta, 1),
                "Grade": p.result.grade,
            }
        )

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No proposals match the selected position filter.")


# ---------------------------------------------------------------------------
# Tab 5 — Arbitrage
# ---------------------------------------------------------------------------


def render_arbitrage(
    values_by_source: dict[str, list[PlayerValue]],
    analyzer: TradeAnalyzer,
) -> None:
    st.header("📊 Arbitrage Opportunities")
    st.markdown(
        "Players where **KTC** and **FantasyCalc** disagree significantly.  "
        "**Buy** candidates are undervalued by one source — try to acquire them.  "
        "**Sell** candidates are overvalued by one source — consider moving them."
    )

    # Convert PlayerValue lists to {source: {name: normalised_value}} dicts
    normalised: dict[str, dict[str, float]] = {}
    for source, players in values_by_source.items():
        if not players:
            continue
        max_val = max(p.value for p in players)
        normalised[source] = {
            normalize_name(p.name): (p.value / max_val * 100) if max_val > 0 else 0
            for p in players
        }

    threshold = st.slider(
        "Minimum spread threshold (%)",
        min_value=10,
        max_value=50,
        value=20,
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
    for o in opps:
        source_cols = {
            f"{src} value": round(val, 1)
            for src, val in o.values_by_source.items()
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

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No opportunities match the current filter.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    cfg = render_sidebar()

    api_key: str = cfg["api_key"]
    league_id: Optional[str] = cfg["league_id"]
    user_data: Optional[dict] = cfg["user_data"]

    if not user_data or not league_id:
        st.title("🏈 Dynasty Trade Finder")
        st.markdown(
            """
Welcome to **Dynasty Trade Finder** — an arbitrage-focused trade analysis tool
for Dynasty Superflex PPR fantasy football.

### Getting started
1. Enter your **Sleeper username** in the sidebar.
2. Select your **season** and **league**.
3. *(Optional)* Add your **Parse.bot API key** to unlock live player values
   from KeepTradeCut and FantasyCalc.

### Features
| Tab | Description |
|-----|-------------|
| 🏟️ League Overview | Roster values and positional depth for every team |
| 📋 My Roster | Your players ranked by consensus value with need assessment |
| 🔄 Trade Explorer | Build trades manually and get an instant grade |
| ⚡ Best Trades | Auto-generated favourable trade proposals |
| 📊 Arbitrage | Players where KTC and FantasyCalc disagree — buy low / sell high |
            """
        )
        return

    # ---- Load league data ----
    with st.spinner("Loading league data…"):
        league_data = fetch_league_data(league_id)
        nfl_players = fetch_nfl_players()

    player_lookup = build_player_lookup(nfl_players)
    user_lookup = build_user_lookup(league_data["users"])
    rosters = league_data["rosters"]

    # Identify my roster (owner_id matches logged-in user)
    my_user_id = user_data["user_id"]
    my_roster_raw = next(
        (r for r in rosters if str(r.get("owner_id")) == str(my_user_id)),
        rosters[0] if rosters else {},
    )

    # ---- Load player values ----
    consensus_values: dict[str, float] = {}
    values_by_source: dict[str, list[PlayerValue]] = {}

    if api_key:
        try:
            values_by_source = fetch_player_values(api_key)
            consensus_values = fetch_consensus_values(api_key)
        except Exception as exc:
            st.warning(f"Could not fetch player values: {exc}")
    else:
        st.info(
            "Add a Parse.bot API key in the sidebar to see live player values "
            "from KTC and FantasyCalc.  Trades will still be evaluated, but "
            "all values will show as 0."
        )

    # ---- Build roster assets ----
    my_assets = roster_to_assets(my_roster_raw, player_lookup, consensus_values)
    calculator = TradeCalculator()
    positional_need = calculator.calculate_positional_need(
        my_assets, SUPERFLEX_STARTER_SLOTS
    )
    analyzer = TradeAnalyzer(calculator)

    # Other teams' rosters as {team_name: [TradeAsset]}
    all_rosters: dict[str, list[TradeAsset]] = {}
    for roster in rosters:
        if str(roster.get("owner_id")) == str(my_user_id):
            continue
        uid = str(roster.get("owner_id", ""))
        team = user_lookup.get(uid, {}).get(
            "team_name", f"Team {roster['roster_id']}"
        )
        all_rosters[team] = roster_to_assets(roster, player_lookup, consensus_values)

    # ---- Tabs ----
    tabs = st.tabs(
        ["🏟️ League Overview", "📋 My Roster", "🔄 Trade Explorer", "⚡ Best Trades", "📊 Arbitrage"]
    )

    with tabs[0]:
        render_league_overview(league_data, user_lookup, player_lookup, consensus_values)

    with tabs[1]:
        render_my_roster(my_roster_raw, player_lookup, consensus_values, calculator)

    with tabs[2]:
        render_trade_explorer(my_assets, all_rosters, calculator, positional_need)

    with tabs[3]:
        render_best_trades(my_assets, all_rosters, consensus_values, positional_need, analyzer)

    with tabs[4]:
        render_arbitrage(values_by_source, analyzer)


if __name__ == "__main__":
    main()
