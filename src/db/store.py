"""High-level persistence API over the SQLModel tables.

Implements *cache-until-manual-refresh*: every ``get_or_fetch_*`` returns
cached data when present and only calls the supplied ``fetcher`` (which makes
the real API call) on a cache miss or when ``force=True``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import Engine, delete, func, inspect, text
from sqlmodel import Session, select

from ..data_providers import NormalizedPlayerValue
from ..value_engine import (
    DEFAULT_SOURCE_WEIGHTS,
    PlayerValuation,
    ValueWeights,
)
from . import models as m

_NPV_FIELDS = list(NormalizedPlayerValue.model_fields.keys())


def _row_to_npv(row: m._PlayerValueRow) -> NormalizedPlayerValue:
    return NormalizedPlayerValue(**{f: getattr(row, f) for f in _NPV_FIELDS})


def _scalar_default(col, dialect_name: str = "sqlite") -> Optional[str]:
    """SQL literal for a column's scalar Python default, or None.

    Used by the additive column migration; factory/callable defaults (e.g.
    ``utcnow``) return None so the added column is simply nullable. Booleans
    are rendered per-dialect (``TRUE``/``FALSE`` on Postgres, ``1``/``0`` on
    SQLite, which has no native boolean).
    """
    default = col.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):  # must precede int — bool is an int subclass
        if dialect_name == "sqlite":
            return "1" if value else "0"
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


class DtfStore:
    """Durable cache + preferences + value-history store."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        m.SQLModel.metadata.create_all(engine)
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        """Add model columns missing from existing tables.

        ``create_all`` creates missing *tables* but never alters existing ones,
        so a new field on an existing model (e.g. ``production_weight``) would
        raise "no such column" against an older ``data/dtf.db``. This adds any
        such columns in place (nullable, with the model's scalar default) —
        additive and lossless. New columns must carry a scalar default.
        """
        inspector = inspect(self.engine)
        existing = set(inspector.get_table_names())
        with self.engine.begin() as conn:
            for table in m.SQLModel.metadata.tables.values():
                if table.name not in existing:
                    continue
                have = {c["name"] for c in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    type_sql = col.type.compile(dialect=self.engine.dialect)
                    ddl = f'ADD COLUMN "{col.name}" {type_sql}'
                    default = _scalar_default(col, self.engine.dialect.name)
                    if default is not None:
                        ddl += f" DEFAULT {default}"
                    conn.execute(text(f'ALTER TABLE "{table.name}" {ddl}'))

    def _session(self) -> Session:
        return Session(self.engine)

    # ------------------------------------------------------------------
    # Sleeper / league information
    # ------------------------------------------------------------------

    def get_or_fetch_user(
        self, username: str, fetcher: Callable[[], Optional[dict]], force: bool = False
    ) -> Optional[dict]:
        with self._session() as s:
            row = s.get(m.SleeperUserCache, username)
            if row is not None and not force:
                return json.loads(row.payload_json)
        data = fetcher()
        if data is None:
            return None
        with self._session() as s:
            s.merge(
                m.SleeperUserCache(
                    username=username,
                    user_id=str(data.get("user_id", "")),
                    payload_json=json.dumps(data),
                    fetched_at=m.utcnow(),
                )
            )
            s.commit()
        return data

    def get_or_fetch_leagues(
        self,
        user_id: str,
        season: str,
        fetcher: Callable[[], list],
        force: bool = False,
    ) -> list:
        key = f"{user_id}/{season}"
        with self._session() as s:
            row = s.get(m.SleeperLeaguesCache, key)
            if row is not None and not force:
                return json.loads(row.payload_json)
        data = fetcher() or []
        with self._session() as s:
            s.merge(
                m.SleeperLeaguesCache(
                    key=key, payload_json=json.dumps(data), fetched_at=m.utcnow()
                )
            )
            s.commit()
        return data

    def get_or_fetch_league_data(
        self, league_id: str, fetcher: Callable[[], dict], force: bool = False
    ) -> dict:
        with self._session() as s:
            row = s.get(m.LeagueDataCache, league_id)
            if row is not None and not force:
                return {
                    "league": json.loads(row.league_json),
                    "rosters": json.loads(row.rosters_json),
                    "users": json.loads(row.users_json),
                }
        data = fetcher()
        with self._session() as s:
            s.merge(
                m.LeagueDataCache(
                    league_id=league_id,
                    league_json=json.dumps(data.get("league")),
                    rosters_json=json.dumps(data.get("rosters", [])),
                    users_json=json.dumps(data.get("users", [])),
                    fetched_at=m.utcnow(),
                )
            )
            s.commit()
        return data

    def get_or_fetch_draft_data(
        self, league_id: str, fetcher: Callable[[], dict], force: bool = False
    ) -> dict:
        with self._session() as s:
            row = s.get(m.DraftDataCache, league_id)
            if row is not None and not force:
                return {
                    "drafts": json.loads(row.drafts_json),
                    "traded_picks": json.loads(row.traded_picks_json),
                }
        data = fetcher()
        with self._session() as s:
            s.merge(
                m.DraftDataCache(
                    league_id=league_id,
                    drafts_json=json.dumps(data.get("drafts", [])),
                    traded_picks_json=json.dumps(data.get("traded_picks", [])),
                    fetched_at=m.utcnow(),
                )
            )
            s.commit()
        return data

    def get_or_fetch_nfl_players(
        self, fetcher: Callable[[], dict], force: bool = False
    ) -> dict:
        with self._session() as s:
            row = s.get(m.NflPlayersCache, 1)
            if row is not None and not force:
                return json.loads(row.payload_json)
        data = fetcher() or {}
        with self._session() as s:
            s.merge(
                m.NflPlayersCache(
                    id=1, payload_json=json.dumps(data), fetched_at=m.utcnow()
                )
            )
            s.commit()
        return data

    # ------------------------------------------------------------------
    # Performance / stat payloads (generic JSON cache keyed by kind/season)
    # ------------------------------------------------------------------

    def get_or_fetch_stat(
        self,
        kind: str,
        season: int | str,
        fetcher: Callable[[], list],
        force: bool = False,
    ) -> list:
        """Return a cached list-of-dicts payload for *kind*/*season*.

        The billed/slow *fetcher* runs only on a cache miss or ``force``.
        """
        key = f"{kind}/{season}"
        with self._session() as s:
            row = s.get(m.StatCache, key)
            if row is not None and not force:
                return json.loads(row.payload_json)
        data = fetcher() or []
        with self._session() as s:
            s.merge(
                m.StatCache(
                    key=key,
                    kind=kind,
                    payload_json=json.dumps(data),
                    fetched_at=m.utcnow(),
                )
            )
            s.commit()
        return data

    # ------------------------------------------------------------------
    # Parse value sources (one table per source)
    # ------------------------------------------------------------------

    def get_or_fetch_source_values(
        self,
        format_key: str,
        fetcher: Callable[[], tuple[dict, dict]],
        force: bool = False,
    ) -> tuple[dict[str, list[NormalizedPlayerValue]], dict[str, str]]:
        """Return ``({source: [NormalizedPlayerValue]}, {source: error})``.

        Cached whenever any source has been fetched for *format_key* before.
        """
        if not force and self._has_source_fetch(format_key):
            return self._load_source_values(format_key), self._load_source_errors(
                format_key
            )
        sources, errors = fetcher()
        self._replace_source_values(format_key, sources, errors)
        return sources, errors

    def _has_source_fetch(self, format_key: str) -> bool:
        with self._session() as s:
            row = s.exec(
                select(m.SourceFetch).where(m.SourceFetch.format_key == format_key)
            ).first()
        return row is not None

    def _load_source_values(
        self, format_key: str
    ) -> dict[str, list[NormalizedPlayerValue]]:
        out: dict[str, list[NormalizedPlayerValue]] = {}
        with self._session() as s:
            for source, table in m.SOURCE_TABLES.items():
                rows = s.exec(
                    select(table).where(table.format_key == format_key)
                ).all()
                if rows:
                    out[source] = [_row_to_npv(r) for r in rows]
        return out

    def _load_source_errors(self, format_key: str) -> dict[str, str]:
        with self._session() as s:
            rows = s.exec(
                select(m.SourceFetch).where(
                    m.SourceFetch.format_key == format_key,
                    m.SourceFetch.error.is_not(None),
                )
            ).all()
        return {r.source: r.error for r in rows if r.error}

    def _replace_source_values(
        self,
        format_key: str,
        sources: dict[str, list[NormalizedPlayerValue]],
        errors: dict[str, str],
    ) -> None:
        attempted = set(sources) | set(errors)
        with self._session() as s:
            for source, table in m.SOURCE_TABLES.items():
                s.exec(delete(table).where(table.format_key == format_key))
                for npv in sources.get(source, []):
                    s.add(table(format_key=format_key, **npv.model_dump()))
            for source in attempted:
                s.merge(
                    m.SourceFetch(
                        key=f"{source}/{format_key}",
                        source=source,
                        format_key=format_key,
                        player_count=len(sources.get(source, [])),
                        error=errors.get(source),
                        fetched_at=m.utcnow(),
                    )
                )
            s.commit()

    def get_or_fetch_trade_history(
        self,
        fc_player_id: str,
        format_key: str,
        fetcher: Callable[[], list],
        force: bool = False,
    ) -> list[tuple[str, float]]:
        key = f"{fc_player_id}/{format_key}"
        with self._session() as s:
            row = s.get(m.FantasyCalcTradeHistory, key)
            if row is not None and not force:
                return [tuple(p) for p in json.loads(row.points_json)]
        points = fetcher() or []
        with self._session() as s:
            s.merge(
                m.FantasyCalcTradeHistory(
                    key=key,
                    points_json=json.dumps([list(p) for p in points]),
                    fetched_at=m.utcnow(),
                )
            )
            s.commit()
        return [tuple(p) for p in points]

    # ------------------------------------------------------------------
    # User preferences
    # ------------------------------------------------------------------

    def load_preferences(self, username: str) -> m.UserPreferences:
        with self._session() as s:
            row = s.get(m.UserPreferences, username)
        return row if row is not None else m.UserPreferences(username=username)

    def save_preferences(self, prefs: m.UserPreferences) -> None:
        prefs.updated_at = m.utcnow()
        with self._session() as s:
            s.merge(prefs)
            s.commit()

    # ------------------------------------------------------------------
    # Value-history snapshots (ported from value_store.ValueStore)
    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        valuations: dict[str, PlayerValuation],
        snapshot_date: Optional[date] = None,
    ) -> int:
        day = (snapshot_date or date.today()).isoformat()
        with self._session() as s:
            for v in valuations.values():
                s.merge(
                    m.ValueSnapshot(
                        snapshot_date=day,
                        player_key=v.key,
                        name=v.name,
                        position=v.position,
                        base_value=v.base_value,
                        adjusted_value=v.adjusted_value,
                        sources_json=json.dumps(v.values_by_source),
                    )
                )
            s.commit()
        return len(valuations)

    def has_snapshot(self, snapshot_date: Optional[date] = None) -> bool:
        day = (snapshot_date or date.today()).isoformat()
        with self._session() as s:
            row = s.exec(
                select(m.ValueSnapshot).where(m.ValueSnapshot.snapshot_date == day)
            ).first()
        return row is not None

    def snapshot_dates(self) -> list[str]:
        with self._session() as s:
            rows = s.exec(
                select(m.ValueSnapshot.snapshot_date)
                .distinct()
                .order_by(m.ValueSnapshot.snapshot_date)
            ).all()
        return list(rows)

    def get_history(self, player_key: str, days: int = 365) -> list[tuple[str, float]]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._session() as s:
            rows = s.exec(
                select(
                    m.ValueSnapshot.snapshot_date, m.ValueSnapshot.adjusted_value
                )
                .where(
                    m.ValueSnapshot.player_key == player_key,
                    m.ValueSnapshot.snapshot_date >= cutoff,
                )
                .order_by(m.ValueSnapshot.snapshot_date)
            ).all()
        return [(r[0], r[1]) for r in rows]

    def get_deltas(self, days: int = 30) -> dict[str, float]:
        dates = self.snapshot_dates()
        if len(dates) < 2:
            return {}
        latest = dates[-1]
        cutoff = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        eligible = [d for d in dates[:-1] if d <= cutoff]
        baseline = eligible[-1] if eligible else dates[0]

        with self._session() as s:
            latest_rows = dict(
                s.exec(
                    select(
                        m.ValueSnapshot.player_key, m.ValueSnapshot.adjusted_value
                    ).where(m.ValueSnapshot.snapshot_date == latest)
                ).all()
            )
            baseline_rows = dict(
                s.exec(
                    select(
                        m.ValueSnapshot.player_key, m.ValueSnapshot.adjusted_value
                    ).where(m.ValueSnapshot.snapshot_date == baseline)
                ).all()
            )
        return {
            key: value - baseline_rows[key]
            for key, value in latest_rows.items()
            if key in baseline_rows
        }

    # ------------------------------------------------------------------
    # Staleness helpers
    # ------------------------------------------------------------------

    def last_fetched(self, kind: str, key: str = "") -> Optional[datetime]:
        """``fetched_at`` for a cached resource, or ``None`` if absent.

        *kind* ∈ {"user","leagues","league_data","draft_data","nfl_players",
        "source"}; *key* is the row's primary key (source uses ``format_key``).
        """
        table_pk = {
            "user": (m.SleeperUserCache, key),
            "leagues": (m.SleeperLeaguesCache, key),
            "league_data": (m.LeagueDataCache, key),
            "draft_data": (m.DraftDataCache, key),
            "nfl_players": (m.NflPlayersCache, 1),
        }
        if kind in table_pk:
            table, pk = table_pk[kind]
            with self._session() as s:
                row = s.get(table, pk)
            return row.fetched_at if row is not None else None
        if kind == "source":
            with self._session() as s:
                row = s.exec(
                    select(m.SourceFetch)
                    .where(m.SourceFetch.format_key == key)
                    .order_by(m.SourceFetch.fetched_at.desc())
                ).first()
            return row.fetched_at if row is not None else None
        if kind == "stat":
            with self._session() as s:
                row = s.get(m.StatCache, key)
            return row.fetched_at if row is not None else None
        return None


# ---------------------------------------------------------------------------
# Preferences <-> ValueWeights conversion
# ---------------------------------------------------------------------------


def weights_from_prefs(prefs: m.UserPreferences) -> ValueWeights:
    raw = json.loads(prefs.source_weights_json) if prefs.source_weights_json else []
    source_weights = (
        tuple((str(s), float(w)) for s, w in raw) if raw else DEFAULT_SOURCE_WEIGHTS
    )
    return ValueWeights(
        source_weights=source_weights,
        age_weight=prefs.age_weight,
        trend_weight=prefs.trend_weight,
        injury_weight=prefs.injury_weight,
        adp_divergence_weight=prefs.adp_divergence_weight,
        production_weight=prefs.production_weight,
    )


def apply_weights_to_prefs(prefs: m.UserPreferences, weights: ValueWeights) -> None:
    prefs.source_weights_json = json.dumps([list(sw) for sw in weights.source_weights])
    prefs.age_weight = weights.age_weight
    prefs.trend_weight = weights.trend_weight
    prefs.injury_weight = weights.injury_weight
    prefs.adp_divergence_weight = weights.adp_divergence_weight
    prefs.production_weight = weights.production_weight
