"""Bounded Bzzoiro v2 outage fallback for production coverage runs.

The primary source remains Bzzoiro v2.  When every v2 request in a provider call
fails with a server-side 5xx response, this module opens a process-local circuit,
tries the still-supported v1 list endpoints once, and reuses any result for the
rest of the run.  v1 and v2 are canonicalized as the same independent provider;
this does not create an extra source or relax publication guards.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.services import daily_coverage_ledger as coverage_ledger

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-bzzoiro-v2-outage-fallback.json"
_INSTALLED = False
_ORIGINAL_CONTEXT = None
_ORIGINAL_MERGE_FETCH = None
_V2_CIRCUIT_OPEN = False
_V2_CIRCUIT_REASON = ""
_V2_CIRCUIT_OPENED_AT = 0.0
_OFFER_CACHE: dict[str, list[Any]] = {}
_OFFER_CACHE_STATS: dict[str, Any] = {}
_V1_ODDS_FAILED = False
_V1_CONTEXT_FAILED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _write(extra: dict[str, Any] | None = None) -> None:
    payload = {
        "status": "installed" if _INSTALLED else "not_installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "v2_circuit_open": _V2_CIRCUIT_OPEN,
        "v2_circuit_reason": _V2_CIRCUIT_REASON,
        "v2_circuit_opened_at_monotonic": _V2_CIRCUIT_OPENED_AT,
        "cached_offer_matches": len(_OFFER_CACHE),
        "v1_odds_failed": _V1_ODDS_FAILED,
        "v1_context_failed": _V1_CONTEXT_FAILED,
        "publication_contract_relaxed": False,
        "provider_identity_policy": "v1_and_v2_are_one_bzzoiro_source",
    }
    if extra:
        payload.update(extra)
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(REPORT_PATH)
    except Exception:
        pass


def _status_codes(stats: Any) -> list[int]:
    if not isinstance(stats, dict):
        return []
    values = stats.get("http_statuses")
    if not isinstance(values, list):
        return []
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except Exception:
            continue
    return out


def _server_failure(stats: Any) -> bool:
    if not isinstance(stats, dict):
        return False
    codes = _status_codes(stats)
    if codes and all(code >= 500 for code in codes):
        return True
    last_error = str(stats.get("last_error") or "").strip().lower()
    return last_error.startswith("http_status=5") or bool(stats.get("server_error_stop"))


def _open_circuit(stats: Any, stage: str) -> None:
    global _V2_CIRCUIT_OPEN, _V2_CIRCUIT_REASON, _V2_CIRCUIT_OPENED_AT
    _V2_CIRCUIT_OPEN = True
    _V2_CIRCUIT_OPENED_AT = time.monotonic()
    codes = _status_codes(stats)
    suffix = ",".join(str(code) for code in codes[-6:]) or str(
        (stats or {}).get("last_error") if isinstance(stats, dict) else "server_failure"
    )
    _V2_CIRCUIT_REASON = f"{stage}:{suffix}"
    _write({"last_stage": stage, "last_primary_stats": stats if isinstance(stats, dict) else {}})


def _hard_context(context: Any) -> bool:
    """Accept only prediction/statistical v1 payloads, never odds-only context."""

    payload = getattr(context, "payload", None)

    def visit(value: Any, parent: str = "") -> bool:
        if isinstance(value, dict):
            keys = {str(key).strip().lower() for key in value}
            if "prediction" in keys and isinstance(value.get("prediction"), dict):
                return bool(value.get("prediction"))
            markets = value.get("markets")
            if isinstance(markets, dict) and isinstance(markets.get("expected_goals"), dict):
                return True
            if parent in {"stats", "statistics", "prediction", "model"}:
                xg_keys = {
                    "xg",
                    "home_xg",
                    "away_xg",
                    "expected_home",
                    "expected_away",
                    "expected_goals",
                }
                if keys & xg_keys:
                    return True
            for key, child in value.items():
                if visit(child, str(key).strip().lower()):
                    return True
        elif isinstance(value, list):
            return any(visit(item, parent) for item in value)
        return False

    return visit(payload)


async def _v1_context_fallback(settings: Any, matches: list[Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    global _V1_CONTEXT_FAILED
    stats: dict[str, Any] = {
        "enabled": _truthy(os.getenv("BZZOIRO_V1_CONTEXT_FALLBACK_ENABLED"), True),
        "api_version": "v1",
        "mode": "server_failure_fallback",
        "contexts_built": 0,
        "hard_contexts_kept": 0,
        "odds_only_contexts_rejected": 0,
        "publication_contract_relaxed": False,
    }
    preview: dict[str, Any] = {"matched_examples": [], "unmatched_examples": []}
    if not stats["enabled"]:
        return {}, stats, preview
    if _V1_CONTEXT_FAILED:
        stats["skipped"] = True
        stats["reason"] = "process_local_v1_context_failure_circuit_open"
        return {}, stats, preview
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider as V1Provider

        provider = V1Provider(settings)
        provider.max_http_requests = min(
            int(getattr(provider, "max_http_requests", 12) or 12),
            max(2, int(float(os.getenv("BZZOIRO_V1_FALLBACK_REQUEST_CAP", "12") or 12))),
        )
        deadline = max(10.0, float(os.getenv("BZZOIRO_V1_CONTEXT_FALLBACK_DEADLINE_SECONDS", "35") or 35))
        contexts, raw_stats, raw_preview = await asyncio.wait_for(
            provider.fetch_context(matches), timeout=deadline
        )
        kept: dict[str, Any] = {}
        for key, context in dict(contexts or {}).items():
            if not _hard_context(context):
                stats["odds_only_contexts_rejected"] += 1
                continue
            details = dict(getattr(context, "details", {}) or {})
            details["bzzoiro_api_fallback"] = "v1_after_v2_server_failure"
            details["bzzoiro_provider_identity"] = "bzzoiro"
            context.details = details
            kept[str(key)] = context
        stats.update(
            {
                "contexts_built": len(contexts or {}),
                "hard_contexts_kept": len(kept),
                "raw_stats": raw_stats if isinstance(raw_stats, dict) else {},
            }
        )
        if not kept and _server_failure(raw_stats):
            _V1_CONTEXT_FAILED = True
            stats["server_failure_circuit_opened"] = True
        preview = raw_preview if isinstance(raw_preview, dict) else preview
        return kept, stats, preview
    except TimeoutError:
        _V1_CONTEXT_FAILED = True
        stats["error"] = "v1_context_fallback_deadline_exhausted"
    except Exception as exc:
        _V1_CONTEXT_FAILED = True
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return {}, stats, preview


def _context_factory(original: Any):
    async def fetch_context(self: Any, matches: list[Any]):
        if _V2_CIRCUIT_OPEN:
            contexts: dict[str, Any] = {}
            stats: dict[str, Any] = {
                "enabled": True,
                "skipped": True,
                "reason": "process_local_v2_5xx_circuit_open",
                "circuit_reason": _V2_CIRCUIT_REASON,
                "publication_contract_relaxed": False,
            }
            preview: dict[str, Any] = {}
        else:
            primary_contexts, primary_stats, primary_preview = await original(self, matches)
            contexts = dict(primary_contexts or {})
            stats = dict(primary_stats or {})
            preview = dict(primary_preview or {})
            if contexts or not _server_failure(stats):
                return contexts, stats, preview
            _open_circuit(stats, "context_v2")

        fallback, fallback_stats, fallback_preview = await _v1_context_fallback(
            getattr(self, "settings", None), list(matches or [])
        )
        stats["v1_server_failure_fallback"] = fallback_stats
        preview["v1_server_failure_fallback"] = fallback_preview
        if fallback:
            contexts.update(fallback)
            stats["contexts_built"] = len(contexts)
            stats["contexts_built_from_v1_fallback"] = len(fallback)
            coverage_ledger.record_provider_result("bzzoiro", "fetch_context", fallback, stats)
        return contexts, stats, preview

    fetch_context._harizon_bzzoiro_v2_outage_fallback = True  # type: ignore[attr-defined]
    return fetch_context


async def _v1_best_odds(
    merge_module: Any,
    settings: Any,
    matches: list[Any],
    base: dict[str, list[Any]],
) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    global _V1_ODDS_FAILED
    key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
    stats: dict[str, Any] = {
        "enabled": bool(key) and _truthy(os.getenv("BZZOIRO_V1_ODDS_FALLBACK_ENABLED"), True),
        "api_version": "v1",
        "mode": "odds_best_server_failure_fallback",
        "requests": 0,
        "response_errors": 0,
        "http_statuses": [],
        "rows": 0,
        "matched": 0,
        "offers": 0,
        "publication_contract_relaxed": False,
    }
    if not stats["enabled"]:
        return {}, stats
    if _V1_ODDS_FAILED:
        stats["skipped"] = True
        stats["reason"] = "process_local_v1_odds_failure_circuit_open"
        return {}, stats
    target = list(merge_module.selected_matches(matches, base) or [])
    if not target:
        return {}, stats

    markets = [
        item.strip()
        for item in str(
            os.getenv("BZZOIRO_BEST_ODDS_MARKETS")
            or "1x2,over_under_15,over_under_25,over_under_35"
        ).split(",")
        if item.strip()
    ]
    days = max(
        1,
        min(
            7,
            max(
                1,
                int(
                    max(
                        (match.commence_time.astimezone(UTC) - datetime.now(UTC)).total_seconds()
                        for match in target
                    )
                    // 86400
                )
                + 2,
            ),
        ),
    )
    url = str(os.getenv("BZZOIRO_V1_BASE_URL") or "https://sports.bzzoiro.com/api").rstrip("/") + "/odds/best/"
    headers = {"Authorization": f"Token {key}"}
    out: dict[str, list[Any]] = {}
    timeout = max(4.0, float(os.getenv("BZZOIRO_V1_ODDS_FALLBACK_TIMEOUT_SECONDS", "8") or 8))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for market in markets:
            stats["requests"] += 1
            try:
                response = await client.get(url, headers=headers, params={"days": days, "market": market})
            except Exception as exc:
                _V1_ODDS_FAILED = True
                stats["response_errors"] += 1
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
                break
            stats["http_statuses"].append(response.status_code)
            stats["last_url"] = url
            stats["last_body_preview"] = response.text[:1200]
            if response.status_code != 200:
                stats["response_errors"] += 1
                stats["last_error"] = f"http_status={response.status_code}"
                if response.status_code >= 500:
                    _V1_ODDS_FAILED = True
                    break
                continue
            try:
                payload = response.json()
            except Exception as exc:
                stats["response_errors"] += 1
                stats["last_error"] = f"json_error:{type(exc).__name__}"
                continue
            batch = list(merge_module.rows(payload) or [])
            stats["rows"] += len(batch)
            for row in batch:
                match, _score = merge_module._match_bzzoiro_row_to_match(row, target)
                if match is None:
                    continue
                event_id = str(row.get("event_id") or row.get("id") or "").strip() or None
                offers = list(merge_module.parse_any(row, match, "bzzoiro", event_id) or [])
                if offers:
                    out.setdefault(match.match_key, []).extend(offers)
                    stats["matched"] += 1
                    stats["offers"] += len(offers)
    return out, stats


def _merge_fetch_factory(original: Any, merge_module: Any):
    async def fetch_bzzoiro(
        settings: Any,
        matches: list[Any],
        base: dict[str, list[Any]],
        amap: dict[str, dict[str, str]],
    ):
        requested = {str(getattr(match, "match_key", "")) for match in matches if getattr(match, "match_key", "")}
        cached = {key: list(value) for key, value in _OFFER_CACHE.items() if key in requested and value}
        cache_complete = bool(requested) and requested.issubset(cached)
        if cache_complete:
            return cached, {
                "enabled": True,
                "mode": "process_cache_after_v2_or_v1",
                "cached_matches": len(cached),
                "network_requests": 0,
                "publication_contract_relaxed": False,
                "cache_origin": dict(_OFFER_CACHE_STATS),
            }

        offers: dict[str, list[Any]] = dict(cached)
        stats: dict[str, Any] = {
            "enabled": True,
            "mode": "v2_then_v1_on_server_failure",
            "cached_matches": len(cached),
            "publication_contract_relaxed": False,
        }
        primary_stats: dict[str, Any] = {}
        if not _V2_CIRCUIT_OPEN:
            fresh, raw_stats = await original(settings, matches, base, amap)
            primary_stats = dict(raw_stats or {})
            merge_module.merge(offers, fresh)
            if _server_failure(primary_stats):
                _open_circuit(primary_stats, "odds_best_v2")
        else:
            primary_stats = {
                "skipped": True,
                "reason": "process_local_v2_5xx_circuit_open",
                "circuit_reason": _V2_CIRCUIT_REASON,
            }
        stats["v2_primary"] = primary_stats

        missing = [match for match in matches if str(getattr(match, "match_key", "")) not in offers]
        if missing and (_V2_CIRCUIT_OPEN or _server_failure(primary_stats)):
            fallback, fallback_stats = await _v1_best_odds(merge_module, settings, missing, base)
            merge_module.merge(offers, fallback)
            stats["v1_server_failure_fallback"] = fallback_stats
        else:
            stats["v1_server_failure_fallback"] = {"skipped": True, "reason": "not_needed"}

        if offers:
            _OFFER_CACHE.update({key: list(value or []) for key, value in offers.items() if value})
            _OFFER_CACHE_STATS.clear()
            _OFFER_CACHE_STATS.update(
                {
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "v2_circuit_open": _V2_CIRCUIT_OPEN,
                    "v2_circuit_reason": _V2_CIRCUIT_REASON,
                }
            )
            coverage_ledger.record_provider_result("bzzoiro", "fetch_offers", offers, stats)
        stats["offers_parsed"] = sum(len(value or []) for value in offers.values())
        stats["matches_with_offers"] = len([1 for value in offers.values() if value])
        _write({"last_offer_stats": stats})
        return offers, stats

    fetch_bzzoiro._harizon_bzzoiro_v2_outage_fallback = True  # type: ignore[attr-defined]
    return fetch_bzzoiro


def _direct_offer_factory(merge_module: Any):
    async def fetch_offers(self: Any, matches: list[Any]):
        offers, stats = await merge_module.fetch_bzzoiro(
            getattr(self, "settings", None), list(matches or []), {}, {}
        )
        preview = {
            "mode": "shared_batch_best_with_v1_server_failure_fallback",
            "matches_with_offers": len([1 for value in offers.values() if value]),
            "v2_circuit_open": _V2_CIRCUIT_OPEN,
            "v2_circuit_reason": _V2_CIRCUIT_REASON,
        }
        return offers, stats, preview

    fetch_offers._harizon_bzzoiro_shared_batch_offers = True  # type: ignore[attr-defined]
    return fetch_offers


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_CONTEXT, _ORIGINAL_MERGE_FETCH
    if not _truthy(os.getenv("BZZOIRO_V2_OUTAGE_FALLBACK_ENABLED"), True):
        return {"status": "disabled_by_env"}
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider
        from app.services import sstats_bzzoiro_odds_merge_patch as merge_module

        current_context = BzzoiroContextProvider.fetch_context
        if not getattr(current_context, "_harizon_bzzoiro_v2_outage_fallback", False):
            _ORIGINAL_CONTEXT = current_context
            BzzoiroContextProvider.fetch_context = _context_factory(current_context)  # type: ignore[assignment]

        current_merge_fetch = merge_module.fetch_bzzoiro
        if not getattr(current_merge_fetch, "_harizon_bzzoiro_v2_outage_fallback", False):
            _ORIGINAL_MERGE_FETCH = current_merge_fetch
            merge_module.fetch_bzzoiro = _merge_fetch_factory(current_merge_fetch, merge_module)

        current_direct_offers = BzzoiroContextProvider.fetch_offers
        if not getattr(current_direct_offers, "_harizon_bzzoiro_shared_batch_offers", False):
            BzzoiroContextProvider.fetch_offers = _direct_offer_factory(merge_module)  # type: ignore[assignment]

        _INSTALLED = True
        result = {
            "status": "installed",
            "context_v1_server_failure_fallback": True,
            "odds_v1_server_failure_fallback": True,
            "shared_batch_offer_cache": True,
            "process_local_5xx_circuit_breaker": True,
            "hard_context_only": True,
            "publication_contract_relaxed": False,
        }
    except Exception as exc:
        result = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "publication_contract_relaxed": False,
        }
    _write(result)
    return result


__all__ = ["_hard_context", "_server_failure", "install"]
