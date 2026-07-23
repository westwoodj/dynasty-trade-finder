"""Player-value providers built on the typed ``parse_apis`` clients.

Each provider fetches one source's dynasty values and returns them as
:class:`NormalizedPlayerValue` rows scaled 0–100 within the source, so
downstream blending and arbitrage compare like with like.

The generated ``parse_apis`` clients are gitignored and only exist after
``uv run parse sync``, so every client import is deferred to provider
construction and surfaces as :class:`ProviderUnavailableError` with
remediation instructions instead of an import crash.

parse_sdk typing is best-effort: a field annotated ``int`` may hold a raw
string or ``None`` (DraftSharks fields are *all* strings).  Every numeric
read goes through :func:`_as_float` / :func:`_as_int`.
"""

from __future__ import annotations

import logging
from typing import Optional

from parse_sdk import PaginationLimitError
from pydantic import BaseModel

from .league_settings import LeagueFormat, draftsharks_params, fantasycalc_params
from .name_matching import normalize_name

logger = logging.getLogger(__name__)

SYNC_HINT = (
    "The generated parse_apis client is missing. Run `uv run parse sync` "
    "with PARSE_API_KEY set (or `uv run parse login` first)."
)


class ProviderUnavailableError(RuntimeError):
    """A provider's generated client is not installed or not usable."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class NormalizedPlayerValue(BaseModel):
    """One player's value from a single source, scaled 0–100 in-source."""

    name: str
    position: str
    source: str
    value: float  # 0–100, normalized within the source
    raw_value: float  # the source's native value scale
    team: str = ""
    age: Optional[float] = None
    sleeper_id: Optional[str] = None
    source_player_id: Optional[str] = None  # e.g. FantasyCalc id, for trade history
    overall_rank: Optional[int] = None
    trend_30_day: Optional[float] = None  # FantasyCalc only
    adp: Optional[float] = None  # DraftSharks only
    injury_risk: Optional[str] = None  # DraftSharks only
    projection: Optional[float] = None  # DraftSharks ds_projection

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


# ---------------------------------------------------------------------------
# Defensive coercion — parse_sdk typing is best-effort
# ---------------------------------------------------------------------------


def _as_float(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> Optional[int]:
    f = _as_float(value)
    return int(f) if f is not None else None


def _get(obj: object, name: str, default: object = None) -> object:
    """Attribute access that tolerates coercion-degraded raw dict rows."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_str(value: object) -> str:
    """String coercion that unwraps str-enums to their value."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _materialize(paginator) -> list:
    """Materialize a Paginator, keeping the partial prefix on a limit error."""
    try:
        return paginator.list()
    except PaginationLimitError as err:
        if err.partial_items:
            logger.warning(
                "Pagination stopped early (%s pages, %s items); using partial results",
                err.pages_fetched,
                err.items_yielded,
            )
            return err.partial_items
        raise


def _rescale_to_100(rows: list[NormalizedPlayerValue]) -> list[NormalizedPlayerValue]:
    """Set each row's ``value`` to ``raw_value`` scaled 0–100 by source max."""
    if not rows:
        return rows
    max_val = max(r.raw_value for r in rows)
    if max_val <= 0:
        return rows
    for r in rows:
        r.value = r.raw_value / max_val * 100.0
    return rows


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class FantasyCalcProvider:
    """Dynasty trade values from FantasyCalc — the primary source.

    FantasyCalc rows carry ``player.sleeper_id``, a direct join key to
    Sleeper roster player ids.
    """

    SOURCE = "fantasycalc"

    def __init__(self, client=None, api_key: Optional[str] = None) -> None:
        if client is None:
            try:
                from parse_apis.fantasycalc_com_api import FantasyCalc
            except ImportError as exc:
                raise ProviderUnavailableError(f"FantasyCalc: {SYNC_HINT}") from exc
            client = FantasyCalc(api_key=api_key) if api_key else FantasyCalc()
        self.client = client

    def fetch_values(
        self, fmt: LeagueFormat, limit: int = 500
    ) -> list[NormalizedPlayerValue]:
        paginator = self.client.rankings.list(
            **fantasycalc_params(fmt), include_adp=False, limit=limit
        )
        rows: list[NormalizedPlayerValue] = []
        for ranking in _materialize(paginator):
            player = _get(ranking, "player")
            name = str(_get(player, "name", "") or "").strip()
            raw_value = _as_float(_get(ranking, "value"))
            if not name or raw_value is None:
                continue
            sleeper_id = _get(player, "sleeper_id")
            fc_id = _get(player, "id")
            rows.append(
                NormalizedPlayerValue(
                    name=name,
                    position=str(_get(player, "position", "") or "").upper(),
                    source=self.SOURCE,
                    value=0.0,
                    raw_value=raw_value,
                    team=str(_get(player, "team", "") or ""),
                    age=_as_float(_get(player, "age")),
                    sleeper_id=str(sleeper_id) if sleeper_id else None,
                    source_player_id=str(fc_id) if fc_id is not None else None,
                    overall_rank=_as_int(_get(ranking, "overall_rank")),
                    trend_30_day=_as_float(_get(ranking, "trend_30_day")),
                )
            )
        return _rescale_to_100(rows)

    def fetch_trade_history(
        self, fc_player_id: str, fmt: LeagueFormat
    ) -> list[tuple[str, float]]:
        """Return ``[(date, value), …]`` of FantasyCalc historical values."""
        history = self.client.trade_histories.get(
            str(fc_player_id),
            num_qbs=fantasycalc_params(fmt)["num_qbs"],
            is_dynasty=fmt.is_dynasty,
        )
        points: list[tuple[str, float]] = []
        for hv in _get(history, "historical_values", None) or []:
            date = str(_get(hv, "date", "") or "")
            value = _as_float(_get(hv, "value"))
            if date and value is not None:
                points.append((date, value))
        return points


class DraftSharksProvider:
    """DraftSharks dynasty rankings: ds_value plus ADP, projections,
    injury risk.  No sleeper_id — joined downstream by name."""

    SOURCE = "draftsharks"

    def __init__(self, client=None, api_key: Optional[str] = None) -> None:
        if client is None:
            try:
                from parse_apis.draftsharks_com_api import DraftSharks
            except ImportError as exc:
                raise ProviderUnavailableError(f"DraftSharks: {SYNC_HINT}") from exc
            client = DraftSharks(api_key=api_key) if api_key else DraftSharks()
        self.client = client

    def fetch_values(
        self, fmt: LeagueFormat, limit: int = 400
    ) -> list[NormalizedPlayerValue]:
        paginator = self.client.players.list(
            depth="rankings", **draftsharks_params(fmt), limit=limit
        )
        rows: list[NormalizedPlayerValue] = []
        for player in _materialize(paginator):
            name = str(_get(player, "name", "") or "").strip()
            raw_value = _as_float(_get(player, "ds_value"))
            if not name or raw_value is None:
                continue
            injury = str(_get(player, "injury_risk", "") or "").strip()
            rows.append(
                NormalizedPlayerValue(
                    name=name,
                    position=str(_get(player, "position", "") or "").upper(),
                    source=self.SOURCE,
                    value=0.0,
                    raw_value=raw_value,
                    team=str(_get(player, "team", "") or ""),
                    source_player_id=str(_get(player, "id", "") or "") or None,
                    overall_rank=_as_int(_get(player, "rank")),
                    adp=_as_float(_get(player, "adp")),
                    injury_risk=injury or None,
                    projection=_as_float(_get(player, "ds_projection")),
                )
            )
        return _rescale_to_100(rows)


class KTCProvider:
    """KeepTradeCut crowdsourced dynasty values.

    Rows are ``Player`` resources keyed by ``playerName``/``slug``; the
    ``overallTrend`` field feeds trend analysis like FantasyCalc's
    ``trend_30_day``.  No sleeper_id — joined downstream by name.
    """

    SOURCE = "ktc"

    def __init__(self, client=None, api_key: Optional[str] = None) -> None:
        if client is None:
            try:
                from parse_apis.keeptradecut_com_api import KeepTradeCut
            except ImportError as exc:
                raise ProviderUnavailableError(f"KeepTradeCut: {SYNC_HINT}") from exc
            client = KeepTradeCut(api_key=api_key) if api_key else KeepTradeCut()
        self.client = client

    def fetch_values(
        self, fmt: LeagueFormat, limit: int = 500
    ) -> list[NormalizedPlayerValue]:
        paginator = self.client.players.search(
            scoring="superflex" if fmt.is_superflex else "oneqb",
            position="all",
            limit=limit,
        )
        rows: list[NormalizedPlayerValue] = []
        for player in _materialize(paginator):
            name = _as_str(_get(player, "playerName")).strip()
            raw_value = _as_float(_get(player, "value"))
            if not name or raw_value is None:
                continue
            slug = _as_str(_get(player, "slug"))
            rows.append(
                NormalizedPlayerValue(
                    name=name,
                    position=_as_str(_get(player, "position")).upper(),
                    source=self.SOURCE,
                    value=0.0,
                    raw_value=raw_value,
                    team=_as_str(_get(player, "team")),
                    age=_as_float(_get(player, "age")),
                    source_player_id=slug or None,
                    overall_rank=_as_int(_get(player, "rank")),
                    trend_30_day=_as_float(_get(player, "overallTrend")),
                )
            )
        return _rescale_to_100(rows)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

PROVIDER_CLASSES = (FantasyCalcProvider, DraftSharksProvider, KTCProvider)


def fetch_all_sources(
    fmt: LeagueFormat,
    api_key: Optional[str] = None,
    providers: Optional[list] = None,
) -> tuple[dict[str, list[NormalizedPlayerValue]], dict[str, str]]:
    """Fetch values from every available provider.

    Providers fail independently: the result is
    ``({source: [NormalizedPlayerValue, …]}, {source: error_message})`` so
    the UI can warn about partial failures while using what worked.
    """
    results: dict[str, list[NormalizedPlayerValue]] = {}
    errors: dict[str, str] = {}

    if providers is None:
        providers = []
        for cls in PROVIDER_CLASSES:
            try:
                providers.append(cls(api_key=api_key))
            except ProviderUnavailableError as exc:
                errors[cls.SOURCE] = str(exc)

    for provider in providers:
        source = getattr(provider, "SOURCE", provider.__class__.__name__)
        try:
            values = provider.fetch_values(fmt)
        except Exception as exc:  # ParseError, AuthError, network, …
            logger.warning("Provider %s failed: %s", source, exc)
            errors[source] = str(exc)
            continue
        if values:
            results[source] = values
        else:
            errors[source] = "returned no players"
    return results, errors
