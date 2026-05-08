from __future__ import annotations

"""Small startup compatibility patches for odds-api.io provider.

The production provider contains helper methods that are called as instance
methods from parsing code. If a helper was accidentally declared without
``self`` and without ``@staticmethod``, Python binds ``self`` automatically and
raises errors such as:

    TypeError: OddsApiIoProvider._is_supported_market() takes 1 positional
    argument but 2 were given

This module normalizes helper binding and adds a conservative rescue for
odds-api.io ``/odds/multi`` responses that are empty only because the requested
bookmaker filter is too narrow or uses unsupported bookmaker labels.
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
    """Return an instance method wrapper tolerant to both helper styles.

    Supports:
    - def helper(value)
    - @staticmethod def helper(value)
    - def helper(self, value)
    - wrappers installed by older runtime patches

    The first call attempts the no-self form; if Python reports an argument
    binding TypeError, the wrapper retries with ``self`` injected.
    """
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


def _patch_unfiltered_odds_rescue(cls: type[Any]) -> bool:
    if getattr(cls, "_harizon_unfiltered_odds_rescue_installed", False):
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
        if str(target_books or "").strip().lower() in {"", "__unfiltered__", "all", "none"}:
            return items

        rescue_enabled = str(__import__("os").getenv("ODDS_API_IO_UNFILTERED_EMPTY_RETRY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on", "force"}
        if not rescue_enabled:
            return items
        try:
            if not self._request_budget_allows(stats, account_name=account_name):
                return items
        except Exception:
            return items

        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        account_stats["unfiltered_empty_retry"] = int(account_stats.get("unfiltered_empty_retry") or 0) + 1
        stats["unfiltered_empty_retry"] = int(stats.get("unfiltered_empty_retry") or 0) + 1
        stats["odds_requests"] = int(stats.get("odds_requests") or 0) + 1
        account_stats["odds_requests"] = int(account_stats.get("odds_requests") or 0) + 1
        try:
            self._record_request(account_name=account_name)
        except Exception:
            pass

        params = {
            "apiKey": api_key,
            "eventIds": ",".join(str(item) for item in event_ids),
        }
        try:
            response = await client.get(f"{self.base_url}/odds/multi", params=params)
        except Exception as exc:
            stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
            account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
            stats["last_body_preview"] = f"unfiltered odds retry failed: {exc}"
            return items

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
            return items
        if status != 200:
            stats["response_errors"] = int(stats.get("response_errors") or 0) + 1
            account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
            return items
        rescued = _payload_items_from_response(self, response, stats)
        if rescued:
            stats["unfiltered_empty_retry_rescued_events"] = int(stats.get("unfiltered_empty_retry_rescued_events") or 0) + len(rescued)
            account_stats["unfiltered_empty_retry_rescued_events"] = int(account_stats.get("unfiltered_empty_retry_rescued_events") or 0) + len(rescued)
        return rescued

    cls._fetch_odds_multi_chunk = fetch_odds_multi_chunk_patched
    cls._harizon_unfiltered_odds_rescue_installed = True
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
        # Always install the wrapper. Older startup/runtime patches may have
        # marked the class as installed before the provider was actually safe.
        setattr(cls, name, _make_binding_safe(raw))
        fixed.append(name)

    rescue_installed = _patch_unfiltered_odds_rescue(cls)

    cls._harizon_startup_compat_installed = True
    cls._harizon_startup_compat_version = "binding-safe-v3-unfiltered-rescue"
    cls._harizon_startup_compat_fixed = fixed
    return {
        "status": "installed",
        "version": "binding-safe-v3-unfiltered-rescue",
        "fixed": ",".join(fixed),
        "unfiltered_rescue": str(bool(rescue_installed)).lower(),
    }
