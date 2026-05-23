from __future__ import annotations

"""Fast-mode odds-api.io event-account fallback.

odds-api.io fetch_offers uses the first configured account to load the event
catalogue and then requests odds for all accounts.  In fast workflow runs the
primary account can hit a short 429 cooldown before odds backfill starts.  When
that happens the whole odds layer becomes empty even though account2 is present.

This runtime patch only reorders the provider's accounts in HARIZON fast mode so
account2 can be used for event lookup while account1 is still used for odds
backfill.  It does not change publication guards or count accounts as sources by
itself.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-odds-api-io-fast-event-account-patch.json"
UTC = timezone.utc


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "fast", "balanced"}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _preferred_order(accounts: list[dict[str, str]]) -> list[dict[str, str]]:
    order_raw = str(os.getenv("ODDS_API_IO_ACCOUNT_ORDER") or "").strip()
    event_account = str(os.getenv("ODDS_API_IO_FAST_EVENT_ACCOUNT") or os.getenv("ODDS_API_IO_EVENT_ACCOUNT") or "").strip()
    if not order_raw and event_account:
        order_raw = event_account
    if not order_raw:
        return accounts
    wanted = [x.strip().lower() for x in order_raw.split(",") if x.strip()]
    by_name = {str(a.get("name") or "").strip().lower(): a for a in accounts}
    out: list[dict[str, str]] = []
    for name in wanted:
        row = by_name.get(name)
        if row is not None and row not in out:
            out.append(row)
    for row in accounts:
        if row not in out:
            out.append(row)
    return out


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("HARIZON_FAST_RUN"), False):
        result = {"status": "disabled_not_fast"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    if not _truthy(os.getenv("ODDS_API_IO_FAST_EVENT_ACCOUNT_PATCH_ENABLED"), True):
        result = {"status": "disabled_by_env"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    try:
        from app.providers.odds_api_io import OddsApiIoProvider
    except Exception as exc:
        result = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result

    current = getattr(OddsApiIoProvider, "_odds_accounts", None)
    if not callable(current):
        result = {"status": "skipped", "reason": "missing__odds_accounts"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    if getattr(current, "_harizon_fast_event_account_patch", False):
        result = {"status": "already_installed"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result

    original = current

    def odds_accounts_patched(self: Any) -> list[dict[str, str]]:
        accounts = list(original(self) or [])
        ordered = _preferred_order(accounts)
        try:
            self._harizon_fast_event_account_order = [a.get("name") for a in ordered]
        except Exception:
            pass
        return ordered

    odds_accounts_patched._harizon_fast_event_account_patch = True  # type: ignore[attr-defined]
    odds_accounts_patched._harizon_original = original  # type: ignore[attr-defined]
    OddsApiIoProvider._odds_accounts = odds_accounts_patched  # type: ignore[method-assign]
    result = {
        "status": "installed",
        "event_account": os.getenv("ODDS_API_IO_FAST_EVENT_ACCOUNT") or os.getenv("ODDS_API_IO_EVENT_ACCOUNT") or "",
        "account_order": os.getenv("ODDS_API_IO_ACCOUNT_ORDER") or "",
    }
    _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
    return result
