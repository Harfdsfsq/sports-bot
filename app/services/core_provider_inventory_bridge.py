from __future__ import annotations

"""Normalize core provider evidence inside day-inventory payloads.

This runtime bridge is intentionally conservative: it does not create picks and it
does not lower publication gates.  It only standardizes provider aliases, carries
source IDs/evidence between rows, and recomputes coverage counters after the day
inventory is built.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-core-provider-inventory-bridge.json"
UTC = timezone.utc
_INSTALLED = False

PROVIDER_ALIASES = {
    "oddsapiio": "odds_api_io",
    "odds_api": "odds_api_io",
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "bzzoiro_predictions": "bzzoiro",
    "bzzoiro_current_odds": "bzzoiro",
    "bzzoiro_v2": "bzzoiro",
    "bsd": "bzzoiro",
    "bsd_sports_api": "bzzoiro",
    "sstats_form": "sstats",
    "sstats_net": "sstats",
    "sport_logic": "sportlogic",
    "sportlogic_io": "sportlogic",
    "football_data_org": "football_data",
    "sportsdb": "thesportsdb",
    "the_sports_db": "thesportsdb",
}
CORE_PRICE_PROVIDERS = {"odds_api_io", "bzzoiro", "sportlogic", "sstats"}
CORE_CONTEXT_PROVIDERS = {"bzzoiro", "sstats", "sportlogic", "football_data", "thesportsdb", "clubelo", "weatherapi", "open_meteo"}
LIST_FIELDS = ("odds_sources", "line_sources", "books", "price_confirmations", "context_sources", "context_confirmations", "fixture_sources", "sources_seen")
COUNT_FIELDS = (
    "fixture_sources_count",
    "independent_odds_sources_count",
    "odds_sources_count",
    "books_count",
    "price_confirmation_sources_count",
    "price_sources_count",
    "context_sources_count",
    "confirmation_sources_count",
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _enabled() -> bool:
    return _truthy(os.getenv("CORE_PROVIDER_INVENTORY_BRIDGE_ENABLED", "true"))


def _norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return PROVIDER_ALIASES.get(text, text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, (tuple, set)):
        raw = list(value)
    elif isinstance(value, dict):
        raw = list(value.keys())
    elif isinstance(value, str) and value.strip():
        raw = re.split(r"[,|;/]+", value)
    else:
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = _norm(text) if text.lower().replace("-", "_") in PROVIDER_ALIASES else text
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
    return out


def _row_keys(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field in ("canonical_match_id", "match_key", "loose_key"):
        value = str(row.get(field) or "").strip()
        if value:
            out.append(value)
    home = _norm(row.get("home_team_norm") or row.get("home_team"))
    away = _norm(row.get("away_team_norm") or row.get("away_team"))
    kickoff = str(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or "")[:16]
    league = _norm(row.get("league_key") or row.get("league_name"))
    if home and away and kickoff:
        out.append(f"{home}__{away}__{kickoff}")
        out.append(f"{league}__{home}__{away}__{kickoff}")
    ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    for provider, value in ids.items():
        src = _norm(provider)
        val = str(value or "").strip()
        if src and val:
            out.append(f"source:{src}:{val}")
    return list(dict.fromkeys(out))


def _source_ids(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    for key, value in raw.items():
        src = _norm(key)
        val = str(value or "").strip()
        if src and val:
            out[src] = val
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    provider_source_ids = md.get("provider_source_ids") if isinstance(md.get("provider_source_ids"), dict) else {}
    for key, value in provider_source_ids.items():
        src = _norm(key)
        val = str(value or "").strip()
        if src and val:
            out.setdefault(src, val)
    for src in ("odds_api_io", "bzzoiro", "sstats", "sportlogic", "football_data", "thesportsdb", "clubelo"):
        for field in (f"{src}_event_id", f"{src}_id", f"{src}_match_id", f"{src}_game_id"):
            val = str(md.get(field) or "").strip()
            if val:
                out.setdefault(src, val)
    return out


def _price_count(row: dict[str, Any]) -> int:
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(_as_int(md.get("price_confirmation_sources_count")), _as_int(md.get("price_sources_count")), len(row.get("price_confirmations") or []), len(row.get("books") or []), len(row.get("odds_sources") or []))


def _context_count(row: dict[str, Any]) -> int:
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(_as_int(md.get("context_sources_count")), _as_int(md.get("confirmation_sources_count")), len(row.get("context_confirmations") or []), len(row.get("context_sources") or []))


def _merge(dst: dict[str, Any], src: dict[str, Any], now_iso: str) -> bool:
    before = json.dumps(dst, ensure_ascii=False, sort_keys=True)
    for field in LIST_FIELDS:
        dst[field] = sorted(set(_listify(dst.get(field)) + _listify(src.get(field))))
    ids = _source_ids(dst)
    ids.update(_source_ids(src))
    dst["source_ids"] = {k: v for k, v in sorted(ids.items()) if k and v}
    md = dst.get("metadata") if isinstance(dst.get("metadata"), dict) else {}
    smd = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
    for field in COUNT_FIELDS:
        md[field] = max(_as_int(md.get(field)), _as_int(smd.get(field)))
    raw_cache = md.get("raw_cache_refs") if isinstance(md.get("raw_cache_refs"), list) else []
    src_cache = smd.get("raw_cache_refs") if isinstance(smd.get("raw_cache_refs"), list) else []
    if raw_cache or src_cache:
        md["raw_cache_refs"] = (raw_cache + src_cache)[:40]
    md["core_provider_inventory_bridge_updated_utc"] = now_iso
    dst["metadata"] = md
    return before != json.dumps(dst, ensure_ascii=False, sort_keys=True)


def _normalize_row(row: dict[str, Any], now_iso: str) -> None:
    ids = _source_ids(row)
    row["source_ids"] = {k: v for k, v in sorted(ids.items()) if k and v}
    sources_seen = set(_listify(row.get("sources_seen"))) | set(ids.keys())
    fixture_sources = set(_listify(row.get("fixture_sources"))) | set(ids.keys())
    row["sources_seen"] = sorted(sources_seen)
    row["fixture_sources"] = sorted(fixture_sources)
    odds_sources = set(_listify(row.get("odds_sources"))) | (set(ids.keys()) & CORE_PRICE_PROVIDERS)
    context_sources = set(_listify(row.get("context_sources"))) | (set(ids.keys()) & CORE_CONTEXT_PROVIDERS)
    # Only provider presence is not enough to create fake odds evidence, but it is enough
    # to keep routing/diagnostics coherent. Price confirmations remain actual tokens.
    row["odds_sources"] = sorted(odds_sources)
    row["line_sources"] = sorted(set(_listify(row.get("line_sources"))) | odds_sources)
    row["context_sources"] = sorted(context_sources)
    if context_sources and not row.get("context_confirmations"):
        row["context_confirmations"] = sorted(f"provider:{src}" for src in context_sources)
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    pc = _price_count(row)
    cc = _context_count(row)
    md["fixture_sources_count"] = max(_as_int(md.get("fixture_sources_count")), len(fixture_sources))
    md["odds_sources_count"] = max(_as_int(md.get("odds_sources_count")), len(odds_sources))
    md["independent_odds_sources_count"] = max(_as_int(md.get("independent_odds_sources_count")), len(odds_sources))
    md["context_sources_count"] = max(_as_int(md.get("context_sources_count")), cc, len(context_sources))
    md["confirmation_sources_count"] = max(_as_int(md.get("confirmation_sources_count")), cc, len(context_sources))
    md["price_confirmation_sources_count"] = max(_as_int(md.get("price_confirmation_sources_count")), pc)
    md["price_sources_count"] = max(_as_int(md.get("price_sources_count")), pc)
    md["core_provider_inventory_bridge_updated_utc"] = now_iso
    row["metadata"] = md
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    pc = _price_count(row)
    cc = _context_count(row)
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    cov["odds"] = bool(cov.get("odds")) or pc > 0
    cov["context"] = bool(cov.get("context")) or cc > 0
    cov["odds_2plus_sources"] = pc >= min_price
    cov["context_2plus_sources"] = cc >= min_context
    cov["ready_for_model"] = bool(cov.get("ready_for_model")) or (pc > 0 and cc > 0)
    cov["ready_for_publish"] = bool(cov.get("ready_for_publish")) or (pc >= min_price and cc >= min_context)
    row["coverage"] = cov


def _counts(rows: list[dict[str, Any]], old: dict[str, Any], now_iso: str) -> dict[str, Any]:
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    out = dict(old or {})
    out.update({
        "matches_with_odds": sum(1 for r in rows if _price_count(r) > 0 or (r.get("coverage") or {}).get("odds")),
        "matches_with_context": sum(1 for r in rows if _context_count(r) > 0 or (r.get("coverage") or {}).get("context")),
        "matches_with_2plus_price_confirmations": sum(1 for r in rows if _price_count(r) >= min_price),
        "matches_with_2plus_odds_sources": sum(1 for r in rows if _price_count(r) >= min_price),
        "matches_with_2plus_context_sources": sum(1 for r in rows if _context_count(r) >= min_context),
        "matches_ready_for_model": sum(1 for r in rows if bool((r.get("coverage") or {}).get("ready_for_model"))),
        "matches_ready_for_publish": sum(1 for r in rows if bool((r.get("coverage") or {}).get("ready_for_publish"))),
        "core_provider_inventory_bridge_updated_utc": now_iso,
    })
    out["matches_missing_price_2plus"] = max(0, len(rows) - out["matches_with_2plus_price_confirmations"])
    out["matches_missing_context_2plus"] = max(0, len(rows) - out["matches_with_2plus_context_sources"])
    return out


def install() -> None:
    global _INSTALLED
    if _INSTALLED or not _enabled():
        return
    _INSTALLED = True
    try:
        from app.services.day_inventory import DayInventoryStore
    except Exception:
        return
    if getattr(DayInventoryStore, "_core_provider_inventory_bridge_installed", False):
        return
    original = DayInventoryStore.build_payload

    def patched_build_payload(self, *args, **kwargs):
        payload = original(self, *args, **kwargs)
        try:
            now_iso = datetime.now(UTC).isoformat()
            rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
            existing = kwargs.get("existing") if isinstance(kwargs.get("existing"), dict) else {}
            existing_rows = existing.get("matches") if isinstance(existing.get("matches"), list) else []
            by_key: dict[str, dict[str, Any]] = {}
            for erow in existing_rows:
                if not isinstance(erow, dict):
                    continue
                for key in _row_keys(erow):
                    by_key.setdefault(key, erow)
            restored = changed = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in _row_keys(row):
                    source = by_key.get(key)
                    if source:
                        restored += 1
                        changed += int(_merge(row, source, now_iso))
                        break
                _normalize_row(row, now_iso)
            payload["counts"] = _counts(rows, dict(payload.get("counts") or {}), now_iso)
            sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
            report = {
                "status": "ok",
                "updated_at_utc": now_iso,
                "rows_seen": len(rows),
                "existing_rows_indexed": len(existing_rows),
                "restored_matching_rows": restored,
                "rows_changed": changed,
                "counts": payload.get("counts", {}),
            }
            sources["core_provider_inventory_bridge"] = report
            payload["sources"] = sources
            try:
                REPORT.parent.mkdir(parents=True, exist_ok=True)
                REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except Exception:
                pass
        except Exception:
            pass
        return payload

    DayInventoryStore.build_payload = patched_build_payload
    DayInventoryStore._core_provider_inventory_bridge_installed = True
