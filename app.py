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

from datetime import datetime, timezone
from typing import Optional

import requests
import streamlit as st

from src.config import (
    get_parse_api_key,
    get_sportsdata_api_key,
    set_parse_api_key_env,
)
from src.data_providers import (
    FantasyCalcProvider,
    NormalizedPlayerValue,
    fetch_all_sources,
)
from src import nfl_stats
from src.name_matching import normalize_name
from src.nfl_stats import PlayerPerformance, production_signal_map
from src.player_detail import build_perf_index, build_player_detail, lookup_player
from src.sportsdata_client import SportsDataClient
from src.sportsdata_provider import (
    SportsDataProjection,
    parse_player_news,
    parse_season_projections,
    parse_season_stats,
)
from src.db import DtfStore, get_engine
from src.db.models import UserPreferences
from src.db.store import apply_weights_to_prefs, weights_from_prefs
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
    PlayerRef,
    build_name_map,
    build_player_lookup,
    build_user_lookup,
    roster_valuations,
    team_names_by_roster,
)
from src.ui.player_modal import open_player_detail_dialog
from src.ui.sidebar import (
    render_connection_sidebar,
    render_diagnostics,
    render_format_panel,
    render_value_model,
)
from src.ui.tabs_arbitrage import render_arbitrage
from src.ui.tabs_league import render_league_overview
from src.ui.tabs_performance import render_performance
from src.ui.tabs_roster import render_my_roster
from src.ui.tabs_targets import render_trade_targets
from src.ui.tabs_trades import render_best_trades, render_trade_explorer
from src.ui.tabs_trends import render_trends
from src.value_engine import ValueEngine, ValueWeights

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
# Persistence — one DtfStore over data/dtf.db (cache-until-manual-refresh)
# ---------------------------------------------------------------------------


@st.cache_resource
def get_store() -> DtfStore:
    return DtfStore(get_engine())


def _should_force(kind: str) -> bool:
    """True when the user requested a refresh of *kind* this run."""
    forced = st.session_state.get("force_refresh", set())
    return "all" in forced or kind in forced


# --- Raw API fetchers (only called on a cache miss or forced refresh) ---


def _api_user(username: str) -> Optional[dict]:
    try:
        return SleeperClient().get_user(username)
    except requests.HTTPError:
        return None


def _api_leagues(user_id: str, season: str) -> list[dict]:
    try:
        return SleeperClient().get_user_leagues(user_id, season) or []
    except requests.HTTPError:
        return []


def _api_league_data(league_id: str) -> dict:
    client = SleeperClient()
    return {
        "league": client.get_league(league_id),
        "rosters": client.get_league_rosters(league_id),
        "users": client.get_league_users(league_id),
    }


def _api_draft_data(league_id: str) -> dict:
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


def _api_nfl_players() -> dict:
    try:
        return SleeperClient().get_nfl_players()
    except Exception:
        return {}


# --- DB-backed fetchers (return cached data unless forced) ---


def fetch_user(username: str) -> Optional[dict]:
    return get_store().get_or_fetch_user(
        username, lambda: _api_user(username), force=_should_force("league")
    )


def fetch_leagues(user_id: str, season: str) -> list[dict]:
    return get_store().get_or_fetch_leagues(
        user_id,
        season,
        lambda: _api_leagues(user_id, season),
        force=_should_force("league"),
    )


def fetch_league_data(league_id: str) -> dict:
    return get_store().get_or_fetch_league_data(
        league_id, lambda: _api_league_data(league_id), force=_should_force("league")
    )


def fetch_draft_data(league_id: str) -> dict:
    return get_store().get_or_fetch_draft_data(
        league_id, lambda: _api_draft_data(league_id), force=_should_force("league")
    )


@st.cache_data(show_spinner="Loading NFL player list…")
def _load_nfl_players(refresh_token: int, force: bool) -> dict:
    # cache_data memo (the ~5 MB dump) keyed by refresh_token; the DB gates
    # the actual API call.
    return get_store().get_or_fetch_nfl_players(_api_nfl_players, force=force)


def fetch_nfl_players() -> dict:
    return _load_nfl_players(
        st.session_state.get("refresh_token", 0), _should_force("league")
    )


def fetch_source_values(
    fmt: LeagueFormat, api_key: str
) -> tuple[dict[str, list[NormalizedPlayerValue]], dict[str, str]]:
    return get_store().get_or_fetch_source_values(
        fmt.cache_key(),
        lambda: fetch_all_sources(fmt, api_key=api_key or None),
        force=_should_force("values"),
    )


def fetch_player_history(
    fc_player_id: str, fmt: LeagueFormat, api_key: str
) -> list[tuple[str, float]]:
    return get_store().get_or_fetch_trade_history(
        fc_player_id,
        fmt.cache_key(),
        lambda: FantasyCalcProvider(api_key=api_key or None).fetch_trade_history(
            fc_player_id, fmt
        ),
        force=False,
    )


# --- Performance data (nflverse realized + SportsDataIO projections) ---


def fetch_nfl_perf(season: int) -> list[PlayerPerformance]:
    """Validated realized season stats from nflverse (cached by season)."""
    raw = get_store().get_or_fetch_stat(
        "nfl_perf",
        season,
        lambda: [p.model_dump() for p in nfl_stats.fetch_season_stats(season)],
        force=_should_force("stats"),
    )
    return [PlayerPerformance(**d) for d in raw]


def fetch_nfl_weekly(season: int) -> list[dict]:
    return get_store().get_or_fetch_stat(
        "nfl_weekly",
        season,
        lambda: nfl_stats.fetch_weekly_stats(season),
        force=_should_force("stats"),
    )


def fetch_sdio_season_stats(season: int, api_key: str):
    """SportsDataIO realized season stats (empty list if no key / on error)."""
    if not api_key:
        return []
    try:
        raw = get_store().get_or_fetch_stat(
            "sdio_season",
            season,
            lambda: SportsDataClient(api_key).get_player_season_stats(season),
            force=_should_force("stats"),
        )
    except Exception as err:  # network/auth — degrade to nflverse only
        st.sidebar.warning(f"SportsDataIO stats unavailable: {err}")
        return []
    return parse_season_stats(raw)


def fetch_sdio_projections(season: int, api_key: str) -> list[SportsDataProjection]:
    if not api_key:
        return []
    try:
        raw = get_store().get_or_fetch_stat(
            "sdio_proj",
            season,
            lambda: SportsDataClient(api_key).get_player_season_projections(season),
            force=_should_force("stats"),
        )
    except Exception as err:
        st.sidebar.warning(f"SportsDataIO projections unavailable: {err}")
        return []
    return parse_season_projections(raw)


def fetch_player_news(fantasy_data_id: str):
    """Recent SportsDataIO news for a player (keyed by PlayerID == fantasy_data_id).

    Returns an empty list when there's no key, the free tier excludes the news
    endpoint, or the call fails — the modal degrades gracefully.
    """
    key = get_sportsdata_api_key() or ""
    if not key or not fantasy_data_id:
        return []
    try:
        raw = get_store().get_or_fetch_stat(
            "sdio_news",
            fantasy_data_id,
            lambda: SportsDataClient(key).get_player_news_by_id(fantasy_data_id),
            force=_should_force("stats"),
        )
    except Exception:  # free-tier 403 / network / auth — degrade to no news
        return []
    return parse_player_news(raw)


# ---------------------------------------------------------------------------
# Preferences (persisted per Sleeper username)
# ---------------------------------------------------------------------------

_PREF_WIDGET_KEYS = (
    "w_fantasycalc", "w_ktc", "w_draftsharks", "w_age", "w_trend", "w_injury",
    "w_adp", "w_production", "fmt_override", "pref_num_qbs", "pref_ppr",
    "pref_te_premium", "pref_fairness", "pref_include_picks", "pref_mutual_only",
    "pref_timeline_fit", "arb_threshold",
)


def _reset_pref_widgets_on_user_change(username: str) -> None:
    """Clear preference widget state when the active user changes so the new
    user's saved preferences seed the widgets via their ``value=`` defaults."""
    if st.session_state.get("_prefs_user") != username:
        for key in _PREF_WIDGET_KEYS:
            st.session_state.pop(key, None)
        st.session_state["_prefs_user"] = username


def _collect_preferences(username: str) -> UserPreferences:
    """Build a UserPreferences from the current widget state."""
    ss = st.session_state
    weights = ValueWeights(
        source_weights=(
            ("fantasycalc", float(ss.get("w_fantasycalc", 0.5))),
            ("ktc", float(ss.get("w_ktc", 0.3))),
            ("draftsharks", float(ss.get("w_draftsharks", 0.2))),
        ),
        age_weight=float(ss.get("w_age", 0.5)),
        trend_weight=float(ss.get("w_trend", 0.0)),
        injury_weight=float(ss.get("w_injury", 0.0)),
        adp_divergence_weight=float(ss.get("w_adp", 0.0)),
        production_weight=float(ss.get("w_production", 0.0)),
    )
    prefs = UserPreferences(
        username=username,
        override_enabled=bool(ss.get("fmt_override", False)),
        num_qbs=ss.get("pref_num_qbs"),
        ppr=ss.get("pref_ppr"),
        te_premium=ss.get("pref_te_premium"),
        fairness_weight=ss.get("pref_fairness", 50) / 100.0,
        include_picks=bool(ss.get("pref_include_picks", True)),
        mutual_only=bool(ss.get("pref_mutual_only", True)),
        timeline_fit=bool(ss.get("pref_timeline_fit", True)),
        arb_threshold=ss.get("arb_threshold", 20) / 100.0,
    )
    apply_weights_to_prefs(prefs, weights)
    return prefs


def _save_preferences_if_changed(
    store: DtfStore, username: str, loaded: UserPreferences
) -> None:
    if not username:
        return
    current = _collect_preferences(username)
    fields = set(current.model_dump()) - {"updated_at"}
    changed = any(getattr(current, f) != getattr(loaded, f) for f in fields)
    if changed:
        store.save_preferences(current)


def render_refresh_controls() -> None:
    """Sidebar buttons that force a one-shot refetch of cached data."""
    st.session_state.setdefault("refresh_token", 0)
    st.sidebar.markdown("---")
    col1, col2, col3 = st.sidebar.columns(3)
    if col1.button("🔄 Values", help="Refetch player values from the sources"):
        st.session_state["force_refresh"] = {"values"}
        st.session_state["refresh_token"] += 1
        st.rerun()
    if col2.button("🔄 League", help="Refetch Sleeper league, rosters, and picks"):
        st.session_state["force_refresh"] = {"league"}
        st.session_state["refresh_token"] += 1
        st.rerun()
    if col3.button("🔄 Stats", help="Refetch nflverse + SportsDataIO performance data"):
        st.session_state["force_refresh"] = {"stats"}
        st.session_state["refresh_token"] += 1
        st.rerun()


def _staleness(store: DtfStore, kind: str, key: str) -> str:
    """Short 'cached <ago>' string for a resource, or ''."""
    ts = store.last_fetched(kind, key)
    if ts is None:
        return ""
    # SQLite drops tzinfo, so a stored UTC timestamp reads back naive.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = max(0, int(delta.total_seconds()))
    if secs < 3600:
        ago = f"{secs // 60}m"
    elif secs < 86400:
        ago = f"{secs // 3600}h"
    else:
        ago = f"{secs // 86400}d"
    return f"cached {ago} ago"


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
    store = get_store()
    render_refresh_controls()
    cfg = render_connection_sidebar(
        fetch_user, fetch_leagues, get_parse_api_key() or "", CURRENT_SEASON
    )

    user_data: Optional[dict] = cfg["user_data"]
    league_id: Optional[str] = cfg["league_id"]
    api_key: str = cfg["api_key"]
    username: str = cfg["username"]

    prefs = (
        store.load_preferences(username)
        if username
        else UserPreferences(username="")
    )
    _reset_pref_widgets_on_user_change(username)

    if not user_data or not league_id:
        render_welcome()
        st.session_state.pop("force_refresh", None)
        return

    # ---- League data ----
    with st.spinner("Fetching league, rosters & players…"):
        league_data = fetch_league_data(league_id)
        nfl_players = fetch_nfl_players()
    league = league_data["league"]
    rosters = league_data["rosters"]

    fmt = render_format_panel(detect_league_format(league), prefs)
    weights = render_value_model(weights_from_prefs(prefs))

    # ---- Performance data (nflverse realized production) ----
    # Fetched before blending so the value model's production signal is ready.
    sdio_key = get_sportsdata_api_key() or ""
    stats_season_guess = int(league.get("season", CURRENT_SEASON) or CURRENT_SEASON)
    with st.spinner("Loading performance data…"):
        try:
            nfl_perf = fetch_nfl_perf(stats_season_guess)
        except Exception as err:  # nflverse download failure — degrade gracefully
            st.sidebar.warning(f"nflverse performance data unavailable: {err}")
            nfl_perf = []
    stats_season = nfl_perf[0].season if nfl_perf else stats_season_guess
    production = production_signal_map(nfl_perf)

    # ---- Player values ----
    if api_key:
        set_parse_api_key_env(api_key)
    with st.spinner("Loading player values…"):
        sources, source_errors = fetch_source_values(fmt, api_key)
        engine = ValueEngine(weights, production=production)
        valuations = engine.blend(sources)
    caption = _staleness(store, "source", fmt.cache_key())
    if caption:
        st.sidebar.caption(f"Player values {caption}")

    if not valuations:
        st.warning(
            "No player values available. Add a Parse API key in the sidebar "
            "(or run `uv run parse login` / `uv run parse sync`). Rosters "
            "will show with zero values."
        )

    with st.spinner("Building rosters & trade engine…"):
        # One local snapshot per day powers the Trends tab
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
        # Valued (asset, valuation) pairs incl. picks, for the roster tables in
        # the Trade Explorer. Picks carry no valuation, so pair them with None.
        my_pairs = list(roster_pairs.get(my_rid, [])) + [
            (p, None) for p in my_picks
        ]

        headcount_need = calculator.calculate_positional_need(my_players, fmt.slots_dict())
        vor_need = None
        if valuations:
            baselines = replacement_baselines(valuations, fmt)
            vor_need = value_based_need(my_players, baselines, fmt.slots_dict())

        all_rosters: dict[str, list] = {}
        counterparty_needs: dict[str, dict] = {}
        their_pairs_by_team: dict[str, list] = {}
        for roster in rosters:
            rid = roster.get("roster_id")
            if rid is None or rid == my_rid:
                continue
            team = team_names.get(rid, f"Team {rid}")
            all_rosters[team] = roster_assets.get(rid, []) + picks_by_roster.get(rid, [])
            their_pairs_by_team[team] = list(roster_pairs.get(rid, [])) + [
                (p, None) for p in picks_by_roster.get(rid, [])
            ]
            counterparty_needs[team] = calculator.calculate_positional_need(
                roster_assets.get(rid, []), fmt.slots_dict()
            )

        market_insights = find_buy_low_sell_high(valuations, store.get_deltas(30))

    # ---- Performance staleness caption (production-signal season) ----
    # The Performance tab fetches its own (independently selectable) season;
    # this caption reflects the season feeding the value model's production
    # signal above.
    if nfl_perf:
        caption = _staleness(store, "stat", f"nfl_perf/{stats_season_guess}")
        if caption:
            st.sidebar.caption(f"Performance {caption}")

    # ---- Player-detail modal wiring ----
    # Indexes for the click-a-player pop-up: nflverse production by id/name, and
    # league ownership (team + timeline) by id/name across every roster incl. mine.
    perf_index = build_perf_index(nfl_perf)
    owner_index: dict[str, tuple[str, str]] = {}
    for rid, pairs in roster_pairs.items():
        team = team_names.get(rid, f"Team {rid}")
        profile = profiles.get(rid)
        timeline = profile.classification if profile else ""
        for asset, valuation in pairs:
            owner_index.setdefault(
                f"name:{normalize_name(asset.name)}", (team, timeline)
            )
            if valuation and valuation.sleeper_id:
                owner_index.setdefault(valuation.sleeper_id, (team, timeline))

    def open_player_detail(ref: PlayerRef) -> None:
        valuation = None
        if ref.sleeper_id and ref.sleeper_id in sleeper_map:
            valuation = sleeper_map[ref.sleeper_id]
        if valuation is None:
            valuation = name_map.get(normalize_name(ref.name))
        sid = ref.sleeper_id or (valuation.sleeper_id if valuation else None)
        owner = lookup_player(owner_index, sid, ref.name)
        detail = build_player_detail(
            name=ref.name,
            position=ref.position,
            sleeper_id=sid,
            valuation=valuation,
            perf=lookup_player(perf_index, sid, ref.name),
            owner=owner[0] if owner else None,
            owner_timeline=owner[1] if owner else None,
        )
        open_player_detail_dialog(
            detail,
            fetch_weekly=fetch_nfl_weekly,
            fetch_local_history=store.get_history,
            fetch_market_history=lambda pid: fetch_player_history(pid, fmt, api_key),
            fetch_news=fetch_player_news,
        )

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
            "🏈 Performance",
        ]
    )

    with tabs[0]:
        render_league_overview(league, profiles, roster_assets)
    with tabs[1]:
        render_my_roster(
            roster_pairs.get(my_rid, []), my_picks, headcount_need, vor_need,
            on_select_player=open_player_detail,
        )
    with tabs[2]:
        render_trade_explorer(
            my_pairs, their_pairs_by_team, calculator, headcount_need,
            counterparty_needs, valuations,
        )
    with tabs[3]:
        render_best_trades(
            my_assets, all_rosters, name_values, headcount_need, analyzer,
            counterparty_needs, profiles_by_team, my_classification, prefs,
            on_select_player=open_player_detail,
        )
    with tabs[4]:
        render_trade_targets(
            my_assets, all_rosters, name_values, headcount_need, analyzer, name_map,
            on_select_player=open_player_detail,
        )
    with tabs[5]:
        render_arbitrage(
            sources, analyzer, prefs, on_select_player=open_player_detail
        )
    with tabs[6]:
        render_trends(
            valuations,
            store,
            market_insights,
            fetch_history=lambda pid: fetch_player_history(pid, fmt, api_key),
            on_select_player=open_player_detail,
        )
    with tabs[7]:
        render_performance(
            fetch_perf=fetch_nfl_perf,
            fetch_weekly=fetch_nfl_weekly,
            fetch_sdio_stats=lambda season: fetch_sdio_season_stats(season, sdio_key),
            fetch_projections=lambda season: fetch_sdio_projections(season, sdio_key),
            default_season=stats_season,
            staleness=lambda season: _staleness(store, "stat", f"nfl_perf/{season}"),
        )

    # Persist any preference changes and clear the one-shot refresh flag.
    _save_preferences_if_changed(store, username, prefs)
    st.session_state.pop("force_refresh", None)


if __name__ == "__main__":
    main()
