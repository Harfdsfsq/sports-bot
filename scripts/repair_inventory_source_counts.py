from __future__ import annotations

"""Repair and normalize day-inventory source evidence.

The daily inventory must not only count coverage.  It must keep a stable top-300
fixture list and, for every match, persist enough evidence to know whether the
match has:

* fixture sources;
* independent odds API sources;
* price confirmations, usually distinct bookmakers/lines;
* context/confirmation sources;
* ready_for_model / ready_for_publish flags.

This script runs after runtime artifacts exist.  It is also safe in provider-smoke
when only fixture inventory exists: it will still normalize/cap the inventory and
keep fixture-source evidence.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import should_update_current_aliases, write_current_aliases

ROOT = Path(".").resolve()
UTC = timezone.utc
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
LINE_HISTORY_DIR = ROOT / ".data" / "line_history"
OUT = EXPORT_DIR / "latest-inventory-source-count-repair.json"
SUMMARY_PATH = EXPORT_DIR / "latest-day-inventory-summary.json"
LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic"}

CANDIDATE_PATHS = [
    EXPORT_DIR / "latest-rescue-candidates.json",
    EXPORT_DIR / "latest-candidates-before-quality.json",
    EXPORT_DIR / "latest-candidates-after-quality.json",
    EXPORT_DIR / "latest-candidates.json",
    EXPORT_DIR / "latest-controlled-fallback-report.json",
    ROOT / "artifacts" / "controlled-fallback-report.json",
]

COVERAGE_PATHS = [
    EXPORT_DIR / "latest-match-data-coverage-matches.json",
    EXPORT_DIR / "latest-provider-smoke-coverage-matrix.json",
    EXPORT_DIR / "provider-smoke-coverage-matrix.json",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date(now: datetime) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    return explicit or now.astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
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


def norm_source(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "odds_api_io_account1": "odds_api_io",
        "odds_api_io_account2": "odds_api_io",
        "the_odds_api": "odds_api_io",
        "bzzoiro_predictions": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "sstats_form": "sstats",
        "football_data_org": "football_data",
        "the_sports_db": "thesportsdb",
        "sportsdb": "thesportsdb",
    }
    return aliases.get(text, text)


def norm_book(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    aliases = {
        "bet365": "bet365",
        "unibet": "unibet",
        "betfair": "betfair_exchange",
        "betfairexchange": "betfair_exchange",
        "sbobet": "sbobet",
    }
    return aliases.get(compact, re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_"))


def uniq(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[,|;/]+", value) if v.strip()]
    return []


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return out
    for key in (
        "candidates",
        "rows",
        "data",
        "selected",
        "selected_all",
        "published_candidates",
        "top_candidates",
        "evaluated",
        "blocked_top",
        "near_miss",
        "publishable_candidates",
        "candidates_before_quality",
        "passed_candidates",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(dict(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.append(dict(value))
    decision = payload.get("decision")
    if isinstance(decision, dict):
        out.extend(candidate_rows(decision))
    return out


def raw_bucket(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    for container in (candidate, candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}):
        value = container.get("raw_bucket_offers") if isinstance(container, dict) else None
        if isinstance(value, list):
            return [dict(x) for x in value if isinstance(x, dict)]
    return []


def deep_count(row: dict[str, Any], *names: str) -> int:
    best = 0
    stack: list[Any] = [row]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for k, v in item.items():
                if k in names:
                    best = max(best, as_int(v))
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(item, list):
            stack.extend(v for v in item if isinstance(v, (dict, list)))
    return best


def evidence_template() -> dict[str, Any]:
    return {
        "odds_sources": set(),
        "price_confirmations": set(),
        "context_sources": set(),
        "context_confirmations": set(),
        "fixture_sources": set(),
        "books": set(),
        "line_sources": set(),
        "latest_odds_at": None,
        "latest_context_at": None,
        "samples": [],
        "counts": {},
    }


def add_sample(evidence: dict[str, Any], source: str, detail: dict[str, Any]) -> None:
    samples = evidence.setdefault("samples", [])
    if isinstance(samples, list) and len(samples) < 8:
        samples.append({"source": source, **{k: v for k, v in detail.items() if v not in (None, "", [], {})}})


def merge_count_max(dst: dict[str, Any], key: str, value: int) -> None:
    counts = dst.setdefault("counts", {})
    if isinstance(counts, dict):
        counts[key] = max(as_int(counts.get(key)), int(value or 0))


def candidate_evidence(candidate: dict[str, Any], path_name: str) -> dict[str, Any]:
    ev = evidence_template()
    offers = raw_bucket(candidate)
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    diagnostics = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}

    for offer in offers:
        source = norm_source(offer.get("source"))
        book = norm_book(offer.get("bookmaker"))
        market = str(offer.get("market_key") or offer.get("market_name") or offer.get("family") or "").strip()
        selection = str(offer.get("selection") or "").strip()
        point = str(offer.get("point") or "").strip()
        price = offer.get("price") or offer.get("odds") or offer.get("decimal")
        if source:
            ev["odds_sources"].add(source)
            ev["line_sources"].add(source)
        if book:
            ev["books"].add(book)
            token = f"book:{book}"
            if market or selection or point:
                token += f":{market}:{selection}:{point}"
            ev["price_confirmations"].add(token)
        elif source:
            ev["price_confirmations"].add(f"provider:{source}:{market}:{selection}:{point}")
        if as_float(price) > 1.0:
            add_sample(ev, path_name, {"provider": source, "bookmaker": book, "market": market, "selection": selection, "point": point, "price": price})

    for key in ("odds_sources", "price_sources", "exact_price_sources", "line_sources"):
        for src in list_from_any(candidate.get(key)) + list_from_any(summary.get(key)) + list_from_any(diagnostics.get(key)):
            srcn = norm_source(src)
            if srcn:
                ev["odds_sources"].add(srcn)
                ev["line_sources"].add(srcn)
                ev["price_confirmations"].add(f"provider:{srcn}")
    selected_source = norm_source(summary.get("selected_source") or summary.get("source") or candidate.get("source"))
    if selected_source and selected_source not in {"model", "market", "ensemble"}:
        ev["odds_sources"].add(selected_source)
        ev["price_confirmations"].add(f"provider:{selected_source}")

    for key in ("confirmation_sources", "context_sources", "providers", "merged_context_sources"):
        for src in list_from_any(candidate.get(key)) + list_from_any(summary.get(key)) + list_from_any(metrics.get(key)) + list_from_any(diagnostics.get(key)):
            srcn = norm_source(src)
            if srcn and srcn not in {"market", "odds_api_io", "ensemble"}:
                ev["context_sources"].add(srcn)
                ev["context_confirmations"].add(srcn)
    context_source = norm_source(summary.get("context_source") or candidate.get("context_source"))
    if context_source and context_source not in {"market", "odds_api_io", "ensemble"}:
        ev["context_sources"].add(context_source)
        ev["context_confirmations"].add(context_source)

    if candidate.get("expected_home") not in (None, "") or candidate.get("expected_away") not in (None, "") or metrics.get("xg_sanity"):
        ev["context_sources"].add("model_xg")
        ev["context_confirmations"].add("model_xg")
    if summary or metrics:
        add_sample(ev, path_name, {"summary_keys": sorted(str(k) for k in summary.keys())[:10], "metric_keys": sorted(str(k) for k in metrics.keys())[:10]})

    merge_count_max(ev, "independent_odds_sources_count", max(len(ev["odds_sources"]), deep_count(candidate, "odds_sources_count", "price_sources_count", "exact_sources_count", "independent_odds_sources_count")))
    merge_count_max(ev, "books_count", max(len(ev["books"]), deep_count(candidate, "books_count", "odds_books_count", "paired_books", "exact_line_bookmakers_count")))
    merge_count_max(ev, "price_confirmation_sources_count", max(len(ev["price_confirmations"]), as_int(ev["counts"].get("independent_odds_sources_count")), as_int(ev["counts"].get("books_count"))))
    merge_count_max(ev, "context_sources_count", max(len(ev["context_sources"]), deep_count(candidate, "context_sources_count", "confirmation_sources_count")))
    merge_count_max(ev, "confirmation_sources_count", max(len(ev["context_confirmations"]), as_int(ev["counts"].get("context_sources_count"))))
    ev["latest_odds_at"] = datetime.now(UTC).isoformat() if ev["odds_sources"] or ev["price_confirmations"] else None
    ev["latest_context_at"] = datetime.now(UTC).isoformat() if ev["context_sources"] or ev["context_confirmations"] else None
    return ev


def merge_evidence(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in ("odds_sources", "price_confirmations", "context_sources", "context_confirmations", "fixture_sources", "books", "line_sources"):
        dst.setdefault(key, set()).update(src.get(key) or set())
    for key in ("latest_odds_at", "latest_context_at"):
        if src.get(key):
            dst[key] = max(str(dst.get(key) or ""), str(src.get(key))) or src.get(key)
    dst.setdefault("samples", [])
    for sample in src.get("samples") or []:
        if len(dst["samples"]) < 8:
            dst["samples"].append(sample)
    counts = dst.setdefault("counts", {})
    for k, v in (src.get("counts") or {}).items():
        counts[k] = max(as_int(counts.get(k)), as_int(v))


def rows_by_match_from_candidates() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        if payload in (None, {}, []):
            continue
        for candidate in candidate_rows(payload):
            key = str(candidate.get("match_key") or candidate.get("canonical_match_id") or "").strip()
            if not key:
                continue
            ev = out.setdefault(key, evidence_template())
            merge_evidence(ev, candidate_evidence(candidate, path.name))
    return out


def rows_by_match_from_coverage() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in COVERAGE_PATHS:
        payload = load_json(path, None)
        rows: list[Any] = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            for key in ("matches", "rows", "data", "coverage", "matrix"):
                val = payload.get(key)
                if isinstance(val, list):
                    rows = val
                    break
        for row_raw in rows:
            if not isinstance(row_raw, dict):
                continue
            row = dict(row_raw)
            key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
            if not key:
                continue
            ev = out.setdefault(key, evidence_template())
            books = max(as_int(row.get("books_max")), as_int(row.get("books_count")), as_int(row.get("odds_books_count")))
            odds = max(as_int(row.get("odds_sources_max")), as_int(row.get("odds_sources_count")), as_int(row.get("sources_max")), as_int(row.get("sources_count")))
            context = max(as_int(row.get("context_sources_max")), as_int(row.get("confirmation_sources_max")), as_int(row.get("context_sources_count")), as_int(row.get("confirmation_sources_count")))
            for src in list_from_any(row.get("odds_sources")) + list_from_any(row.get("price_sources")):
                srcn = norm_source(src)
                if srcn:
                    ev["odds_sources"].add(srcn)
                    ev["price_confirmations"].add(f"provider:{srcn}")
            for src in list_from_any(row.get("context_sources")) + list_from_any(row.get("confirmation_sources")):
                srcn = norm_source(src)
                if srcn:
                    ev["context_sources"].add(srcn)
                    ev["context_confirmations"].add(srcn)
            for idx in range(books):
                ev["price_confirmations"].add(f"book_confirmation_{idx + 1}")
            merge_count_max(ev, "independent_odds_sources_count", odds)
            merge_count_max(ev, "books_count", books)
            merge_count_max(ev, "price_confirmation_sources_count", max(odds, books, len(ev["price_confirmations"])))
            merge_count_max(ev, "context_sources_count", max(context, len(ev["context_sources"])))
            merge_count_max(ev, "confirmation_sources_count", max(context, len(ev["context_confirmations"])))
            if odds or books:
                ev["latest_odds_at"] = datetime.now(UTC).isoformat()
            if context:
                ev["latest_context_at"] = datetime.now(UTC).isoformat()
            add_sample(ev, path.name, {"books": books, "odds_sources": odds, "context_sources": context})
    return out


def rows_by_match_from_line_history(local_date: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (LINE_HISTORY_DIR / f"{local_date}.json", LINE_HISTORY_DIR / "latest.json"):
        payload = load_json(path, {})
        lines = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines, dict):
            continue
        for entry in lines.values():
            if not isinstance(entry, dict):
                continue
            snap = entry.get("last_snapshot") if isinstance(entry.get("last_snapshot"), dict) else None
            if not snap:
                continue
            key = str(snap.get("match_key") or "").strip()
            if not key:
                continue
            ev = out.setdefault(key, evidence_template())
            ev["odds_sources"].add("line_history")
            ev["line_sources"].add("line_history")
            book = norm_book(snap.get("bookmaker"))
            if book:
                ev["books"].add(book)
                ev["price_confirmations"].add(f"book:{book}")
            else:
                ev["price_confirmations"].add("line_history")
            ev["latest_odds_at"] = snap.get("captured_at_utc") or payload.get("updated_at_utc") or datetime.now(UTC).isoformat()
            merge_count_max(ev, "independent_odds_sources_count", len(ev["odds_sources"]))
            merge_count_max(ev, "books_count", len(ev["books"]))
            merge_count_max(ev, "price_confirmation_sources_count", max(len(ev["price_confirmations"]), len(ev["books"]), len(ev["odds_sources"])))
            add_sample(ev, "line_history", {"bookmaker": book, "captured_at_utc": ev["latest_odds_at"]})
    return out


def collect_all_evidence(local_date: str) -> dict[str, dict[str, Any]]:
    evidence = rows_by_match_from_coverage()
    for source in (rows_by_match_from_candidates(), rows_by_match_from_line_history(local_date)):
        for key, value in source.items():
            current = evidence.setdefault(key, evidence_template())
            merge_evidence(current, value)
    return evidence


def existing_fixture_sources(row: dict[str, Any]) -> list[str]:
    sources = []
    for src in row.get("sources_seen") or []:
        srcn = norm_source(src)
        if srcn:
            sources.append(srcn)
    source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    for src in source_ids.keys():
        srcn = norm_source(src)
        if srcn:
            sources.append(srcn)
    source = norm_source(row.get("source"))
    if source:
        sources.append(source)
    return uniq(sources)


def apply_evidence_to_row(row: dict[str, Any], ev: dict[str, Any] | None, min_price: int, min_context: int, now_iso: str) -> bool:
    before = json.dumps(row, ensure_ascii=False, sort_keys=True)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    refresh = row.get("refresh") if isinstance(row.get("refresh"), dict) else {}

    fixture_sources = set(existing_fixture_sources(row))
    if ev:
        fixture_sources.update(ev.get("fixture_sources") or set())
    odds_sources = {src for src in (norm_source(x) for x in list_from_any(row.get("odds_sources")) + list_from_any(metadata.get("odds_sources"))) if src in LIVE_ODDS_SOURCES}
    price_confirmations = set(list_from_any(row.get("price_confirmations")) + list_from_any(metadata.get("price_confirmations")))
    context_sources = set(list_from_any(row.get("context_sources")) + list_from_any(metadata.get("context_sources")))
    context_confirmations = set(list_from_any(row.get("context_confirmations")) + list_from_any(metadata.get("context_confirmations")))
    line_sources = {src for src in (norm_source(x) for x in list_from_any(row.get("line_sources")) + list_from_any(metadata.get("line_sources"))) if src in LIVE_ODDS_SOURCES}
    books = set(list_from_any(row.get("books")) + list_from_any(metadata.get("books")))

    if ev:
        odds_sources.update(src for src in (norm_source(x) for x in (ev.get("odds_sources") or set())) if src in LIVE_ODDS_SOURCES)
        price_confirmations.update(ev.get("price_confirmations") or set())
        context_sources.update(ev.get("context_sources") or set())
        context_confirmations.update(ev.get("context_confirmations") or set())
        line_sources.update(src for src in (norm_source(x) for x in (ev.get("line_sources") or set())) if src in LIVE_ODDS_SOURCES)
        books.update(ev.get("books") or set())

    # Preserve existing numeric counts even if names are unavailable.
    numeric_independent = len(odds_sources | line_sources)
    numeric_books = max(as_int(metadata.get("books_count")), len(books))
    numeric_price = max(as_int(metadata.get("price_confirmation_sources_count")), as_int(metadata.get("price_sources_count")), len(price_confirmations), numeric_books)
    context_sources = {src for src in (norm_source(x) for x in context_sources) if src and not re.match(r"^context_(source|confirmation)_\d+$", src)}
    context_confirmations = {src for src in (norm_source(x) for x in context_confirmations) if src and not re.match(r"^context_(source|confirmation)_\d+$", src)}
    numeric_context = max(len(context_sources), len(context_confirmations))
    if ev and isinstance(ev.get("counts"), dict):
        counts = ev["counts"]
        numeric_books = max(numeric_books, as_int(counts.get("books_count")))
        numeric_price = max(numeric_price, as_int(counts.get("price_confirmation_sources_count")), as_int(counts.get("price_sources_count")), numeric_books)
        numeric_context = max(numeric_context, len(context_sources), len(context_confirmations))

    # Synthetic confirmation placeholders are explicit and only used when numeric depth exists but exact names were not exported.
    while len(price_confirmations) < numeric_price:
        price_confirmations.add(f"price_confirmation_{len(price_confirmations) + 1}")
    row["fixture_sources"] = sorted(fixture_sources)
    row["odds_sources"] = sorted(odds_sources)
    row["line_sources"] = sorted(line_sources or odds_sources)
    row["books"] = sorted(books)
    row["price_confirmations"] = sorted(price_confirmations)
    row["context_sources"] = sorted(context_sources)
    row["context_confirmations"] = sorted(context_confirmations)

    metadata.update({
        "fixture_sources_count": len(fixture_sources),
        "independent_odds_sources_count": numeric_independent,
        "odds_sources_count": numeric_independent,
        "books_count": numeric_books,
        "price_confirmation_sources_count": numeric_price,
        "price_sources_count": numeric_price,
        "context_sources_count": numeric_context,
        "confirmation_sources_count": numeric_context,
        "source_evidence_updated_utc": now_iso,
    })
    if ev and ev.get("samples"):
        metadata["source_evidence_samples"] = ev.get("samples")[:8]
    row["metadata"] = metadata

    has_odds = numeric_price > 0 or numeric_independent > 0 or bool(coverage.get("odds"))
    has_context = numeric_context > 0 or bool(coverage.get("context"))
    coverage["odds"] = has_odds
    coverage["context"] = has_context
    coverage["odds_2plus_sources"] = numeric_independent >= min_price
    coverage["context_2plus_sources"] = numeric_context >= min_context
    coverage["ready_for_model"] = bool(coverage.get("ready_for_model")) or (has_odds and has_context)
    coverage["ready_for_publish"] = numeric_price >= min_price and numeric_independent >= min_price and numeric_context >= min_context and has_odds and has_context
    row["coverage"] = coverage

    if has_odds:
        refresh["last_odds_refresh_utc"] = (ev or {}).get("latest_odds_at") or refresh.get("last_odds_refresh_utc") or now_iso
    if has_context:
        refresh["last_context_refresh_utc"] = (ev or {}).get("latest_context_at") or refresh.get("last_context_refresh_utc") or now_iso
    row["refresh"] = refresh
    row["last_enriched_at"] = max(str(row.get("last_enriched_at") or ""), refresh.get("last_odds_refresh_utc") or "", refresh.get("last_context_refresh_utc") or "") or row.get("last_enriched_at")
    after = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return before != after


def priority_score(row: dict[str, Any], now: datetime) -> tuple[int, float, float, int, str, str]:
    kickoff = parse_dt(row.get("kickoff_utc") or row.get("kickoff_local") or row.get("commence_time"))
    if kickoff is None:
        return (9, 9999.0, -as_float(row.get("priority")), 99, str(row.get("league_name") or ""), str(row.get("home_team") or ""))
    hours = (kickoff - now).total_seconds() / 3600.0
    if hours < -2:
        time_bucket = 8
    elif hours <= 6:
        time_bucket = 0
    elif hours <= 12:
        time_bucket = 1
    elif hours <= 24:
        time_bucket = 2
    elif hours <= 48:
        time_bucket = 3
    else:
        time_bucket = 4
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    evidence_bonus = 0.0
    evidence_bonus += min(20, len(row.get("price_confirmations") or []) * 5)
    evidence_bonus += min(20, len(row.get("context_confirmations") or []) * 5)
    evidence_bonus += 15 if coverage.get("ready_for_publish") else 0
    evidence_bonus += 10 if coverage.get("ready_for_model") else 0
    priority = as_float(row.get("priority")) + evidence_bonus
    tier_rank = 0 if str(row.get("tier") or "").lower() == "top" else 1 if str(row.get("tier") or "").lower() == "mid" else 2
    return (time_bucket, abs(hours), -priority, tier_rank, str(row.get("league_name") or ""), str(row.get("home_team") or ""))


def recompute_inventory_counts(matches: list[dict[str, Any]], previous: dict[str, Any], min_price: int, min_context: int, before_cut: int, high_watermark: int) -> dict[str, Any]:
    counts = dict(previous or {})
    price_2plus = odds_source_2plus = context_2plus = odds_any = context_any = ready_model = ready_publish = fixture_2plus = fixture_3plus = 0
    for row in matches:
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        fixture_count = len(row.get("fixture_sources") or [])
        price_sources = max(as_int((row.get("metadata") or {}).get("price_confirmation_sources_count")), len(row.get("price_confirmations") or []))
        odds_source_count = len({src for src in (norm_source(x) for x in list_from_any(row.get("odds_sources")) + list_from_any(row.get("line_sources"))) if src in LIVE_ODDS_SOURCES})
        context_sources = max(len(row.get("context_confirmations") or []), len(row.get("context_sources") or []))
        odds_any += int(bool(coverage.get("odds")) or price_sources > 0)
        context_any += int(bool(coverage.get("context")) or context_sources > 0)
        price_2plus += int(price_sources >= min_price)
        odds_source_2plus += int(odds_source_count >= min_price)
        context_2plus += int(context_sources >= min_context)
        ready_model += int(bool(coverage.get("ready_for_model")))
        ready_publish += int(price_sources >= min_price and odds_source_count >= min_price and context_sources >= min_context)
        fixture_2plus += int(fixture_count >= 2)
        fixture_3plus += int(fixture_count >= 3)
    counts.update({
        "matches_total": len(matches),
        "matches_total_before_top_selection": before_cut,
        "matches_after_top_cut": len(matches),
        "matches_with_odds": odds_any,
        "matches_with_context": context_any,
        "matches_with_2plus_price_confirmations": price_2plus,
        "matches_with_2plus_odds_sources": odds_source_2plus,
        "matches_with_2plus_context_sources": context_2plus,
        "matches_with_2plus_core_fixture_sources": fixture_2plus,
        "matches_with_3_core_fixture_sources": fixture_3plus,
        "matches_ready_for_model": ready_model,
        "matches_ready_for_publish": ready_publish,
        "publish_min_price_confirmations": min_price,
        "publish_min_odds_sources": min_price,
        "publish_min_context_sources": min_context,
    })
    counts["matches_missing_odds_source_2plus"] = max(0, len(matches) - odds_source_2plus)
    counts["matches_total_high_watermark"] = max(as_int(counts.get("matches_total_high_watermark")), high_watermark, before_cut, len(matches))
    return counts


def main() -> int:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    d = target_date(now)
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        inv = {"date_local": d, "matches": []}
    matches = [dict(row) for row in inv.get("matches", []) if isinstance(row, dict)]
    evidence = collect_all_evidence(d)
    min_price = max(2, as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    target_size = max(1, as_int(os.getenv("DAY_INVENTORY_TARGET_SIZE") or os.getenv("DAY_INVENTORY_MAX_MATCHES"), 300))
    if target_size < 300 and str(os.getenv("DAY_INVENTORY_FORCE_TOP_300") or "true").lower() in {"1", "true", "yes", "on", "force"}:
        target_size = 300

    repaired = 0
    for row in matches:
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        repaired += int(apply_evidence_to_row(row, evidence.get(key), min_price, min_context, now_iso))

    before_cut = len(matches)
    high_watermark = max(as_int((inv.get("counts") or {}).get("matches_total_high_watermark")), before_cut)
    matches.sort(key=lambda row: priority_score(row, now))
    kept = matches[:target_size]
    dropped = max(0, len(matches) - len(kept))

    inv["matches"] = kept
    inv["counts"] = recompute_inventory_counts(kept, inv.get("counts") if isinstance(inv.get("counts"), dict) else {}, min_price, min_context, before_cut, high_watermark)
    inv["updated_at_utc"] = now_iso
    inv["date_local"] = d
    inv["timezone"] = str(app_tz())
    sources = inv.setdefault("sources", {})
    if isinstance(sources, dict):
        sources["source_count_repair"] = {
            "updated_at_utc": now_iso,
            "evidence_matches": len(evidence),
            "rows_repaired": repaired,
            "matches_before_top_cut": before_cut,
            "matches_kept": len(kept),
            "matches_dropped_after_top_cut": dropped,
            "target_size": target_size,
            "min_price_confirmations": min_price,
            "min_context_sources": min_context,
        }

    write_json(inv_path, inv)
    alias_update = write_current_aliases(ROOT, d, inv, write_json)
    summary = {
        "date_local": d,
        "updated_at_utc": now_iso,
        "timezone": str(app_tz()),
        "build_status": inv.get("build_status") or "ok",
        "counts": inv.get("counts", {}),
        "source_match_counts": dict(inv.get("source_match_counts") or {}),
        "league_match_counts": dict(inv.get("league_match_counts") or {}),
        "sources": dict(inv.get("sources") or {}),
        "alias_update": alias_update,
    }
    if should_update_current_aliases(d):
        write_json(SUMMARY_PATH, summary)
    report = {
        "status": "ok",
        "date_local": d,
        "updated_at_utc": now_iso,
        "inventory_path": str(inv_path),
        "summary_path": str(SUMMARY_PATH) if should_update_current_aliases(d) else None,
        "alias_update": alias_update,
        "evidence_matches": len(evidence),
        "rows_repaired": repaired,
        "matches_before_top_cut": before_cut,
        "matches_kept": len(kept),
        "matches_dropped_after_top_cut": dropped,
        "target_size": target_size,
        "counts": inv.get("counts", {}),
        "notes": [
            "Inventory matches are capped to top-300 by kickoff/priority/evidence, while matches_total_high_watermark keeps the raw daily audit size.",
            "Each match now stores odds_sources, price_confirmations, context_sources, context_confirmations, fixture_sources and books.",
            "price_confirmation_sources_count uses distinct bookmaker prices as confirmations when only one odds API provider is available.",
            "ready_for_publish requires 2+ price confirmations, 2+ independent live odds providers, and 2+ context confirmations by default.",
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
