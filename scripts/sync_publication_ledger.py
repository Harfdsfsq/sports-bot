from __future__ import annotations

"""Create durable HARIZON publication/run ledgers from runtime artifacts.

latest-picks/latest-pending-bets are snapshots and can be empty after a later
no-pick run.  The bot needs append-only ledgers so daily reports, settlement and
auto-learning can see what was actually sent to Telegram.
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


def row_key(row: dict[str, Any]) -> str:
    explicit = row.get("dedupe_key") or row.get("prediction_id") or row.get("fingerprint")
    if explicit:
        return str(explicit)
    raw = "|".join(
        [
            norm(row.get("match_key") or row.get("canonical_match_id") or ""),
            norm(row.get("home_team") or row.get("home") or ""),
            norm(row.get("away_team") or row.get("away") or ""),
            str(row.get("commence_time") or row.get("kickoff") or row.get("start_time") or "")[:16],
            norm(row.get("family") or row.get("market_family") or ""),
            norm(row.get("selection") or row.get("selection_key") or ""),
            point_text(row.get("point") or row.get("line") or row.get("handicap")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_sent(row: dict[str, Any]) -> bool:
    for key in ("telegram_sent", "published", "sent", "is_published"):
        if str(row.get(key) or "").strip().lower() in {"1", "true", "yes", "on", "sent", "published"}:
            return True
    status = str(row.get("publication_lifecycle_status") or row.get("status") or "").strip().lower()
    return status in {"telegram_sent", "published", "sent", "posted", "pending"} and bool(row.get("published_at_utc") or row.get("sent_at"))


def collect_publication_rows() -> list[dict[str, Any]]:
    paths = [
        EXPORT_DIR / "latest-controlled-fallback-published-picks.json",
        EXPORT_DIR / "published-picks-ledger.json",
        EXPORT_DIR / "controlled-fallback-published-ledger.json",
        EXPORT_DIR / "published-bets-ledger.json",
        EXPORT_DIR / "latest-picks.json",
        EXPORT_DIR / "latest-bets.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = load_json(path, [])
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("picks") or payload.get("bets") or []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and is_sent(item):
                    rows.append(dict(item))

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
                rows.append(row)
    return rows


def normalize_bet(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    metrics = out.get("metrics") if isinstance(out.get("metrics"), dict) else {}
    now = datetime.now(UTC).isoformat()
    out.setdefault("dedupe_key", row_key(out))
    out.setdefault("source", "controlled_fallback")
    out.setdefault("published_by", out.get("source") or "controlled_fallback")
    out.setdefault("telegram_sent", True)
    out.setdefault("published", True)
    out.setdefault("publication_lifecycle_status", "telegram_sent")
    out.setdefault("status", "pending")
    out.setdefault("published_at_utc", out.get("sent_at") or out.get("created_at_utc") or now)
    out.setdefault("stake_amount", out.get("stake") or out.get("stake_amount") or 0)
    out.setdefault("odds", out.get("selected_odds") or out.get("price") or metrics.get("odds"))
    out.setdefault("selected_odds", out.get("odds"))
    out.setdefault("ev_pct", out.get("ev_pct") if out.get("ev_pct") is not None else metrics.get("canonical_ev_pct"))
    out.setdefault("edge_pp", out.get("edge_pp") if out.get("edge_pp") is not None else metrics.get("canonical_edge_pp"))
    out.setdefault("quality_score", out.get("quality_score") if out.get("quality_score") is not None else metrics.get("quality_score"))
    out.setdefault("books_count", out.get("books_count") if out.get("books_count") is not None else metrics.get("books_count"))
    out.setdefault("confirmation_sources", out.get("confirmation_sources") or metrics.get("confirmation_sources"))
    out.setdefault("confirmation_sources_count", out.get("confirmation_sources_count") or metrics.get("confirmation_sources_count"))
    return out


def merge_by_key(existing: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in existing + rows:
        if not isinstance(row, dict):
            continue
        key = row_key(row)
        merged = dict(out.get(key, {}))
        merged.update(row)
        merged.setdefault("dedupe_key", key)
        out[key] = merged
    return list(out.values())


def sync_bets() -> dict[str, Any]:
    existing = iter_jsonl(PUBLISHED_JSONL)
    rows = [normalize_bet(row) for row in collect_publication_rows()]
    merged = merge_by_key(existing, rows)
    if rows or existing:
        write_jsonl(PUBLISHED_JSONL, merged)
        pending = [row for row in merged if str(row.get("status") or "pending").lower() in {"pending", "generated", "published"}]
        write_json(PENDING_JSON, pending)
        # Keep legacy exports non-empty for tools that still read snapshots.
        write_json(EXPORT_DIR / "latest-pending-bets.json", pending)
        write_json(EXPORT_DIR / "latest-picks.json", merged)
    if not SETTLED_JSONL.exists():
        write_jsonl(SETTLED_JSONL, [])
    return {"new_rows_seen": len(rows), "published_ledger_rows": len(merged)}


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
    key = hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    rows_by_key = {hashlib.sha1(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(): item for item in existing}
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
