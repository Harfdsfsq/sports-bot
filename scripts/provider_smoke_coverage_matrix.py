from __future__ import annotations

"""Build a compact 300-match provider-smoke coverage matrix.

This script does not call external APIs. It reads artifacts produced by
provider-smoke.yml and converts them into a stable report that is easy to paste
back into ChatGPT:

- inventory coverage for the first N matches, nearest kickoff first;
- 2+ odds/context source progress by kickoff bucket;
- provider endpoint status summary;
- provider matching stage summary;
- concrete next matches that still need enrichment.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
OUT_DIR = ROOT / ".data" / "exports"
JSON_OUT = OUT_DIR / "provider-smoke-coverage-matrix.json"
TXT_OUT = OUT_DIR / "provider-smoke-coverage-matrix.txt"

ODDS_COUNT_KEYS = (
    "price_confirmation_sources_count",
    "latest_books_max",
    "books_count",
    "bookmaker_count",
    "bookmakers_count",
    "odds_sources_count",
    "latest_odds_sources_max",
    "price_sources_count",
    "independent_odds_sources_count",
    "exact_price_sources_count",
    "exact_sources_count",
)
CONTEXT_COUNT_KEYS = (
    "context_sources_count",
    "latest_context_sources_max",
    "confirmation_sources_count",
    "latest_confirmation_sources_max",
    "context_source_count",
    "xg_sources_count",
    "form_sources_count",
)


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value is not None else default
    except Exception:
        return default


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("PROVIDER_SMOKE_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(_app_tz()).date().isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value))
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _split_sources(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text.lower() not in {"none", "null", "unknown", "day_inventory", "inventory"}:
            out.append(text)
    return sorted(set(out))


def _containers(row: dict[str, Any]) -> list[dict[str, Any]]:
    containers = [row]
    stack = [row]
    seen = {id(row)}
    while stack and len(containers) < 40:
        cur = stack.pop()
        for key in (
            "coverage",
            "metadata",
            "source_summary",
            "market_summary",
            "price_summary",
            "integrity_report",
            "quality",
            "diagnostics",
            "model_inputs",
            "enrichment",
            "latest_run",
        ):
            value = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(value, dict) and id(value) not in seen:
                seen.add(id(value))
                containers.append(value)
                stack.append(value)
    return containers


def _source_count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    best = 0
    for container in _containers(row):
        for key in keys:
            best = max(best, _as_int(container.get(key)))
    return best


def _coverage_bool(row: dict[str, Any], key: str) -> bool:
    for container in _containers(row):
        if isinstance(container.get("coverage"), dict) and bool(container["coverage"].get(key)):
            return True
        if bool(container.get(key)) and key in {"odds", "context", "weather", "news", "xg", "form", "ready_for_model", "ready_for_publish"}:
            return True
    return False


def _fixture_sources(row: dict[str, Any]) -> list[str]:
    sources = set(_split_sources(row.get("sources_seen")))
    source_ids = row.get("source_ids")
    if isinstance(source_ids, dict):
        sources.update(str(k).strip() for k in source_ids.keys() if str(k).strip())
    source = str(row.get("source") or "").strip()
    if source:
        sources.add(source)
    return sorted(s for s in sources if s)


def _bucket(kickoff: datetime | None, now: datetime) -> str:
    if kickoff is None:
        return "unknown"
    hours = (kickoff - now).total_seconds() / 3600.0
    if hours < 0:
        return "started"
    if hours <= 2:
        return "0_2h"
    if hours <= 6:
        return "2_6h"
    if hours <= 12:
        return "6_12h"
    if hours <= 24:
        return "12_24h"
    return "24h_plus"


def _empty_bucket() -> dict[str, int]:
    return {
        "matches": 0,
        "fixture_2plus_sources": 0,
        "odds_any": 0,
        "odds_2plus_sources": 0,
        "context_any": 0,
        "context_2plus_sources": 0,
        "weather": 0,
        "news": 0,
        "xg": 0,
        "form": 0,
        "ready_for_model": 0,
        "ready_for_publish": 0,
    }


def _inventory_path(target_date: str) -> Path:
    candidates = [
        ROOT / ".data" / "day_inventory" / f"{target_date}.json",
        ROOT / ".data" / "day_inventory" / "latest.json",
        ROOT / ".data" / "day_inventory" / "current.json",
        ROOT / ".data" / "day_inventory" / "today.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _provider_status_summary(provider_payload: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for key in ("results", "checks"):
        value = provider_payload.get(key)
        if isinstance(value, list):
            rows.extend([item for item in value if isinstance(item, dict)])
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    not_ok = [
        {
            "provider": row.get("provider"),
            "group": row.get("group"),
            "status": row.get("status"),
            "http_status": row.get("http_status"),
            "reason": row.get("reason") or row.get("error") or row.get("note"),
            "rows_count": row.get("rows_count") or row.get("item_count"),
        }
        for row in rows
        if str(row.get("status") or "").lower() not in {"ok", "skipped_preserve_runtime_quota"}
           and not bool(row.get("ok"))
    ]
    return {
        "total_rows": len(rows),
        "by_status": dict(statuses),
        "not_ok_top": not_ok[:25],
    }


def _matching_summary(matching_payload: dict[str, Any]) -> dict[str, Any]:
    providers = matching_payload.get("providers") if isinstance(matching_payload.get("providers"), list) else []
    by_stage = Counter(str(row.get("failure_stage") or "unknown") for row in providers if isinstance(row, dict))
    compact = []
    for row in providers:
        if not isinstance(row, dict):
            continue
        compact.append({
            "provider": row.get("provider"),
            "role": row.get("provider_role"),
            "status": row.get("status"),
            "raw_rows": row.get("raw_rows"),
            "parsed_events": row.get("parsed_events"),
            "eligible_events": row.get("eligible_events"),
            "matched_to_odds_inventory": row.get("matched_to_odds_inventory"),
            "match_rate_pct": row.get("match_rate_pct"),
            "stage": row.get("failure_stage"),
            "adapter_version": row.get("adapter_version"),
        })
    return {
        "odds_inventory": {
            "raw_rows": (matching_payload.get("odds_inventory") or {}).get("raw_rows") if isinstance(matching_payload.get("odds_inventory"), dict) else None,
            "parsed_events": (matching_payload.get("odds_inventory") or {}).get("parsed_events") if isinstance(matching_payload.get("odds_inventory"), dict) else None,
            "status": (matching_payload.get("odds_inventory") or {}).get("status") if isinstance(matching_payload.get("odds_inventory"), dict) else None,
        },
        "by_stage": dict(by_stage),
        "providers": compact,
    }


def _row_summary(row: dict[str, Any], now: datetime, min_odds: int, min_context: int) -> dict[str, Any]:
    kickoff = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local"))
    odds_sources = _source_count(row, ODDS_COUNT_KEYS)
    context_sources = _source_count(row, CONTEXT_COUNT_KEYS)
    has_odds = _coverage_bool(row, "odds") or odds_sources > 0
    has_context = _coverage_bool(row, "context") or context_sources > 0
    fixture_sources = _fixture_sources(row)
    ready_for_model = _coverage_bool(row, "ready_for_model") or (has_odds and has_context)
    ready_for_publish = _coverage_bool(row, "ready_for_publish") or (odds_sources >= min_odds and context_sources >= min_context)
    missing: list[str] = []
    if len(fixture_sources) < 2:
        missing.append("fixture_source_2plus")
    if odds_sources < min_odds:
        missing.append("odds_source_2plus")
    if context_sources < min_context:
        missing.append("context_source_2plus")
    if not _coverage_bool(row, "weather"):
        missing.append("weather")
    if not _coverage_bool(row, "xg"):
        missing.append("xg")
    return {
        "match_key": row.get("match_key") or row.get("canonical_match_id"),
        "kickoff_utc": kickoff.isoformat() if kickoff else None,
        "bucket": _bucket(kickoff, now),
        "priority": _as_float(row.get("priority")),
        "league_name": row.get("league_name"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "fixture_sources": fixture_sources,
        "fixture_source_count": len(fixture_sources),
        "odds_sources": odds_sources,
        "context_sources": context_sources,
        "has_odds": has_odds,
        "has_context": has_context,
        "has_weather": _coverage_bool(row, "weather"),
        "has_news": _coverage_bool(row, "news"),
        "has_xg": _coverage_bool(row, "xg"),
        "has_form": _coverage_bool(row, "form"),
        "ready_for_model": ready_for_model,
        "ready_for_publish": ready_for_publish,
        "missing": missing,
    }


def _render(payload: dict[str, Any]) -> str:
    lines = [
        "# Provider smoke 300-match coverage matrix",
        "",
        f"- status: **{payload.get('status')}**",
        f"- target_date: {payload.get('target_date')}",
        f"- inventory_path: `{payload.get('inventory_path')}`",
        f"- inventory_matches_total: {payload.get('inventory_matches_total')}",
        f"- matrix_matches: {payload.get('matrix_matches')}/{payload.get('coverage_target')}",
        f"- full target reached: {payload.get('target_reached')}",
        f"- min odds/context sources: {payload.get('min_odds_sources')}/{payload.get('min_context_sources')}",
        "",
        "## Coverage by kickoff window",
        "",
        "| window | matches | fixtures 2+ | odds any | odds 2+ | context any | context 2+ | xG | weather | ready model | ready publish |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window in ("0_2h", "2_6h", "6_12h", "12_24h", "24h_plus", "started", "unknown"):
        row = payload.get("coverage_by_kickoff_window", {}).get(window)
        if not row:
            continue
        lines.append(
            f"| {window} | {row['matches']} | {row['fixture_2plus_sources']} | {row['odds_any']} | {row['odds_2plus_sources']} | "
            f"{row['context_any']} | {row['context_2plus_sources']} | {row['xg']} | {row['weather']} | {row['ready_for_model']} | {row['ready_for_publish']} |"
        )
    totals = payload.get("totals", {})
    lines += [
        "",
        "## Totals",
        "",
        f"- fixture_2plus_sources: {totals.get('fixture_2plus_sources')}/{payload.get('matrix_matches')}",
        f"- odds_2plus_sources: {totals.get('odds_2plus_sources')}/{payload.get('matrix_matches')}",
        f"- context_2plus_sources: {totals.get('context_2plus_sources')}/{payload.get('matrix_matches')}",
        f"- ready_for_model: {totals.get('ready_for_model')}/{payload.get('matrix_matches')}",
        f"- ready_for_publish: {totals.get('ready_for_publish')}/{payload.get('matrix_matches')}",
        "",
        "## Provider endpoint status",
    ]
    status_summary = payload.get("provider_status_summary", {})
    lines.append(f"- by_status: `{json.dumps(status_summary.get('by_status', {}), ensure_ascii=False)}`")
    not_ok = status_summary.get("not_ok_top") or []
    if not_ok:
        lines.append("- not_ok:")
        for item in not_ok[:12]:
            lines.append(f"  - {item.get('provider')}: {item.get('status')} http={item.get('http_status')} rows={item.get('rows_count')} reason={item.get('reason')}")
    match_summary = payload.get("matching_summary", {})
    lines += ["", "## Matching diagnostics"]
    lines.append(f"- odds_inventory: `{json.dumps(match_summary.get('odds_inventory', {}), ensure_ascii=False)}`")
    lines.append(f"- by_stage: `{json.dumps(match_summary.get('by_stage', {}), ensure_ascii=False)}`")
    for item in (match_summary.get("providers") or [])[:12]:
        lines.append(
            f"- {item.get('provider')}: stage={item.get('stage')} raw={item.get('raw_rows')} parsed={item.get('parsed_events')} "
            f"eligible={item.get('eligible_events')} matched={item.get('matched_to_odds_inventory')} rate={item.get('match_rate_pct')}% status={item.get('status')}"
        )
    lines += ["", "## Next enrichment queue"]
    for item in payload.get("next_enrichment_queue", [])[:30]:
        lines.append(
            f"- {item.get('bucket')} | {item.get('kickoff_utc')} | {item.get('home_team')} — {item.get('away_team')} | "
            f"odds={item.get('odds_sources')} context={item.get('context_sources')} fixtures={item.get('fixture_source_count')} missing={','.join(item.get('missing') or [])}"
        )
    lines.append("")
    lines.append("Attach `provider-smoke-coverage-matrix.json` plus provider/matching/full-data diagnostics when asking for the next fix.")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    target_date = _target_date()
    coverage_target = max(1, _as_int(os.getenv("PROVIDER_SMOKE_COVERAGE_TARGET") or os.getenv("DAY_INVENTORY_MAX_MATCHES"), 300))
    min_odds_sources = max(2, _as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context_sources = max(2, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))

    inventory_path = _inventory_path(target_date)
    inventory = _load_json(inventory_path, {})
    raw_matches = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    matches = [row for row in raw_matches if isinstance(row, dict)]

    def sort_key(row: dict[str, Any]) -> tuple[int, float, float, str, str]:
        kickoff = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local"))
        if kickoff is None:
            return (3, 999999.0, -_as_float(row.get("priority")), str(row.get("league_name") or ""), str(row.get("home_team") or ""))
        hours = (kickoff - now).total_seconds() / 3600.0
        started = 1 if hours < -2 else 0
        return (started, abs(max(hours, 0.0)), -_as_float(row.get("priority")), str(row.get("league_name") or ""), str(row.get("home_team") or ""))

    selected = sorted(matches, key=sort_key)[:coverage_target]
    summaries = [_row_summary(row, now, min_odds_sources, min_context_sources) for row in selected]

    by_bucket: dict[str, dict[str, int]] = defaultdict(_empty_bucket)
    totals = _empty_bucket()
    for item in summaries:
        bucket = str(item.get("bucket") or "unknown")
        slot = by_bucket[bucket]
        slot["matches"] += 1
        totals["matches"] += 1
        fixture_2plus = int(_as_int(item.get("fixture_source_count")) >= 2)
        odds_any = int(bool(item.get("has_odds")))
        odds_2plus = int(_as_int(item.get("odds_sources")) >= min_odds_sources)
        context_any = int(bool(item.get("has_context")))
        context_2plus = int(_as_int(item.get("context_sources")) >= min_context_sources)
        for key, value in (
            ("fixture_2plus_sources", fixture_2plus),
            ("odds_any", odds_any),
            ("odds_2plus_sources", odds_2plus),
            ("context_any", context_any),
            ("context_2plus_sources", context_2plus),
            ("weather", int(bool(item.get("has_weather")))),
            ("news", int(bool(item.get("has_news")))),
            ("xg", int(bool(item.get("has_xg")))),
            ("form", int(bool(item.get("has_form")))),
            ("ready_for_model", int(bool(item.get("ready_for_model")))),
            ("ready_for_publish", int(bool(item.get("ready_for_publish")))),
        ):
            slot[key] += value
            totals[key] += value

    queue = [
        item for item in summaries
        if _as_int(item.get("odds_sources")) < min_odds_sources or _as_int(item.get("context_sources")) < min_context_sources
    ][:80]

    provider_payload = _load_json(OUT_DIR / "latest-provider-smoke-diagnostics.json", {})
    if not provider_payload:
        provider_payload = _load_json(OUT_DIR / "latest-provider-smoke-fast.json", {}) or _load_json(OUT_DIR / "latest-provider-smoke.json", {})
    matching_payload = _load_json(OUT_DIR / "latest-provider-smoke-matching-diagnostics.json", {})
    full_payload = _load_json(OUT_DIR / "latest-api-full-data-enrichment.json", {})

    payload = {
        "created_at_utc": now.isoformat(),
        "status": "ok" if selected else "no_inventory_matches",
        "target_date": target_date,
        "inventory_path": str(inventory_path),
        "inventory_matches_total": len(matches),
        "coverage_target": coverage_target,
        "matrix_matches": len(selected),
        "target_reached": len(selected) >= coverage_target,
        "min_odds_sources": min_odds_sources,
        "min_context_sources": min_context_sources,
        "coverage_by_kickoff_window": dict(by_bucket),
        "totals": totals,
        "provider_status_summary": _provider_status_summary(provider_payload if isinstance(provider_payload, dict) else {}),
        "matching_summary": _matching_summary(matching_payload if isinstance(matching_payload, dict) else {}),
        "api_full_data_enrichment_status": {
            "status": full_payload.get("status") if isinstance(full_payload, dict) else None,
            "mode": full_payload.get("mode") if isinstance(full_payload, dict) else None,
            "summary": full_payload.get("summary") if isinstance(full_payload, dict) else None,
        },
        "next_enrichment_queue": queue,
        "sample_rows": summaries[:30],
        "notes": [
            "The matrix is sorted nearest kickoff first; this matches the desired 00:00 workflow where early matches are enriched before late matches.",
            "The target is 300/300 with 2+ odds confirmations and 2+ context sources. Any queue row is a concrete match still missing that target.",
            "This report reads workflow artifacts only; endpoint and matching fixes must be made in provider clients or provider-smoke diagnostics when a provider stage fails.",
        ],
    }
    _write_json(JSON_OUT, payload)
    _write(TXT_OUT, _render(payload))
    print(_render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
