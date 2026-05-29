from __future__ import annotations

"""Targeted enrichment queue for HARIZON runtime.

This module is intentionally API-free: it only ranks matches and caps provider
shortlists per run.  It is used to make paid/free-quota providers work as
shortlist enrichers instead of broad 300-match scanners.

Important runtime note: this queue is installed before some post-run artifacts
(`coverage_truth`, `context-source-index`) are regenerated.  Therefore it must
also infer pending B-tier/coverage state directly from the current day inventory,
otherwise provider shortlists miss the very matches that are waiting for second
line snapshots.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"


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


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


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


def _norm_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_key_part(value: Any) -> str:
    return _norm_text(value).replace(" ", "_")


def _date_from_any(value: Any) -> str:
    dt = ensure_utc(value)
    return dt.date().isoformat() if dt else ""


def _row_date(row: dict[str, Any]) -> str:
    for key in ("kickoff_utc", "commence_time", "start_time", "kickoff"):
        value = row.get(key)
        if value:
            d = _date_from_any(value)
            if d:
                return d
    mk = str(row.get("match_key") or row.get("canonical_match_id") or "")
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", mk)
    return m.group(1) if m else ""


def match_key_of(match: Any) -> str:
    return str(getattr(match, "match_key", "") or "").strip()


def row_match_key(row: dict[str, Any]) -> str:
    for key in ("match_key", "canonical_match_id", "event_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    home = str(row.get("home_team") or row.get("home") or "").strip().lower()
    away = str(row.get("away_team") or row.get("away") or "").strip().lower()
    kickoff = str(row.get("commence_time") or row.get("kickoff_utc") or row.get("kickoff") or row.get("start_time") or "").strip()
    return f"{home}|{away}|{kickoff}"


def _key_variants(home: Any, away: Any, date: Any, base: str = "") -> set[str]:
    h = _norm_key_part(home)
    a = _norm_key_part(away)
    d = str(date or "").strip()[:10]
    out = {str(base or "").strip()} if str(base or "").strip() else set()
    if h and a and d:
        out.update({
            f"soccer|{h}|{a}|{d}",
            f"soccer|{a}|{h}|{d}",
            f"{d}|{h}|{a}",
            f"{d}|{a}|{h}",
            f"{h}|{a}|{d}",
            f"{a}|{h}|{d}",
        })
    return {x for x in out if x}


def row_key_variants(row: dict[str, Any]) -> set[str]:
    d = _row_date(row)
    return _key_variants(
        row.get("home_team") or row.get("home"),
        row.get("away_team") or row.get("away"),
        d,
        row_match_key(row),
    )


def match_key_variants(match: Any) -> set[str]:
    kickoff = ensure_utc(getattr(match, "commence_time", None))
    d = kickoff.date().isoformat() if kickoff else ""
    return _key_variants(
        getattr(match, "home_team", ""),
        getattr(match, "away_team", ""),
        d,
        match_key_of(match),
    )


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


def _inventory_payloads() -> list[dict[str, Any]]:
    d = target_date()
    paths = [
        DAY_INV_DIR / f"{d}.json",
        DAY_INV_DIR / "current.json",
        DAY_INV_DIR / "latest.json",
        ROOT / ".data" / "cache" / "day_inventory" / f"{d}.json",
        ROOT / ".data" / "cache" / "day_inventory" / "current.json",
        ROOT / ".data" / "cache" / "day_inventory" / "latest.json",
        ROOT / "artifacts" / "run-bot" / "day_inventory" / f"{d}.json",
    ]
    out: list[dict[str, Any]] = []
    for path in paths:
        payload = load_json_any(path, {})
        if isinstance(payload, dict):
            if str(payload.get("date_local") or d) not in {"", d}:
                continue
            out.append(payload)
    return out


def load_inventory_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in _inventory_payloads():
        raw = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        for row in raw:
            if not isinstance(row, dict):
                continue
            if _row_date(row) and _row_date(row) != target_date():
                continue
            sig = row_match_key(row) or repr(sorted(row.items()))[:240]
            if sig in seen:
                continue
            seen.add(sig)
            rows.append(row)
    return rows


def _list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[,|;/]+", value) if v.strip()]
    return []


def _count_from_any(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _coverage(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("coverage") if isinstance(row.get("coverage"), dict) else {}


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("metadata") if isinstance(row.get("metadata"), dict) else {}


def _row_context_sources(row: dict[str, Any]) -> set[str]:
    cov = _coverage(row)
    md = _metadata(row)
    raw: list[Any] = []
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in (
            "context_sources", "context_confirmations", "all_context_sources",
            "core_context_sources", "supplemental_context_sources",
        ):
            raw.extend(_list_from_any(container.get(key)))
    sources = {_norm_text(x).replace(" ", "_") for x in raw if _norm_text(x)}
    return {x for x in sources if x not in {"market", "ensemble", "odds_api_io", "line_history"}}


def _row_odds_sources(row: dict[str, Any]) -> set[str]:
    cov = _coverage(row)
    md = _metadata(row)
    raw: list[Any] = []
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in ("odds_sources", "line_sources", "all_odds_sources", "core_odds_sources"):
            raw.extend(_list_from_any(container.get(key)))
    aliases = {"oddsapiio": "odds_api_io", "odds_api": "odds_api_io", "bzzoiro_v2": "bzzoiro"}
    return {aliases.get(_norm_text(x).replace(" ", "_"), _norm_text(x).replace(" ", "_")) for x in raw if _norm_text(x)}


def _price_confirmations(row: dict[str, Any]) -> int:
    cov = _coverage(row)
    md = _metadata(row)
    return max(
        _count_from_any(row.get("price_confirmations")),
        _count_from_any(row.get("books")),
        _count_from_any(cov.get("books_count")),
        _count_from_any(md.get("books_count")),
        _count_from_any(md.get("latest_books_max")),
        _count_from_any(md.get("price_confirmation_sources_count")),
        _count_from_any(md.get("price_sources_count")),
    )


def _line_movement_status(row: dict[str, Any]) -> str:
    cov = _coverage(row)
    md = _metadata(row)
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in (
            "line_movement_status", "line_movement_lifecycle_status", "movement_status",
            "line_guard_status", "line_state", "movement_lifecycle_status",
        ):
            value = str(container.get(key) or "").strip().lower()
            if value:
                return value
    return ""


def _movement_confirmed(row: dict[str, Any]) -> bool:
    status = _line_movement_status(row)
    if any(token in status for token in ("confirmed", "passed", "kept", "movement_ok")):
        return True
    cov = _coverage(row)
    md = _metadata(row)
    for container in (row, cov, md):
        if isinstance(container, dict):
            for key in ("line_movement_confirmed", "movement_confirmed", "line_guard_kept", "has_second_line_snapshot"):
                if truthy(container.get(key)):
                    return True
    return False


def _movement_declined(row: dict[str, Any]) -> bool:
    status = _line_movement_status(row)
    return any(token in status for token in ("declined", "rejected", "dropped", "failed"))


def _inventory_waiting_keys() -> set[str]:
    keys: set[str] = set()
    for row in load_inventory_rows():
        cov = _coverage(row)
        has_odds = bool(cov.get("odds")) or _price_confirmations(row) >= 1 or bool(_row_odds_sources(row))
        has_context = bool(cov.get("context")) or bool(_row_context_sources(row))
        tier_b_like = has_odds and has_context and _price_confirmations(row) >= 1 and len(_row_odds_sources(row)) >= 1
        if tier_b_like and not _movement_confirmed(row) and not _movement_declined(row):
            keys.update(row_key_variants(row))
    return keys


def load_value_priority() -> dict[str, float]:
    """Return match_key/variant -> priority from current run candidate artifacts."""
    priority: dict[str, float] = {}
    paths = [
        ".data/exports/latest-rescue-candidates.json",
        "artifacts/run-bot/latest-rescue-candidates.json",
        ".data/exports/latest-controlled-fallback-report.json",
        "artifacts/controlled-fallback-report.json",
        ".logs/debug-last-run.json",
    ]
    current_date = target_date()
    for path in paths:
        payload = load_json_any(path)
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict) and isinstance(payload.get("candidates_before_quality"), list):
            rows.extend([x for x in payload.get("candidates_before_quality") if isinstance(x, dict)])
        if isinstance(payload, dict) and isinstance(payload.get("candidates_after_quality"), list):
            rows.extend([x for x in payload.get("candidates_after_quality") if isinstance(x, dict)])
        rows.extend(_rows_from_payload(payload))
        for idx, row in enumerate(rows):
            if _row_date(row) and _row_date(row) != current_date:
                continue
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            ev = row.get("ev_pct") or row.get("canonical_ev_pct") or metrics.get("canonical_ev_pct") or metrics.get("ev_pct")
            edge = row.get("edge_pct") or row.get("canonical_edge_pp") or metrics.get("canonical_edge_pp") or metrics.get("edge_pct")
            quality = row.get("quality_score") or metrics.get("quality_score") or row.get("confidence") or metrics.get("confidence")
            try:
                score = max(0.0, float(ev or 0.0)) * 1.8 + max(0.0, float(edge or 0.0)) * 2.2 + max(0.0, float(quality or 0.0)) / 30.0
            except Exception:
                score = 1.0
            variants = row_key_variants(row)
            if not variants:
                variants = {row_match_key(row)}
            for key in variants:
                priority[key] = max(priority.get(key, 0.0), score + max(0.0, 0.001 * (len(rows) - idx)))
    return priority


def load_waiting_line_movement_keys() -> set[str]:
    keys: set[str] = set()
    current_date = target_date()

    # Current/pre-run inventory inference is the most important source because the
    # post-run coverage truth artifact is rebuilt after provider shortlists run.
    keys.update(_inventory_waiting_keys())

    for path in (
        ".data/exports/latest-day-inventory-coverage-truth.json",
        "artifacts/run-bot/latest-day-inventory-coverage-truth.json",
    ):
        payload = load_json_any(path, {})
        rows = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _row_date(row) and _row_date(row) != current_date:
                continue
            if truthy(row.get("line_movement_waiting")):
                keys.update(row_key_variants(row))

    payload = load_json_any(".data/candidate-lifecycle-state.json", {})
    rows: list[Any] = []
    if isinstance(payload, dict):
        candidates = payload.get("candidates")
        if isinstance(candidates, dict):
            rows.extend(candidates.values())
        elif isinstance(candidates, list):
            rows.extend(candidates)
        for key, value in payload.items():
            if key == "candidates":
                continue
            if isinstance(value, list):
                rows.extend(value)
            elif isinstance(value, dict) and any(k in value for k in ("match_key", "kickoff_utc", "status", "last_value_ok")):
                rows.append(value)
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _row_date(row) and _row_date(row) != current_date:
            continue
        status = str(row.get("status") or row.get("publication_lifecycle_status") or "").lower()
        reasons = " ".join(str(x).lower() for x in row.get("reasons") or row.get("reject_reasons") or [])
        waiting_like = (
            "await" in status or "waiting" in status or "line" in status or "movement" in status or
            "line" in reasons or "movement" in reasons or truthy(row.get("last_value_ok"))
        )
        if waiting_like:
            keys.update(row_key_variants(row))
    return {k for k in keys if k}


def load_context_counts() -> dict[str, int]:
    counts: dict[str, int] = {}

    # First use the current day inventory.  This exists before targeted provider
    # shortlists are computed; latest-context-source-index is often post-run only.
    for row in load_inventory_rows():
        sources = _row_context_sources(row)
        for key in row_key_variants(row):
            counts[key] = max(counts.get(key, 0), len(sources))

    for path in (".data/exports/latest-context-source-index.json", ".data/provider_cache/context-source-index/latest.json"):
        payload = load_json_any(path, {})
        if not isinstance(payload, dict):
            continue
        raw = payload.get("by_match") or payload.get("matches") or payload.get("rows") or payload.get("items") or payload
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    sources = value.get("sources") or value.get("context_sources") or []
                    count = len(sources) if isinstance(sources, list) else int(value.get("count") or value.get("context_source_count") or 0)
                elif isinstance(value, list):
                    count = len(value)
                else:
                    count = _count_from_any(value)
                if str(key).strip():
                    counts[str(key)] = max(counts.get(str(key), 0), count)
        elif isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                sources = row.get("sources") or row.get("context_sources") or []
                count = len(sources) if isinstance(sources, list) else int(row.get("count") or row.get("context_source_count") or 0)
                for key in row_key_variants(row):
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


def _lookup_any(mapping: dict[str, Any], variants: set[str], default: Any = 0) -> Any:
    for key in variants:
        if key in mapping:
            return mapping[key]
    return default


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
        variants = match_key_variants(match)
        match_key = match_key_of(match)
        kickoff = ensure_utc(getattr(match, "commence_time", None)) or now
        seconds = max((kickoff - now).total_seconds(), 0.0)
        hours = seconds / 3600.0
        window = 6.0 if hours <= 4 else 5.0 if hours <= 8 else 4.0 if hours <= 12 else 3.0 if hours <= 24 else 1.0
        offers = list(offers_by_match.get(match_key) or [])
        odds_sources = {str(getattr(offer, "source", "") or "").lower() for offer in offers if str(getattr(offer, "source", "") or "").strip()}
        books = {str(getattr(offer, "bookmaker", "") or "").lower() for offer in offers if str(getattr(offer, "bookmaker", "") or "").strip()}
        families = {str(getattr(offer, "family", "") or "").lower() for offer in offers if str(getattr(offer, "family", "") or "").strip()}
        ctx_count = int(_lookup_any(context_counts, variants, 0) or 0)
        context_gap = 1.0 if ctx_count < 2 and (offers or ctx_count >= 1) else 0.0
        second_odds_gap = 1.0 if len(odds_sources) < 2 and offers else 0.0
        value = float(max(float(value_priority.get(v, 0.0) or 0.0) for v in variants) if variants else 0.0)
        waiting = 1.0 if variants.intersection(waiting_line_keys) else 0.0
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
    context_counts = load_context_counts()
    value_priority = load_value_priority()
    waiting_line_keys = load_waiting_line_movement_keys()
    ranked = rank_matches(
        combined,
        key,
        offers_by_match,
        context_counts=context_counts,
        value_priority=value_priority,
        waiting_line_keys=waiting_line_keys,
    )
    selected = ranked[:limit] if limit > 0 else []
    pool_variants: set[str] = set()
    for match in combined:
        pool_variants.update(match_key_variants(match))
    return selected, {
        "provider": key,
        "input_matches": len(combined),
        "selected_matches": len(selected),
        "limit": limit,
        "value_priority_items": len(value_priority),
        "value_priority_items_in_pool": sum(1 for k in value_priority if k in pool_variants),
        "waiting_line_items": sum(1 for match in combined if match_key_variants(match).intersection(waiting_line_keys)),
        "waiting_line_items_total": len(waiting_line_keys),
        "context_index_items": len(context_counts),
        "context_index_items_in_pool": sum(1 for match in combined if any(v in context_counts for v in match_key_variants(match))),
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
