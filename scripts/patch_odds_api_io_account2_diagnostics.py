from __future__ import annotations

"""Runtime patch for odds-api.io account2 entitlement diagnostics/fallback.

Goals:
- ODDS_API_IO_KEY_2 is valid, but selected account2 books can still return 403
  plan restriction. Do not treat that as a bad key.
- If the selected pair is unavailable, optionally retry account2 without the
  bookmakers filter to discover which two dashboard-selected books the account
  is actually allowed to return.
- Preserve source independence: recovered account2 rows are still odds_api_io,
  not a second provider.
"""

import os
from typing import Any


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def install(module: Any | None = None) -> dict[str, Any]:
    if module is None:
        from app.providers import odds_api_io as module  # type: ignore
    cls = getattr(module, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "provider_class_missing"}
    if getattr(cls, "_harizon_account2_diag_patched", False):
        return {"status": "already_patched"}

    original_accounts = cls._odds_accounts
    original_fetch_chunk = cls._fetch_odds_multi_chunk

    def patched_accounts(self: Any) -> list[dict[str, str]]:
        accounts = original_accounts(self)
        settings = getattr(self, "settings", None)
        configured_fallback = _csv(getattr(settings, "odds_api_io_bookmakers_account2_fallback", ""))
        env_fallback = _csv(os.getenv("ODDS_API_IO_BOOKMAKERS_ACCOUNT2_FALLBACK"))
        fallback = configured_fallback or env_fallback
        for account in accounts:
            if str(account.get("name") or "") == "account2":
                if fallback:
                    account["fallback_bookmakers"] = fallback
                elif _truthy("ODDS_API_IO_ACCOUNT2_UNFILTERED_RETRY_ENABLED", "true"):
                    # Empty fallback means retry /odds/multi without bookmakers filter.
                    account["fallback_bookmakers"] = "__UNFILTERED__"
                account["diagnostic_note"] = "account2 key present; bookmaker entitlement may be narrower than configured pair"
        return accounts

    async def patched_fetch_chunk(
        self: Any,
        client: Any,
        api_key: str,
        event_ids: list[int],
        target_books: str,
        stats: dict[str, Any],
        account_name: str = "account1",
        fallback_books: str = "",
    ) -> list[dict[str, Any]]:
        if str(account_name) != "account2" or str(fallback_books or "") != "__UNFILTERED__":
            return await original_fetch_chunk(self, client, api_key, event_ids, target_books, stats, account_name=account_name, fallback_books=fallback_books)

        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        account_stats["configured_bookmakers"] = target_books
        account_stats["fallback_bookmakers"] = "__UNFILTERED__"
        # First try configured Betfair/Sbobet normally.
        first = await original_fetch_chunk(self, client, api_key, event_ids, target_books, stats, account_name=account_name, fallback_books="")
        if first:
            return first
        if not bool(account_stats.get("plan_restriction")) and not bool(account_stats.get("auth_error")):
            return first
        # Clear per-account plan marker only for diagnostic unfiltered retry.
        account_stats["plan_restriction_before_unfiltered_retry"] = bool(account_stats.get("plan_restriction"))
        account_stats["auth_error_before_unfiltered_retry"] = bool(account_stats.get("auth_error"))
        account_stats["plan_restriction"] = False
        account_stats["auth_error"] = False
        account_stats["unfiltered_retry_attempted"] = True
        retry = await original_fetch_chunk(self, client, api_key, event_ids, "", stats, account_name=account_name, fallback_books="")
        if retry:
            account_stats["unfiltered_retry_recovered"] = True
            account_stats["effective_bookmakers"] = "__UNFILTERED__"
            stats["account2_unfiltered_retry_recovered"] = True
        else:
            account_stats["unfiltered_retry_recovered"] = False
        return retry

    cls._odds_accounts = patched_accounts
    cls._fetch_odds_multi_chunk = patched_fetch_chunk
    cls._harizon_account2_diag_patched = True
    return {
        "status": "installed",
        "account2_unfiltered_retry_enabled": _truthy("ODDS_API_IO_ACCOUNT2_UNFILTERED_RETRY_ENABLED", "true"),
    }


__all__ = ["install"]
