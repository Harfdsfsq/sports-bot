from __future__ import annotations

"""Merge latest runtime line/context artifacts into the day inventory.

This bridge is intentionally conservative: it only copies evidence that already
exists in runtime artifacts into inventory coverage fields.  It does not create
picks or relax publication guards.  The previous version crashed when fields such
as price_confirmations/context_sources were integer counters, because it called
len() directly on scalars.  This version uses safe list/count helpers throughout.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
UTC = timezone.utc
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_DIR = ROOT / ".data" / "day_inventory"
OUT = EXPORT_DIR / "latest-runtime-context-coverage-bridge.json"
LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic"}
IGNORED_CONTEXT = {"", "market", "odds_api_io", "line_history", "ensemble"}
TEAM_STOPWORDS = {"fc", "cf", "sc", "afc", "fk", "ac", "bc", "club", "team", "jfc", "reserve", "reserves", "res", "women", "woman", "w", "u21", "u20", "u19", "ii", "iii", "b", "youth", "academy", "de", "the"}


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    return explicit[:10] if explicit else datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "odds_api_io_account1": "odds_api_io",
        "odds_api_io_account2": "odds_api_io",
        "bzzoiro_predictions": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "bzzoiro_v2": "bzzoiro",
        "sstats_form": "sstats",
        "xg_model_context": "model_xg",
    }
    return aliases.get(text, text)


def norm_team(value: Any) -> str:
    text = norm(value).replace("_", " ")
    tokens = [tok for tok in text.split() if tok and tok not in TEAM_STOPWORDS]
    return " ".join(tokens) or text


def short_team(value: Any) -> str:
    return " ".join(norm_team(value).split()[:3])


def list_any(value: Any) -> list[str]:
    if value in (None, "", False):
        return []
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, (int, float)):
        count = max(0, int(float(value)))
        return [f"count_{idx + 1}" for idx in range(count)]
    return [x.strip() for x in re.split(r"[,|;/]+", str(value)) if x.strip()]


def ensure_bucket(index: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return index.setdefault(key, {"context": set(), "line_sources": set(), "odds_sources": set(), "books": set(), "price": set(), "samples": []})


def merge_bucket(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in ("context", "line_sources", "odds_sources", "books", "price"):
        dst.setdefault(key, set()).update(src.get(key) or set())
    dst.setdefault("samples", [])
    for sample in src.get("samples") or []:
        if len(dst["samples"]) < 12:
            dst["samples"].append(sample)


def add_sample(bucket: dict[str, Any], source: str, detail: dict[str, Any]) -> None:
    samples = bucket.setdefault("samples", [])
    if isinstance(samples, list) and len(samples) < 8:
        samples.append({"source": source, **{k: v for k, v in detail.items() if v not in (None, "", [], {})}})


def rows_from(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, [])
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "items", "data", "matches", "observations", "snapshots", "lines"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, dict)]
    return []


def key_aliases_from_parts(date: str, home: str, away: str) -> set[str]:
    date = str(date or "").strip()[:10]
    home_n = norm_team(home)
    away_n = norm_team(away)
    if not date or not home_n or not away_n:
        return set()
    return {f"{date}|{home_n}|{away_n}", f"{date}|{short_team(home_n)}|{short_team(away_n)}", f"{date}|{home_n[:18]}|{away_n[:18]}"}


def key_aliases_from_key(raw_key: Any) -> set[str]:
    raw = str(raw_key or "").strip()
    if not raw:
        return set()
    out = {raw, norm(raw)}
    tokens = [tok.strip() for tok in raw.split("|") if tok.strip()]
    date = ""
    for tok in tokens:
        match = re.search(r"20\d{2}-\d{2}-\d{2}", tok)
        if match:
            date = match.group(0)
            break
    text_tokens = [tok for tok in tokens if not re.search(r"20\d{2}-\d{2}-\d{2}", tok) and norm(tok) not in {"soccer", "football"}]
    if date and len(text_tokens) >= 2:
        out.update(key_aliases_from_parts(date, text_tokens[0], text_tokens[1]))
    return out


def row_aliases(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in (row.get("match_key"), row.get("canonical_match_id")):
        out.update(key_aliases_from_key(key))
    raw_date = str(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or "")[:10]
    home = row.get("home_team") or row.get("home") or row.get("team_home")
    away = row.get("away_team") or row.get("away") or row.get("team_away")
    out.update(key_aliases_from_parts(raw_date, str(home or ""), str(away or "")))
    return {x for x in out if x and x.strip("|")}


def build_runtime_index() -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    index: dict[str, dict[str, Any]] = {}
    stats = {"context_rows": 0, "serving_rows": 0, "line_rows": 0, "consensus_rows": 0}

    for row in rows_from(EXPORT_DIR / "latest-context-observations.json"):
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        provider = norm(row.get("provider") or row.get("source") or row.get("context_source"))
        if not key or provider in IGNORED_CONTEXT or re.match(r"^context_(source|confirmation)_\d+$", provider):
            continue
        bucket = ensure_bucket(index, key)
        bucket["context"].add(provider)
        add_sample(bucket, "context_observations", {"provider": provider, "kind": row.get("kind")})
        stats["context_rows"] += 1

    for row in rows_from(EXPORT_DIR / "latest-match-serving.json"):
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        if not key:
            continue
        bucket = ensure_bucket(index, key)
        for src in list_any(row.get("context_sources")):
            srcn = norm(src)
            if srcn not in IGNORED_CONTEXT:
                bucket["context"].add(srcn)
        for src in list_any(row.get("line_sources")):
            srcn = norm(src)
            if srcn in LIVE_ODDS_SOURCES:
                bucket["line_sources"].add(srcn)
                bucket["odds_sources"].add(srcn)
        for idx in range(as_int(row.get("line_snapshot_count"))):
            bucket["price"].add(f"line_snapshot_{idx + 1}")
        stats["serving_rows"] += 1

    for path, stat_key in ((EXPORT_DIR / "latest-line-snapshots.json", "line_rows"), (EXPORT_DIR / "latest-consensus-lines.json", "consensus_rows")):
        for row in rows_from(path):
            key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
            if not key:
                continue
            bucket = ensure_bucket(index, key)
            providers = list_any(row.get("sources")) or [row.get("provider") or row.get("source")]
            for src in providers:
                srcn = norm(src)
                if srcn in LIVE_ODDS_SOURCES:
                    bucket["line_sources"].add(srcn)
                    bucket["odds_sources"].add(srcn)
            books = list_any(row.get("books")) or [row.get("bookmaker")]
            for book in books:
                bookn = norm(book)
                if bookn:
                    bucket["books"].add(bookn)
                    bucket["price"].add(f"book:{bookn}")
            stats[stat_key] += 1
    return index, stats


def expanded_runtime_index(raw_index: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    expanded: dict[str, dict[str, Any]] = {}
    alias_links = 0
    for raw_key, ev in raw_index.items():
        aliases = key_aliases_from_key(raw_key)
        aliases.add(str(raw_key))
        for alias in aliases:
            bucket = ensure_bucket(expanded, alias)
            before = sum(as_int(bucket.get(k)) for k in ("context", "line_sources", "odds_sources", "books", "price"))
            merge_bucket(bucket, ev)
            after = sum(as_int(bucket.get(k)) for k in ("context", "line_sources", "odds_sources", "books", "price"))
            alias_links += int(after > before and alias != raw_key)
    return expanded, alias_links


def evidence_for_row(runtime_index: dict[str, dict[str, Any]], row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    merged = {"context": set(), "line_sources": set(), "odds_sources": set(), "books": set(), "price": set(), "samples": []}
    matched: list[str] = []
    for alias in row_aliases(row):
        ev = runtime_index.get(alias)
        if ev:
            merge_bucket(merged, ev)
            matched.append(alias)
    return (merged, matched[:8]) if matched else (None, [])


def recompute_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"matches_total": len(rows), "matches_with_odds": 0, "matches_with_context": 0, "matches_ready_for_model": 0, "matches_ready_for_publish": 0, "matches_with_2plus_context_sources": 0, "matches_with_2plus_odds_sources": 0, "matches_with_2plus_price_confirmations": 0}
    for row in rows:
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        price_count = max(as_int(metadata.get("price_confirmation_sources_count")), as_int(row.get("price_confirmations")), as_int(row.get("books")), as_int(metadata.get("books_count")))
        odds_count = max(as_int(metadata.get("independent_odds_sources_count")), as_int(row.get("odds_sources")), as_int(row.get("line_sources")), as_int(metadata.get("odds_sources_count")))
        context_count = max(as_int(metadata.get("context_sources_count")), as_int(row.get("context_sources")), as_int(row.get("context_confirmations")))
        has_odds = bool(coverage.get("odds")) or price_count > 0 or odds_count > 0
        has_context = bool(coverage.get("context")) or context_count > 0
        counts["matches_with_odds"] += int(has_odds)
        counts["matches_with_context"] += int(has_context)
        counts["matches_with_2plus_context_sources"] += int(context_count >= 2)
        counts["matches_with_2plus_odds_sources"] += int(odds_count >= 2)
        counts["matches_with_2plus_price_confirmations"] += int(price_count >= 2)
        counts["matches_ready_for_model"] += int(has_odds and has_context)
        counts["matches_ready_for_publish"] += int(price_count >= 1 and odds_count >= 1 and context_count >= 1)
    return counts


def apply_evidence(row: dict[str, Any], ev: dict[str, Any], matched_aliases: list[str], now_iso: str) -> bool:
    before = json.dumps(row, ensure_ascii=False, sort_keys=True)
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    context = {norm(x) for x in list_any(row.get("context_sources")) + list_any(metadata.get("context_sources")) if norm(x)}
    context_conf = {norm(x) for x in list_any(row.get("context_confirmations")) + list_any(metadata.get("context_confirmations")) if norm(x)}
    line_sources = {norm(x) for x in list_any(row.get("line_sources")) + list_any(metadata.get("line_sources")) if norm(x) in LIVE_ODDS_SOURCES}
    odds_sources = {norm(x) for x in list_any(row.get("odds_sources")) + list_any(metadata.get("odds_sources")) if norm(x) in LIVE_ODDS_SOURCES}
    books = {norm(x) for x in list_any(row.get("books")) + list_any(metadata.get("books")) if norm(x)}
    price = set(list_any(row.get("price_confirmations")) + list_any(metadata.get("price_confirmations")))

    context.update(x for x in ev["context"] if x)
    context_conf.update(x for x in ev["context"] if x)
    line_sources.update(x for x in ev["line_sources"] if x in LIVE_ODDS_SOURCES)
    odds_sources.update(x for x in ev["odds_sources"] if x in LIVE_ODDS_SOURCES)
    books.update(x for x in ev["books"] if x)
    price.update(x for x in ev["price"] if x)
    while len(price) < len(books):
        price.add(f"book_confirmation_{len(price) + 1}")

    row["context_sources"] = sorted(context)
    row["context_confirmations"] = sorted(context_conf)
    row["line_sources"] = sorted(line_sources or odds_sources)
    row["odds_sources"] = sorted(odds_sources or line_sources)
    row["books"] = sorted(books)
    row["price_confirmations"] = sorted(price)
    metadata["context_sources_count"] = max(as_int(metadata.get("context_sources_count")), len(context), len(context_conf))
    metadata["confirmation_sources_count"] = max(as_int(metadata.get("confirmation_sources_count")), len(context_conf), len(context))
    metadata["independent_odds_sources_count"] = max(as_int(metadata.get("independent_odds_sources_count")), len(odds_sources | line_sources))
    metadata["odds_sources_count"] = metadata["independent_odds_sources_count"]
    metadata["books_count"] = max(as_int(metadata.get("books_count")), len(books))
    metadata["price_confirmation_sources_count"] = max(as_int(metadata.get("price_confirmation_sources_count")), len(price), len(books))
    metadata["runtime_context_bridge_updated_utc"] = now_iso
    metadata["runtime_context_bridge_matched_aliases"] = matched_aliases
    if ev.get("samples"):
        metadata["runtime_context_bridge_samples"] = ev["samples"][:8]
    row["metadata"] = metadata
    coverage["context"] = bool(context or context_conf or coverage.get("context"))
    coverage["odds"] = bool(price or odds_sources or line_sources or coverage.get("odds"))
    coverage["context_2plus_sources"] = max(len(context), len(context_conf), as_int(metadata.get("context_sources_count"))) >= 2
    coverage["ready_for_model"] = bool(coverage.get("odds") and coverage.get("context"))
    coverage["ready_for_publish"] = bool(coverage["ready_for_model"] and as_int(metadata.get("price_confirmation_sources_count")) >= 1 and as_int(metadata.get("context_sources_count")) >= 1)
    row["coverage"] = coverage
    return json.dumps(row, ensure_ascii=False, sort_keys=True) != before


def main() -> int:
    now_iso = datetime.now(UTC).isoformat()
    date = target_date()
    inv_path = DAY_DIR / f"{date}.json"
    inventory = load_json(inv_path, {})
    rows = inventory.get("matches") if isinstance(inventory, dict) else None
    if not isinstance(rows, list):
        report = {"status": "skipped", "reason": "inventory_missing", "inventory_path": str(inv_path), "updated_at_utc": now_iso}
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    raw_runtime, stats = build_runtime_index()
    runtime, alias_links = expanded_runtime_index(raw_runtime)
    updated = alias_matched_rows = exact_matched_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        ev, matched_aliases = evidence_for_row(runtime, row)
        if not ev:
            continue
        exact_matched_rows += int(key in matched_aliases)
        alias_matched_rows += int(key not in matched_aliases)
        updated += int(apply_evidence(row, ev, matched_aliases, now_iso))

    counts = dict(inventory.get("counts") or {})
    fresh_counts = recompute_counts([r for r in rows if isinstance(r, dict)])
    for key, value in fresh_counts.items():
        counts[key] = max(as_int(counts.get(key)), value) if key.startswith("matches_with_") or key.startswith("matches_ready_") else value
    inventory["counts"] = counts
    inventory["updated_at_utc"] = now_iso
    sources = inventory.setdefault("sources", {})
    if isinstance(sources, dict):
        sources["runtime_context_coverage_bridge"] = {"updated_at_utc": now_iso, "runtime_matches": len(raw_runtime), "runtime_alias_entries": len(runtime), "runtime_alias_links": alias_links, "rows_updated": updated, "exact_matched_rows": exact_matched_rows, "alias_matched_rows": alias_matched_rows, **stats}
    for alias in (inv_path, DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json"):
        write_json(alias, inventory)
    report = {"status": "ok", "date_local": date, "inventory_path": str(inv_path), "runtime_matches": len(raw_runtime), "runtime_alias_entries": len(runtime), "runtime_alias_links": alias_links, "rows_updated": updated, "exact_matched_rows": exact_matched_rows, "alias_matched_rows": alias_matched_rows, "stats": stats, "counts": counts, "updated_at_utc": now_iso}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
