from __future__ import annotations

"""Optional user-level startup hook for runtime policy extensions."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from sitecustomize import *  # noqa: F401,F403

ROOT = Path(__file__).resolve().parent

A_TIER_ONLY_ENV = {
    "HARIZON_PUBLICATION_TIER_MODE": "a_only",
    "HARIZON_A_TIER_ONLY": "true",
    "PUBLISH_ALLOW_B_TIER": "false",
    "PUBLISH_B_TIER_WATCH_ONLY": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "a_only_publish_b_watchlist",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "2",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "MIN_CONTEXT_SOURCES_PUBLISH": "2",
    "MIN_SOURCES_PUBLISH": "2",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "false",
    "CONTROLLED_FALLBACK_ALLOW_B_TIER": "false",
    "CONTROLLED_FALLBACK_TIER_B_ENABLED": "false",
    "CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY": "true",
    "CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED": "false",
    "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": "0",
    "PROMOTE_B_COVER_VALUE_CANDIDATES_ENABLED": "false",
    "PROMOTE_B_COVER_AFTER_A_PROMOTION_ENABLED": "false",
    "PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW": "true",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKMAKERS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "999",
    "PUBLISH_TIER_B_MIN_BOOKS": "999",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "999",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "2",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "true",
    "CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN": "1",
    "MAX_PICKS_PER_RUN": "1",
    "CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN": "1",
    "DAY_INVENTORY_EXTRA_FIXTURES_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
    "DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES": "300",
    "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "30",
    "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": "220",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "300",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "36",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_PER_RUN_MAX": "80",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "80",
    "SPORTLOGIC_MATCH_LIMIT": "300",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "150",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "150",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_ODDS_MATCH_LIMIT": "300",
    "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "220",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "160",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "80",
    "SSTATS_LOOKBACK_DAYS": "45",
    "SSTATS_RECENT_MATCHES": "8",
    "SSTATS_FORM_MIN_SAMPLE_PER_TEAM": "2",
}

os.environ.update(A_TIER_ONLY_ENV)

_ORIGINAL_ENVIRON_UPDATE = type(os.environ).update


def _update_then_reapply(self: Any, other: Any = (), /, **kwargs: Any) -> None:
    result = _ORIGINAL_ENVIRON_UPDATE(self, other, **kwargs)
    _ORIGINAL_ENVIRON_UPDATE(self, A_TIER_ONLY_ENV)
    return result


type(os.environ).update = _update_then_reapply


def _write_policy_report(payload: dict[str, Any]) -> None:
    try:
        out = ROOT / ".data" / "exports" / "latest-a-only-usercustomize-policy.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _patch_controlled_fallback(module: Any) -> None:
    if getattr(module, "_harizon_usercustomize_a_only", False):
        return
    original_tier_reasons = getattr(module, "tier_reasons", None)
    if not callable(original_tier_reasons):
        return

    def tier_reasons_a_only(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(original_tier_reasons(tier, candidate, metrics) or [])
        code = str(tier or "").replace("уровень", "").strip().upper()
        if code == "B":
            reasons.append("tier_b_watchlist_only_no_publication")
        elif code == "A":
            odds_sources = _as_int((metrics or {}).get("odds_sources_count"), 0)
            confirmations = _as_int((metrics or {}).get("confirmation_sources_count"), _as_int((metrics or {}).get("sources_count"), 0))
            books = _as_int((metrics or {}).get("books_count"), 0)
            if odds_sources < 2:
                reasons.append(f"tier_a_odds_sources_below_min:{odds_sources}/2")
            if confirmations < 2:
                reasons.append(f"tier_a_confirmation_sources_below_min:{confirmations}/2")
            if books < 2:
                reasons.append(f"tier_a_books_below_min:{books}/2")
        return reasons

    module.tier_reasons = tier_reasons_a_only
    module._harizon_usercustomize_a_only = True
    _write_policy_report({
        "status": "installed",
        "policy": "A-only public publication; B-tier watchlist-only",
        "patched_module": str(getattr(module, "__name__", "")),
        "env": A_TIER_ONLY_ENV,
    })


_original_spec_from_file_location = importlib.util.spec_from_file_location


def _spec_from_file_location_a_only(name: str, location: Any, *args: Any, **kwargs: Any) -> Any:
    spec = _original_spec_from_file_location(name, location, *args, **kwargs)
    try:
        path = Path(str(location)).resolve()
    except Exception:
        path = None
    if spec is None or path is None or path.name != "publish_controlled_fallback.py":
        return spec
    loader = getattr(spec, "loader", None)
    exec_module = getattr(loader, "exec_module", None)
    if not callable(exec_module):
        return spec

    def exec_module_patched(module: Any) -> Any:
        result = exec_module(module)
        try:
            _patch_controlled_fallback(module)
        except Exception as exc:
            _write_policy_report({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return result

    loader.exec_module = exec_module_patched  # type: ignore[method-assign]
    return spec


importlib.util.spec_from_file_location = _spec_from_file_location_a_only
_write_policy_report({"status": "loaded", "policy": "A-only public publication; B-tier watchlist-only", "env": A_TIER_ONLY_ENV})

try:
    from app.services.bzzoiro_gap_planner_fallback_patch import install as _install_bzz_gap_targets
    _install_bzz_gap_targets()
except Exception:
    pass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_helper_process() -> bool:
    name = Path(str(sys.argv[0] or "")).name
    return (
        str(sys.argv[0] or "").strip() == "-"
        or name.startswith("publish_controlled_fallback")
        or os.getenv("HARIZON_SKIP_USERCUSTOMIZE_INSTALLERS") == "1"
    )


def install_legacy_usercustomize() -> dict[str, str]:
    try:
        from app.services import runtime_startup_chain

        result = runtime_startup_chain.install_all()
        return {"app.services.runtime_startup_chain": str(result)}
    except Exception as exc:
        return {"app.services.runtime_startup_chain": f"{type(exc).__name__}: {exc}"}


if _truthy(os.getenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED")) and not _is_helper_process():
    install_legacy_usercustomize()
