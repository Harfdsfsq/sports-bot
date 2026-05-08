from __future__ import annotations

"""Small startup compatibility patches for odds-api.io provider.

The production provider contains helper methods that are called as instance
methods from parsing code. If a helper was accidentally declared without
``self`` and without ``@staticmethod``, Python binds ``self`` automatically and
raises errors such as:

    TypeError: OddsApiIoProvider._is_supported_market() takes 1 positional
    argument but 2 were given

This module normalizes helper binding and adds a conservative rescue for
odds-api.io ``/odds/multi`` responses that are empty because the requested
bookmaker filter uses display labels instead of API bookmaker slugs.
"""

from functools import wraps
from typing import Any, Callable


HELPER_NAMES = (
    "_is_supported_market",
    "_family_for_market",
    "_line_from_value",
    "_map_h2h_selection",
    "_normalize_yes_no",
    "_normalize_double_chance_selection",
    "_normalize_team_total_selection",
    "_infer_team_total_side",
    "_canonical_bookmaker",
)

BOOKMAKER_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "bet365": ("bet365", "Bet365"),
    "unibet": ("unibet", "Unibet"),
    "betfair exchange": ("betfair", "betfair_exchange", "betfairexchange", "Betfair"),
    "betfair": ("betfair", "betfair_exchange", "betfairexchange", "Betfair"),
    "sbobet": ("sbobet", "SBOBET", "Sbobet"),
    "pinnacle": ("pinnacle", "Pinnacle"),
    "william hill": ("williamhill", "william_hill", "WilliamHill"),
    "bwin": ("bwin", "Bwin"),
}


def _raw_class_attr(cls: type[Any], name: str) -> Any:
    try:
        value = cls.__dict__.get(name)
        if isinstance(value, staticmethod):
            return value.__func__
        if isinstance(value, classmethod):
            return value.__func__
        return value
    except Exception:
        return None


def _make_binding_safe(raw: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(raw)
    def binding_safe(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return raw(*args, **kwargs)
        except TypeError as first_exc:
            try:
                return raw(self, *args, **kwargs)
            except TypeError:
                raise first_exc

    return binding_safe


def _payload_items_from_response(provider: Any, response: Any, stats: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = provider._safe_json(response)
    except Exception:
        payload = None
    if payload is None:
        return []
    try:
        shape = provider._payload_shape(payload)
        shapes = stats.setdefault("payload_shapes", [])
        if shape not in shapes:
            shapes.append(shape)
    except Exception:
        pass
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "events", "odds", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _split_books(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _norm_book(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _bookmaker_retry_params(target_books: Any) -> list[str]:
    raw_books = _split_books(target_books)
    if not raw_books:
        return []
    aliases_by_position: list[tuple[str, ...]] = []
    for raw in raw_books:
        key = _norm_book(raw)
        aliases = BOOKMAKER_ALIAS_GROUPS.get(key)
        if aliases is None:
            compact = key.replace(" ", "")
            aliases = (compact, key.replace(" ", "_"), raw)
        deduped: list[str] = []
        for item in aliases:
            text = str(item or "").strip()
            if text and text not in deduped:
                deduped.append(text)
        aliases_by_position.append(tuple(deduped[:4]))

    candidates: list[str] = []
    # First try canonical lowercase slugs for all requested books.
    first = ",".join(group[0] for group in aliases_by_position if group)
    if first:
        candidates.append(first)
    # Then try each alias family at the same index, preserving account book split.
    max_len = max((len(group) for group in aliases_by_position), default=0)
    for idx in range(max_len):
        values = [group[idx] if idx < len(group) else group[0] for group in aliases_by_position if group]
        if values:
            candidates.append(",".join(values))
    # Last, try individual bookmakers: useful when the free account grants only
    # one selected book although the env asks for two.
    for group in aliases_by_position:
        for alias in group[:3]:
            candidates.append(alias)

    original = ",".join(raw_books)
    out: list[str] = []
    seen: set[str] = {original}
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


async def _request_odds_multi_direct(
    provider: Any,
    client: Any,
    api_key: str,
    event_ids: list[int],
    target_books: str,
    stats: dict[str, Any],
    account_stats: dict[str, Any],
    account_name: str,
    retry_kind: str,
) -> list[dict[str, Any]]:
    try:
        if not provider._request_budget_allows(stats, account_name=account_name):
            return []
    except Exception:
        return []
    stats["odds_requests"] = int(stats.get("odds_requests") or 0) + 1
    account_stats["odds_requests"] = int(account_stats.get("odds_requests") or 0) + 1
    try:
        provider._record_request(account_name=account_name)
    except Exception:
        pass
    params = {
        "apiKey": api_key,
        "eventIds": ",".join(str(item) for item in event_ids),
        "bookmakers": target_books,
    }
    try:
        response = await client.get(f"{provider.base_url}/odds/multi", params=params)
    except Exception as exc:
        stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
        account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
        stats["last_body_preview"] = f"{retry_kind} odds retry failed for {target_books}: {exc}"
        return []

    try:
        status = int(response.status_code)
    except Exception:
        status = 0
    stats.setdefault("odds_http_statuses", []).append(status)
    account_stats.setdefault("http_statuses", []).append(status)
    try:
        stats["last_body_preview"] = response.text[:2000]
    except Exception:
        pass
    if status == 429:
        stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
        account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
        stats["rate_limited"] = True
        account_stats["rate_limited"] = True
        return []
    if status != 200:
        stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
        account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
        account_stats.setdefault("bookmaker_retry_errors", []).append({"bookmakers": target_books, "status": status})
        return []
    items = _payload_items_from_response(provider, response, stats)
    account_stats.setdefault("bookmaker_retry_attempts", []).append({
        "bookmakers": target_books,
        "status": status,
        "events": len(items),
    })
    return items


def _patch_bookmaker_alias_odds_rescue(cls: type[Any]) -> bool:
    if getattr(cls, "_harizon_bookmaker_alias_rescue_installed", False):
        return False
    original = getattr(cls, "_fetch_odds_multi_chunk", None)
    if not callable(original):
        return False

    async def fetch_odds_multi_chunk_patched(
        self: Any,
        client: Any,
        api_key: str,
        event_ids: list[int],
        target_books: str,
        stats: dict[str, Any],
        account_name: str = "account1",
    ) -> list[dict[str, Any]]:
        items = await original(self, client, api_key, event_ids, target_books, stats, account_name=account_name)
        if items or not event_ids:
            return items
        retry_enabled = str(__import__("os").getenv("ODDS_API_IO_BOOKMAKER_ALIAS_EMPTY_RETRY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on", "force"}
        if not retry_enabled:
            return items
        alias_params = _bookmaker_retry_params(target_books)
        if not alias_params:
            return items
        max_attempts_raw = str(__import__("os").getenv("ODDS_API_IO_BOOKMAKER_ALIAS_RETRY_MAX", "4") or "4")
        try:
            max_attempts = max(1, int(float(max_attempts_raw)))
        except Exception:
            max_attempts = 4
        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        account_stats["bookmaker_alias_empty_retry"] = int(account_stats.get("bookmaker_alias_empty_retry") or 0) + 1
        stats["bookmaker_alias_empty_retry"] = int(stats.get("bookmaker_alias_empty_retry") or 0) + 1
        for alias_books in alias_params[:max_attempts]:
            rescued = await _request_odds_multi_direct(
                self,
                client,
                api_key,
                event_ids,
                alias_books,
                stats,
                account_stats,
                account_name,
                retry_kind="bookmaker_alias",
            )
            if rescued:
                stats["bookmaker_alias_retry_rescued_events"] = int(stats.get("bookmaker_alias_retry_rescued_events") or 0) + len(rescued)
                account_stats["bookmaker_alias_retry_rescued_events"] = int(account_stats.get("bookmaker_alias_retry_rescued_events") or 0) + len(rescued)
                account_stats["bookmaker_alias_retry_winner"] = alias_books
                return rescued
        return items

    cls._fetch_odds_multi_chunk = fetch_odds_multi_chunk_patched
    cls._harizon_bookmaker_alias_rescue_installed = True
    return True


def install() -> dict[str, str]:
    from app.providers import odds_api_io

    cls = getattr(odds_api_io, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "skipped", "reason": "provider_class_missing"}

    fixed: list[str] = []
    for name in HELPER_NAMES:
        raw = _raw_class_attr(cls, name)
        if not callable(raw):
            continue
        setattr(cls, name, _make_binding_safe(raw))
        fixed.append(name)

    alias_rescue_installed = _patch_bookmaker_alias_odds_rescue(cls)

    cls._harizon_startup_compat_installed = True
    cls._harizon_startup_compat_version = "binding-safe-v4-bookmaker-alias-rescue"
    cls._harizon_startup_compat_fixed = fixed
    return {
        "status": "installed",
        "version": "binding-safe-v4-bookmaker-alias-rescue",
        "fixed": ",".join(fixed),
        "bookmaker_alias_rescue": str(bool(alias_rescue_installed)).lower(),
    }
