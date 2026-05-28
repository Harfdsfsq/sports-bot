from __future__ import annotations

"""Targeted enrichment queue for HARIZON runtime.

This module is intentionally API-free: it only ranks matches and caps provider
shortlists per run.  It is used to make paid/free-quota providers work as
shortlist enrichers instead of broad 300-match scanners.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return max(minimum, int(default))
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, int(default))


def load_json_any(path: str | Path, default: Any = None) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def ensure_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            from app.utils import parse_datetime

            dt = parse_datetime(value)
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def match_key_of(match: Any) -> str:
    return str(getattr(match, "match_key", "") or "").strip()


def row_match_key(row: dict[str, Any]) -> str:
    for key in ("match_key", "canonical_match_id", "event_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    home = str(row.get("home_team") or row.get("home") or "").strip().lower()
    away = str(row.get("away_team") or row.get("away") or "").strip().lower()
    kickoff = str(row.get("commence_time") or row.get("kickoff") or row.get("start_time") or "").strip()
    return f"{home}|{away}|{kickoff}"


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("candidates", "items", "rows", "evaluated", "selected_all", "selected"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def load_value_priority() -> dict[str, float]:
    """Return match_key -> priority from current run candidate artifacts."""
    priority: dict[str, float] = {}
    paths = [
        ".data/exports/latest-rescue-candidates.json",
        "artifacts/run-bot/latest-rescue-candidates.json",
        ".data/exports/latest-controlled-fallback-report.json",
        "artifacts/controlled-fallback-report.json",
        ".logs/debug-last-run.json",
    ]
    for path in paths:
        payload = load_json_any(path)
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict) and isinstance(payload.get("candidates_before_quality"), list):
            rows.extend([x for x in payload.get("candidates_before_quality") if isinstance(x, dict)])
        if isinstance(payload, dict) and isinstance(payload.get("candidates_after_quality"), list):
            rows.extend([x for x in payload.get("candidates_after_quality") if isinstance(x, dict)])
        rows.extend(_rows_from_payload(payload))
        for idx, row in enumerate(rows):
            key = row_match_key(row)
            if not key:
                continue
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            ev = row.get("ev_pct") or row.get("canonical_ev_pct") or metrics.get("canonical_ev_pct") or metrics.get("ev_pct")
            edge = row.get("edge_pct") or row.get("canonical_edge_pp") or metrics.get("canonical_edge_pp") or metrics.get("edge_pct")
            quality = row.get("quality_score") or metrics.get("quality_score") or row.get("confidence") or metrics.get("confidence")
            try:
                score = max(0.0, float(ev or 0.0)) * 1.8 + max(0.0, float(edge or 0.0)) * 2.2 + max(0.0, float(quality or 0.0)) / 30.0
            except Exception:
                score = 1.0
            priority[key] = max(priority.get(key, 0.0), score + max(0.0, 0.001 * (len(rows) - idx)))
    return priority


def load_waiting_line_movement_keys() -> set[str]:
    keys: set[str] = set()
    payload = load_json_any(".data/candidate-lifecycle-state.json", {})
    rows: list[Any] = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                rows.extend(value)
            elif isinstance(value, dict):
                rows.append(value)
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or row.get("publication_lifecycle_status") or "").lower()
        reasons = " ".join(str(x).lower() for x in row.get("reasons") or row.get("reject_reasons") or [])
        if "line" in status or "movement" in status or "line" in reasons:
            key = row_match_key(row)
            if key:
                keys.add(key)
    return keys


def load_context_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in (".data/exports/latest-context-source-index.json", ".data/provider_cache/context-source-index/latest.json"):
        payload = load_json_any(path, {})
        if not isinstance(payload, dict):
            continue
        raw = payload.get("matches") or payload.get("rows") or payload.get("items") or payload
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    sources = value.get("sources") or value.get("context_sources") or []
                    counts[str(key)] = max(counts.get(str(key), 0), len(sources) if isinstance(sources, list) else int(value.get("count") or 0))
        elif isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                key = row_match_key(row)
                sources = row.get("sources") or row.get("context_sources") or []
                count = len(sources) if isinstance(sources, list) else int(row.get("count") or row.get("context_source_count") or 0)
                if key:
                    counts[key] = max(counts.get(key, 0), count)
    return counts


def provider_default_limit(provider_key: str) -> int:
    defaults = {
        "sstats": 120,
        "bzzoiro": 48,
        "thesportsdb": 24,
        "football_data": 14,
        "futrixmetrics": 4,
        "api_football": 8,
        "allsportsapi": 8,
        "newsapi": 3,
        "gnews": 3,
        "weather": 12,
        "openfootball": 80,
        "openligadb": 18,
        "espn": 18,
        "sportlogic": 0,
    }
    return defaults.get(provider_key, 12)


def provider_limit(provider_key: str, fallback: int | None = None) -> int:
    key = str(provider_key or "").strip().lower()
    upper = key.upper().replace("-", "_")
    hard = env_int("TARGETED_ENRICHMENT_MAX_MATCHES_PER_PROVIDER", 120, 0)
    default = provider_default_limit(key) if fallback is None else fallback
    limit = env_int(f"TARGETED_ENRICHMENT_{upper}_MATCH_LIMIT", default, 0)
    return min(limit, hard) if hard > 0 else limit


def rank_matches(
    matches: list[Any],
    provider_key: str,
    offers_by_match: dict[str, list[Any]] | None = None,
    *,
    context_counts: dict[str, int] | None = None,
    value_priority: dict[str, float] | None = None,
    waiting_line_keys: set[str] | None = None,
) -> list[Any]:
    offers_by_match = offers_by_match or {}
    context_counts = context_counts or load_context_counts()
    value_priority = value_priority or load_value_priority()
    waiting_line_keys = waiting_line_keys or load_waiting_line_movement_keys()
    now = datetime.now(UTC)
    key = str(provider_key or "").strip().lower()
    ranked: list[tuple[tuple[float, ...], Any]] = []
    for match in matches:
        if str(getattr(match, "sport_key", "") or "") != "soccer":
            continue
        match_key = match_key_of(match)
        kickoff = ensure_utc(getattr(match, "commence_time", None)) or now
        seconds = max((kickoff - now).total_seconds(), 0.0)
        hours = seconds / 3600.0
        window = 6.0 if hours <= 4 else 5.0 if hours <= 8 else 4.0 if hours <= 12 else 3.0 if hours <= 24 else 1.0
        offers = list(offers_by_match.get(match_key) or [])
        odds_sources = {str(getattr(offer, "source", "") or "").lower() for offer in offers if str(getattr(offer, "source", "") or "").strip()}
        books = {str(getattr(offer, "bookmaker", "") or "").lower() for offer in offers if str(getattr(offer, "bookmaker", "") or "").strip()}
        families = {str(getattr(offer, "family", "") or "").lower() for offer in offers if str(getattr(offer, "family", "") or "").strip()}
        ctx_count = int(context_counts.get(match_key, 0) or 0)
        context_gap = 1.0 if ctx_count < 2 and offers else 0.0
        second_odds_gap = 1.0 if len(odds_sources) < 2 and offers else 0.0
        value = float(value_priority.get(match_key, 0.0) or 0.0)
        waiting = 1.0 if match_key in waiting_line_keys else 0.0
        source_id_bonus = 0.0
        metadata = getattr(match, "metadata", {}) if isinstance(getattr(match, "metadata", {}), dict) else {}
        source_ids = metadata.get("day_inventory_source_ids") if isinstance(metadata.get("day_inventory_source_ids"), dict) else {}
        if key and (source_ids.get(key) or metadata.get(f"{key}_event_id") or metadata.get(f"{key}_id")):
            source_id_bonus = 1.5
        cheap_alias_bonus = 0.5 if key in {"thesportsdb", "openfootball", "openligadb"} else 0.0
        rank = (
            value,
            waiting,
            context_gap,
            second_odds_gap,
            window,
            source_id_bonus,
            float(len(offers)),
            float(len(books)),
            float(len(families)),
            cheap_alias_bonus,
            -seconds,
        )
        ranked.append((rank, match))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [match for _, match in ranked]


def select_for_provider(
    matches: list[Any],
    provider_key: str,
    offers_by_match: dict[str, list[Any]] | None = None,
    *,
    fallback_matches: list[Any] | None = None,
    base_limit: int | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    key = str(provider_key or "").strip().lower()
    limit = provider_limit(key, base_limit)
    combined: list[Any] = []
    seen: set[str] = set()
    for match in list(matches or []) + list(fallback_matches or []):
        mk = match_key_of(match)
        if not mk or mk in seen:
            continue
        seen.add(mk)
        combined.append(match)
    ranked = rank_matches(combined, key, offers_by_match)
    selected = ranked[:limit] if limit > 0 else []
    return selected, {
        "provider": key,
        "input_matches": len(combined),
        "selected_matches": len(selected),
        "limit": limit,
        "value_priority_items": len(load_value_priority()),
        "waiting_line_items": len(load_waiting_line_movement_keys()),
    }


def write_queue_report(rows: list[dict[str, Any]]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), "providers": rows}
        (EXPORT_DIR / "latest-targeted-enrichment-queue.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
