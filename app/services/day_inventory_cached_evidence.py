from __future__ import annotations

"""Preserve useful day-inventory evidence across rebuilds.

The day inventory can be rebuilt from fresh fixture rows whose canonical keys are
not always identical to a previous run.  This module keeps already collected odds
and context evidence when the same match can be matched by alternate stable keys.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
DEFAULT_REPORT_PATH = Path(".data/exports/latest-day-inventory-cached-evidence-preserve.json")

LIST_EVIDENCE_FIELDS = (
    "odds_sources",
    "line_sources",
    "books",
    "price_confirmations",
    "context_sources",
    "context_confirmations",
    "fixture_sources",
)
COUNT_METADATA_FIELDS = (
    "fixture_sources_count",
    "independent_odds_sources_count",
    "odds_sources_count",
    "books_count",
    "price_confirmation_sources_count",
    "price_sources_count",
    "context_sources_count",
    "confirmation_sources_count",
)
SAMPLE_METADATA_FIELDS = (
    "source_evidence_samples",
    "odds_api_io_backfill_samples",
    "context_source_projection_reasons",
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def preserve_cached_evidence_enabled() -> bool:
    return (
        str(os.getenv("APP_ENV") or "").strip().lower() == "provider-smoke-minimal-repair"
        or _truthy(os.getenv("PROVIDER_SMOKE_MINIMAL_REPAIR"))
        or _truthy(os.getenv("DAY_INVENTORY_PRESERVE_CACHED_EVIDENCE"))
    )


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _norm_key_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _row_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("canonical_match_id", "match_key", "loose_key"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(value)
    home = _norm_key_text(row.get("home_team"))
    away = _norm_key_text(row.get("away_team"))
    kickoff = str(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or "")[:16]
    league = _norm_key_text(row.get("league_name"))
    if home and away and kickoff:
        keys.append(f"{home}__{away}__{kickoff}")
        keys.append(f"{league}__{home}__{away}__{kickoff}")
    return list(dict.fromkeys(keys))


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in re.split(r"[,|;/]+", value) if item.strip()]
    return []


def _uniq(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        low = text.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(text)
    return out


def _price_count(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        _as_int(metadata.get("price_confirmation_sources_count")),
        _as_int(metadata.get("price_sources_count")),
        len(row.get("price_confirmations") or []),
        len(row.get("books") or []),
    )


def _context_count(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        _as_int(metadata.get("context_sources_count")),
        _as_int(metadata.get("confirmation_sources_count")),
        len(row.get("context_confirmations") or []),
        len(row.get("context_sources") or []),
    )


def _merge_cached_evidence(dst: dict[str, Any], src: dict[str, Any], now_iso: str) -> bool:
    before = json.dumps(dst, ensure_ascii=False, sort_keys=True)
    for field in LIST_EVIDENCE_FIELDS:
        dst[field] = _uniq(_listify(dst.get(field)) + _listify(src.get(field)))
    for field in ("price_backfill", "coverage_gaps"):
        src_value = src.get(field)
        if isinstance(src_value, dict):
            dst_value = dst.get(field) if isinstance(dst.get(field), dict) else {}
            merged = dict(dst_value)
            merged.update({key: value for key, value in src_value.items() if value not in (None, "", [], {})})
            dst[field] = merged

    src_metadata = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
    dst_metadata = dst.get("metadata") if isinstance(dst.get("metadata"), dict) else {}
    for field in COUNT_METADATA_FIELDS:
        dst_metadata[field] = max(_as_int(dst_metadata.get(field)), _as_int(src_metadata.get(field)))
    for field in SAMPLE_METADATA_FIELDS:
        if src_metadata.get(field) and not dst_metadata.get(field):
            dst_metadata[field] = src_metadata[field]
        elif isinstance(src_metadata.get(field), list) and isinstance(dst_metadata.get(field), list):
            dst_metadata[field] = (dst_metadata[field] + src_metadata[field])[:12]
    for field in ("odds_api_io_backfill_updated_utc", "source_evidence_updated_utc", "context_source_projection_updated_utc"):
        if src_metadata.get(field):
            dst_metadata[field] = max(str(dst_metadata.get(field) or ""), str(src_metadata[field])) or src_metadata[field]
    dst_metadata["cached_evidence_preserved_utc"] = now_iso
    dst["metadata"] = dst_metadata

    price_count = max(_price_count(dst), _price_count(src))
    context_count = max(_context_count(dst), _context_count(src))
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH"), 2))
    min_context = max(1, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 1))

    coverage = dst.get("coverage") if isinstance(dst.get("coverage"), dict) else {}
    src_coverage = src.get("coverage") if isinstance(src.get("coverage"), dict) else {}
    coverage["odds"] = bool(coverage.get("odds")) or bool(src_coverage.get("odds")) or price_count > 0
    coverage["context"] = bool(coverage.get("context")) or bool(src_coverage.get("context")) or context_count > 0
    coverage["odds_2plus_sources"] = price_count >= 2
    coverage["context_2plus_sources"] = context_count >= 2
    coverage["ready_for_model"] = bool(coverage.get("ready_for_model")) or bool(src_coverage.get("ready_for_model")) or (price_count > 0 and context_count > 0)
    coverage["ready_for_publish"] = bool(coverage.get("ready_for_publish")) or bool(src_coverage.get("ready_for_publish")) or (price_count >= min_price and context_count >= min_context)
    dst["coverage"] = coverage

    refresh = dst.get("refresh") if isinstance(dst.get("refresh"), dict) else {}
    src_refresh = src.get("refresh") if isinstance(src.get("refresh"), dict) else {}
    for field in ("last_odds_refresh_utc", "last_context_refresh_utc"):
        if src_refresh.get(field):
            refresh[field] = max(str(refresh.get(field) or ""), str(src_refresh[field])) or src_refresh[field]
    if refresh:
        dst["refresh"] = refresh
    dst["last_enriched_at"] = max(str(dst.get("last_enriched_at") or ""), str(src.get("last_enriched_at") or ""), now_iso)
    return before != json.dumps(dst, ensure_ascii=False, sort_keys=True)


def _recompute_counts(rows: list[dict[str, Any]], counts: dict[str, Any], now_iso: str) -> dict[str, Any]:
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH"), 2))
    min_context = max(1, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 1))
    price2 = context2 = odds_any = context_any = ready_model = ready_publish = 0
    for row in rows:
        price_count = _price_count(row)
        context_count = _context_count(row)
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        odds_any += int(bool(coverage.get("odds")) or price_count > 0)
        context_any += int(bool(coverage.get("context")) or context_count > 0)
        price2 += int(price_count >= 2)
        context2 += int(context_count >= 2)
        ready_model += int(bool(coverage.get("ready_for_model")))
        ready_publish += int(bool(coverage.get("ready_for_publish")))
    out = dict(counts or {})
    out.update({
        "matches_with_odds": odds_any,
        "matches_with_context": context_any,
        "matches_with_2plus_price_confirmations": price2,
        "matches_with_2plus_odds_sources": price2,
        "matches_with_2plus_context_sources": context2,
        "matches_ready_for_model": ready_model,
        "matches_ready_for_publish": ready_publish,
        "matches_missing_price_2plus": max(0, len(rows) - price2),
        "matches_missing_context_2plus": max(0, len(rows) - context2),
        "cached_evidence_preserve_updated_utc": now_iso,
    })
    return out


def preserve_cached_evidence(
    payload: dict[str, Any],
    existing: dict[str, Any] | None,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    if not preserve_cached_evidence_enabled():
        return payload
    existing = existing if isinstance(existing, dict) else {}
    existing_rows = existing.get("matches") if isinstance(existing.get("matches"), list) else []
    if not existing_rows:
        return payload

    now_iso = datetime.now(UTC).isoformat()
    source_by_key: dict[str, dict[str, Any]] = {}
    evidence_rows = 0
    for row in existing_rows:
        if not isinstance(row, dict):
            continue
        if _price_count(row) <= 0 and _context_count(row) <= 0:
            continue
        evidence_rows += 1
        for key in _row_keys(row):
            source_by_key.setdefault(key, row)

    changed = 0
    restored = 0
    rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = None
        for key in _row_keys(row):
            source = source_by_key.get(key)
            if source:
                break
        if source is None:
            continue
        restored += 1
        changed += int(_merge_cached_evidence(row, source, now_iso))

    payload["counts"] = _recompute_counts(rows, dict(payload.get("counts") or {}), now_iso)
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    report = {
        "updated_at_utc": now_iso,
        "existing_evidence_rows": evidence_rows,
        "restored_matching_rows": restored,
        "rows_changed": changed,
    }
    sources["cached_evidence_preserve"] = report
    payload["sources"] = sources

    path = Path(report_path) if report_path is not None else DEFAULT_REPORT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    return payload
