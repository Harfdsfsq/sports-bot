from __future__ import annotations

"""Create durable HARIZON publication/run ledgers from runtime artifacts.

This version uses a semantic bet key instead of raw runtime dedupe keys.  A
single Telegram pick can appear in multiple runtime artifacts with different
technical dedupe_key values; using those raw keys double-counts stake, pending
risk, settlement rows and daily ROI.  The durable ledger key is now:

    match_key/canonical teams + kickoff + family + selection + point

Runtime keys are preserved as source_dedupe_key for diagnostics only.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
BET_DIR = Path(".data/bets")
PUBLISHED_JSONL = BET_DIR / "published_bets.jsonl"
PENDING_JSON = BET_DIR / "pending_bets.json"
SETTLED_JSONL = BET_DIR / "settled_bets.jsonl"
RUN_LEDGER_JSONL = BET_DIR / "run_report_ledger.jsonl"
REPORT = EXPORT_DIR / "latest-publication-ledger-sync.json"

SETTLED_STATUSES = {"won", "lost", "push", "void", "half_won", "half_lost", "settled", "closed"}
PENDING_STATUSES = {"", "pending", "generated", "published", "telegram_sent", "sent", "posted", "open", "active"}


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


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


def norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е").replace("—", "-").replace("–", "-")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def point_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", "."))
        return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return norm(value)


def nested(row: dict[str, Any], name: str) -> dict[str, Any]:
    value = row.get(name)
    return value if isinstance(value, dict) else {}


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def normalized_kickoff(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    value = first_nonempty(
        row.get("commence_time"), row.get("kickoff"), row.get("kickoff_utc"), row.get("start_time"),
        payload.get("commence_time"), payload.get("kickoff"), payload.get("start_time"),
    )
    dt = parse_dt(value)
    if dt:
        # Minute precision is enough and prevents duplicate rows created a few
        # milliseconds apart from splitting the same bet.
        return dt.replace(second=0, microsecond=0).isoformat()
    return str(value or "")[:16]


def semantic_key_raw(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    match_key = first_nonempty(row.get("match_key"), row.get("canonical_match_id"), payload.get("match_key"))
    home = first_nonempty(row.get("home_team"), row.get("home"), payload.get("home_team"), payload.get("home"))
    away = first_nonempty(row.get("away_team"), row.get("away"), payload.get("away_team"), payload.get("away"))
    family = first_nonempty(row.get("family"), row.get("market_family"), payload.get("family"), payload.get("market_family"))
    selection = first_nonempty(row.get("selection"), row.get("selection_key"), payload.get("selection"), payload.get("selection_key"))
    point = first_nonempty(row.get("point"), row.get("line"), row.get("handicap"), payload.get("point"), payload.get("line"), payload.get("handicap"))
    # If match_key exists, it already includes teams/date, so keep it as the
    # strongest identity.  Home/away are still appended for old rows where
    # match_key is absent or too generic.
    return "|".join([
        norm(match_key or ""),
        norm(home or ""),
        norm(away or ""),
        normalized_kickoff(row),
        norm(family or ""),
        norm(selection or ""),
        point_text(point),
    ])


def row_key(row: dict[str, Any]) -> str:
    raw = semantic_key_raw(row)
    if raw.strip("|"):
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
    # Last resort only.  Do not prefer runtime dedupe_key because it changes
    # across artifacts for the same Telegram pick.
    fallback = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(fallback.encode("utf-8")).hexdigest()


def is_sent(row: dict[str, Any]) -> bool:
    summary = nested(row, "source_summary")
    for key in ("telegram_sent", "published", "sent", "is_published"):
        for obj in (row, summary):
            if str(obj.get(key) or "").strip().lower() in {"1", "true", "yes", "on", "sent", "published"}:
                return True
    status = str(row.get("publication_lifecycle_status") or row.get("status") or "").strip().lower()
    return status in {"telegram_sent", "published", "sent", "posted", "pending"} and bool(row.get("published_at_utc") or row.get("sent_at"))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def collect_publication_rows() -> list[dict[str, Any]]:
    paths = [
        EXPORT_DIR / "latest-controlled-fallback-published-picks.json",
        EXPORT_DIR / "published-picks-ledger.json",
        EXPORT_DIR / "controlled-fallback-published-ledger.json",
        EXPORT_DIR / "published-bets-ledger.json",
        EXPORT_DIR / "latest-picks.json",
        EXPORT_DIR / "latest-bets.json",
        EXPORT_DIR / "latest-pending-bets.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = load_json(path, [])
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("picks") or payload.get("bets") or payload.get("pending") or []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and is_sent(item):
                    row = dict(item)
                    row.setdefault("ledger_source_file", str(path))
                    rows.append(row)

    fallback = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    if isinstance(fallback, dict) and fallback.get("published"):
        for key in ("published_candidates", "selected", "selected_rows", "published_rows", "evaluated"):
            value = fallback.get(key)
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                if item.get("ok") is False and key == "evaluated":
                    continue
                row = dict(item)
                row["telegram_sent"] = True
                row["published"] = True
                row.setdefault("published_at_utc", fallback.get("created_at") or fallback.get("created_at_utc") or datetime.now(UTC).isoformat())
                row.setdefault("source", "controlled_fallback")
                row.setdefault("ledger_source_file", "latest-controlled-fallback-report.json")
                rows.append(row)
    return rows


def normalize_bet(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    metrics = out.get("metrics") if isinstance(out.get("metrics"), dict) else {}
    payload = out.get("bet_payload") if isinstance(out.get("bet_payload"), dict) else {}
    now = datetime.now(UTC).isoformat()

    source_dedupe = out.get("dedupe_key")
    semantic = row_key(out)
    if source_dedupe and source_dedupe != semantic:
        out.setdefault("source_dedupe_key", source_dedupe)
    out["dedupe_key"] = semantic
    out["ledger_semantic_key"] = semantic
    out["ledger_semantic_key_raw"] = semantic_key_raw(out)

    out.setdefault("source", "controlled_fallback")
    out.setdefault("published_by", out.get("source") or "controlled_fallback")
    out.setdefault("telegram_sent", True)
    out.setdefault("published", True)
    out.setdefault("publication_lifecycle_status", "telegram_sent")
    out.setdefault("publication_lifecycle_stage", "telegram_sent")
    out.setdefault("status", "pending")
    out.setdefault("published_at_utc", out.get("sent_at") or out.get("created_at_utc") or now)

    stake = first_nonempty(out.get("stake"), out.get("stake_amount"), payload.get("stake"), payload.get("stake_amount"))
    stake_num = as_float(stake, 0.0)
    if stake_num > 0:
        out["stake"] = stake_num
        out["stake_amount"] = stake_num
    else:
        out.setdefault("stake", 0)
        out.setdefault("stake_amount", 0)

    odds = first_nonempty(out.get("odds"), out.get("selected_odds"), out.get("price"), payload.get("odds"), metrics.get("odds"))
    if odds is not None:
        out["odds"] = as_float(odds, 0.0) or odds
        out.setdefault("selected_odds", out["odds"])
    out.setdefault("ev_pct", out.get("ev_pct") if out.get("ev_pct") is not None else metrics.get("canonical_ev_pct"))
    out.setdefault("edge_pp", out.get("edge_pp") if out.get("edge_pp") is not None else metrics.get("canonical_edge_pp"))
    out.setdefault("quality_score", out.get("quality_score") if out.get("quality_score") is not None else metrics.get("quality_score"))
    out.setdefault("books_count", out.get("books_count") if out.get("books_count") is not None else metrics.get("books_count"))
    out.setdefault("confirmation_sources", out.get("confirmation_sources") or metrics.get("confirmation_sources"))
    out.setdefault("confirmation_sources_count", out.get("confirmation_sources_count") or metrics.get("confirmation_sources_count"))
    return out


def row_score(row: dict[str, Any]) -> tuple[int, int, int, int, float, int]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    stake = max(as_float(row.get("stake")), as_float(row.get("stake_amount")), as_float(nested(row, "bet_payload").get("stake")), as_float(nested(row, "bet_payload").get("stake_amount")))
    has_payload = 1 if isinstance(row.get("bet_payload"), dict) and row.get("bet_payload") else 0
    source_count = len(row.get("confirmation_sources") or metrics.get("confirmation_sources") or []) if isinstance(row.get("confirmation_sources") or metrics.get("confirmation_sources"), list) else 0
    metric_size = len(json.dumps(metrics, ensure_ascii=False, sort_keys=True)) if metrics else 0
    published = 1 if is_sent(row) else 0
    created = parse_dt(row.get("published_at_utc") or row.get("sent_at") or row.get("created_at_utc"))
    created_ts = int(created.timestamp()) if created else 0
    return (published, 1 if stake > 0 else 0, has_payload, source_count, stake, metric_size + created_ts // 100000)


def merge_rows(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    # Prefer the richer row as base, then fill only missing fields from the other.
    base, extra = (new, old) if row_score(new) >= row_score(old) else (old, new)
    out = dict(base)
    for key, value in extra.items():
        if out.get(key) in (None, "", [], {}, 0) and value not in (None, "", [], {}):
            out[key] = value
    # Preserve all runtime dedupe keys for diagnostics.
    source_keys = []
    for row in (old, new):
        for key in (row.get("source_dedupe_key"), row.get("dedupe_key")):
            if key and key not in source_keys:
                source_keys.append(str(key))
    if source_keys:
        out["source_dedupe_keys"] = source_keys
    # Stake must never be overwritten by a duplicate technical row with 0 stake.
    stake = max(as_float(old.get("stake")), as_float(old.get("stake_amount")), as_float(new.get("stake")), as_float(new.get("stake_amount")))
    if stake > 0:
        out["stake"] = stake
        out["stake_amount"] = stake
    out["dedupe_key"] = row_key(out)
    out["ledger_semantic_key"] = out["dedupe_key"]
    out["ledger_semantic_key_raw"] = semantic_key_raw(out)
    return out


def merge_by_key(existing: list[dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    input_rows = 0
    for row in existing + rows:
        if not isinstance(row, dict):
            continue
        input_rows += 1
        normalized = normalize_bet(row)
        key = row_key(normalized)
        if key in out:
            duplicate_rows += 1
            out[key] = merge_rows(out[key], normalized)
        else:
            out[key] = normalized
    merged = sorted(out.values(), key=lambda item: str(item.get("published_at_utc") or item.get("sent_at") or item.get("created_at_utc") or ""))
    return merged, {"input_rows": input_rows, "unique_rows": len(merged), "duplicates_removed": duplicate_rows}


def is_pending(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "pending").strip().lower()
    settlement = row.get("settlement") if isinstance(row.get("settlement"), dict) else {}
    result = str(settlement.get("result") or row.get("result") or row.get("outcome") or "").strip().lower()
    return status in PENDING_STATUSES and status not in SETTLED_STATUSES and result not in SETTLED_STATUSES


def sync_bets() -> dict[str, Any]:
    existing = iter_jsonl(PUBLISHED_JSONL)
    collected = collect_publication_rows()
    rows = [normalize_bet(row) for row in collected]
    merged, stats = merge_by_key(existing, rows)
    if rows or existing:
        write_jsonl(PUBLISHED_JSONL, merged)
        pending, pending_stats = merge_by_key([], [row for row in merged if is_pending(row)])
        write_json(PENDING_JSON, pending)
        # Keep legacy exports non-empty and deduped for tools that still read snapshots.
        write_json(EXPORT_DIR / "latest-pending-bets.json", pending)
        write_json(EXPORT_DIR / "latest-picks.json", merged)
    else:
        pending_stats = {"input_rows": 0, "unique_rows": 0, "duplicates_removed": 0}
    if not SETTLED_JSONL.exists():
        write_jsonl(SETTLED_JSONL, [])
    return {
        "new_rows_seen": len(rows),
        "published_ledger_rows": len(merged),
        "unique_published_bets": len(merged),
        "duplicates_removed": stats.get("duplicates_removed", 0),
        "published_input_rows": stats.get("input_rows", 0),
        "pending_unique_rows": pending_stats.get("unique_rows", 0),
        "pending_duplicates_removed": pending_stats.get("duplicates_removed", 0),
        "dedupe_policy": "semantic_match_market_selection_point_kickoff",
    }


def sync_run_ledger() -> dict[str, Any]:
    report = load_json(EXPORT_DIR / "latest-harizon-telegram-run-report.json", {})
    fallback = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    debug = load_json(".logs/debug-last-run.json", {})
    day_summary = load_json(EXPORT_DIR / "latest-day-inventory-summary.json", {})
    if not any(isinstance(x, dict) and x for x in (report, fallback, debug, day_summary)):
        return {"run_appended": False, "reason": "no_runtime_artifacts"}
    summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else {}
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    funnel = report.get("funnel") if isinstance(report.get("funnel"), dict) else {}
    counts = day_summary.get("counts") if isinstance(day_summary.get("counts"), dict) else {}
    created = report.get("created_at_utc") or fallback.get("created_at") or summary.get("current_time_utc") or datetime.now(UTC).isoformat()
    row = {
        "created_at_utc": created,
        "source": "run-bot",
        "github_run_id": report.get("github_run_id") or report.get("run_id"),
        "summary": {
            "matches_seen": coverage.get("matches_seen") or counts.get("matches_seen_latest_run") or summary.get("matches_seen"),
            "matches_with_offers": coverage.get("matches_with_offers") or counts.get("runtime_matches_with_odds_last_run") or summary.get("matches_with_offers"),
            "contexts_built": coverage.get("matches_with_context") or counts.get("runtime_matches_with_context_last_run") or summary.get("contexts_built"),
            "candidates_raw": funnel.get("raw_candidates") or summary.get("candidates_raw"),
            "candidates_before_quality": funnel.get("candidates_before_quality") or summary.get("candidates_before_quality"),
            "candidates_publishable": funnel.get("publishable_candidates") or summary.get("candidates_publishable"),
            "published": funnel.get("published_count") or (1 if fallback.get("published") else 0),
            "published_to_telegram": funnel.get("published_count") or (1 if fallback.get("published") else 0),
            "telegram_messages_sent": 1 if fallback.get("published") else 0,
            "status": report.get("status") or summary.get("status") or "ok",
        },
        "fallback_status": fallback.get("status"),
        "fallback_published": bool(fallback.get("published")),
    }
    existing = iter_jsonl(RUN_LEDGER_JSONL)
    # Prefer a stable per-run key if available; otherwise retain old content hash fallback.
    stable_raw = str(row.get("github_run_id") or "") + "|" + str(row.get("created_at_utc") or "")[:16]
    key = hashlib.sha1((stable_raw if stable_raw.strip("|") else json.dumps(row, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
    rows_by_key = {hashlib.sha1((str(item.get("github_run_id") or "") + "|" + str(item.get("created_at_utc") or "")[:16] if item.get("github_run_id") else json.dumps(item, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest(): item for item in existing}
    rows_by_key[key] = row
    write_jsonl(RUN_LEDGER_JSONL, list(rows_by_key.values()))
    return {"run_appended": True, "run_ledger_rows": len(rows_by_key)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="default")
    args = parser.parse_args()
    BET_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "phase": args.phase,
        "bets": sync_bets(),
        "runs": sync_run_ledger(),
    }
    write_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
