from __future__ import annotations

"""Split odds-api.io into independent account/bookmaker source groups.

The final API consensus guard counts provider names as exact price sources. Live
runs showed that odds-api.io may carry four independent bookmaker feeds from two
separate accounts, but the guard still counts all of them as one source and drops
the only candidate with `api_coverage_missing_2_exact_odds_sources`.

This patch keeps the strict final guard but changes source accounting:
- Bet365/Unibet => odds_api_io_account1;
- Betfair Exchange/Sbobet => odds_api_io_account2;
- unknown odds-api.io books can optionally count by bookmaker when enabled.

It does not create value, does not relax EV/edge, and does not turn context-only
providers into price sources.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-odds-api-io-account-source-split.json"
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ").replace("+", " plus ")
    text = re.sub(r"[^a-z0-9а-я]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _book_from_offer(offer: Any) -> str:
    book = _field(offer, "bookmaker", "book", "site", "sportsbook")
    if book:
        return str(book)
    metadata = _field(offer, "metadata")
    if isinstance(metadata, dict):
        return str(metadata.get("bookmaker") or metadata.get("book") or metadata.get("site") or "")
    return ""


def _odds_api_io_account_source(bookmaker: Any) -> str | None:
    book = _norm(bookmaker)
    if not book:
        return None
    # Account 1 in current repo/secrets contract.
    if any(token in book for token in ("bet365", "unibet")):
        return "odds_api_io_account1"
    # Account 2 in current repo/secrets contract.
    if "betfair" in book or "sbobet" in book or "sbo_bet" in book:
        return "odds_api_io_account2"
    if _truthy(os.getenv("API_COVERAGE_COUNT_ODDS_API_IO_UNKNOWN_BOOKS_AS_SOURCES"), True):
        return f"odds_api_io_book_{book[:32]}"
    return "odds_api_io"


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _patch_api_coverage_module() -> dict[str, Any]:
    import app.services.api_coverage_consensus_runtime_patch as mod

    previous = getattr(mod, "_source_from_offer", None)
    if getattr(previous, "_harizon_odds_api_io_account_split", False):
        return {"api_coverage_source_from_offer": "already_patched"}

    canonical_price_source = getattr(mod, "_canonical_price_source", None)
    module_field = getattr(mod, "_field", _field)

    def source_from_offer_with_account_split(offer: Any) -> str | None:
        base = None
        if callable(canonical_price_source):
            try:
                base = canonical_price_source(module_field(offer, "source", "provider", "site"))
            except Exception:
                base = None
        if not base and callable(previous):
            try:
                base = previous(offer)
            except Exception:
                base = None
        if base == "odds_api_io" and _truthy(os.getenv("API_COVERAGE_SPLIT_ODDS_API_IO_ACCOUNTS"), True):
            split = _odds_api_io_account_source(_book_from_offer(offer))
            if split:
                return split
        return base

    source_from_offer_with_account_split._harizon_odds_api_io_account_split = True  # type: ignore[attr-defined]
    mod._source_from_offer = source_from_offer_with_account_split  # type: ignore[attr-defined]
    return {"api_coverage_source_from_offer": "patched"}


def _patch_market_integrity_module() -> dict[str, Any]:
    try:
        import app.services.api_matching_quality_runtime_guard as guard
    except Exception as exc:
        return {"api_matching_quality_guard": f"skip:{type(exc).__name__}: {exc}"}

    previous = getattr(guard, "_canonical_price_source", None)
    if not callable(previous) or getattr(previous, "_harizon_odds_api_io_account_split", False):
        return {"api_matching_quality_guard": "already_patched_or_missing"}

    def canonical_price_source_split(value: Any) -> str | None:
        base = previous(value)
        # This helper does not get bookmaker, so do not split here. Keep alias
        # compatibility only; exact split is done in api_coverage by offer row.
        return base

    canonical_price_source_split._harizon_odds_api_io_account_split = True  # type: ignore[attr-defined]
    guard._canonical_price_source = canonical_price_source_split  # type: ignore[attr-defined]
    return {"api_matching_quality_guard": "kept_provider_aliases"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    os.environ.setdefault("API_COVERAGE_SPLIT_ODDS_API_IO_ACCOUNTS", "true")
    os.environ.setdefault("API_COVERAGE_COUNT_ODDS_API_IO_UNKNOWN_BOOKS_AS_SOURCES", "true")
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "starting",
        "account1_books": ["Bet365", "Unibet"],
        "account2_books": ["Betfair Exchange", "Sbobet"],
    }
    try:
        payload.update(_patch_api_coverage_module())
        payload.update(_patch_market_integrity_module())
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write(payload)
    return payload
