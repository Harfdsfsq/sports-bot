from __future__ import annotations

"""Runtime patch for odds-api.io account2 diagnostics and fallback config.

Safe operational patch: no publication guards are relaxed. The patch only makes
account2 expose why it produced zero offers and lets configured fallback books be
used when the provider reports entitlement/plan restriction.
"""

from typing import Any


def install(base: Any) -> None:
    provider_cls = getattr(base, "OddsApiIoProvider", None)
    if provider_cls is None:
        return
    if getattr(provider_cls, "_harizon_account2_diag_patch_installed", False):
        return

    old_odds_accounts = getattr(provider_cls, "_odds_accounts", None)
    old_fetch = getattr(provider_cls, "_fetch_odds_multi_chunk", None)
    if not callable(old_odds_accounts) or not callable(old_fetch):
        return

    def patched_odds_accounts(self: Any) -> list[dict[str, str]]:
        accounts = list(old_odds_accounts(self) or [])
        settings = getattr(self, "settings", None)
        fallback = getattr(settings, "odds_api_io_bookmakers_account2_fallback", None)
        if fallback is None:
            import os
            fallback = os.getenv("ODDS_API_IO_BOOKMAKERS_ACCOUNT2_FALLBACK", "")
        if isinstance(fallback, (list, tuple)):
            fallback_text = ",".join(str(x).strip() for x in fallback if str(x).strip())
        else:
            fallback_text = str(fallback or "").strip()
        if fallback_text:
            for account in accounts:
                if str(account.get("name") or "") == "account2":
                    account["fallback_bookmakers"] = fallback_text
                    account["configured_fallback_bookmakers"] = fallback_text
        return accounts

    async def patched_fetch(
        self: Any,
        client: Any,
        api_key: str,
        event_ids: list[int],
        target_books: str,
        stats: dict[str, Any],
        account_name: str = "account1",
        fallback_books: str = "",
    ) -> list[dict[str, Any]]:
        before = 0
        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        try:
            before = int(account_stats.get("offers_parsed") or 0)
        except Exception:
            before = 0
        account_stats["configured_bookmakers"] = str(target_books or "")
        account_stats["configured_fallback_bookmakers"] = str(fallback_books or account_stats.get("fallback_bookmakers") or "")
        result = await old_fetch(self, client, api_key, event_ids, target_books, stats, account_name=account_name, fallback_books=fallback_books)
        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        account_stats["last_event_ids_requested"] = [int(x) for x in list(event_ids or [])[:10]]
        account_stats["last_event_ids_count"] = len(event_ids or [])
        account_stats["last_payload_events_returned"] = len(result or [])
        account_stats["zero_offer_after_request"] = bool(account_name == "account2" and int(account_stats.get("offers_parsed") or 0) <= before)
        account_stats["effective_bookmakers"] = str(account_stats.get("effective_bookmakers") or target_books or "")
        account_stats["body_preview"] = str(stats.get("last_body_preview") or "")[:600]
        account_stats["http_statuses"] = list(account_stats.get("http_statuses") or [])[-10:]
        return result

    provider_cls._odds_accounts = patched_odds_accounts
    provider_cls._fetch_odds_multi_chunk = patched_fetch
    provider_cls._harizon_account2_diag_patch_installed = True


def main() -> int:
    try:
        import app.providers.odds_api_io as base
        install(base)
        print('{"status":"installed","patch":"odds_api_io_account2_diagnostics"}')
    except Exception as exc:
        print('{"status":"error","patch":"odds_api_io_account2_diagnostics","error":"%s"}' % str(exc).replace('"', "'"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
