from __future__ import annotations

"""Use semantic publication ledger as daily-limit source of truth.

Older fallback state can overcount after a multi-pick top bundle because
fallback-sent-index stores every sent row while the durable ledger deduplicates by
match/market/selection/point/kickoff.  If the semantic ledger exists, daily limit
checks should use it first; otherwise the fallback keeps the original legacy
counter as a backup.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
BET_DIR = ROOT / ".data" / "bets"
EXPORT_DIR = ROOT / ".data" / "exports"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows: list[dict[str, Any]] = []
        for key in ("rows", "bets", "items", "pending", "published", "selected_all"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([x for x in value if isinstance(x, dict)])
        return rows
    return []


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


def _row_local_day(row: dict[str, Any], tz: Any) -> str | None:
    # Prefer publication time.  Ledger rows mostly use *_utc keys, which the
    # legacy counter did not read, causing fallback-sent-index overcounts to win.
    for key in (
        "published_at_utc", "published_at", "sent_at", "telegram_sent_at_utc", "telegram_sent_at",
        "created_at_utc", "created_at", "updated_at_utc",
        "commence_time", "kickoff_utc", "kickoff", "start_time",
    ):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    for key in ("published_at_utc", "published_at", "sent_at", "created_at_utc", "created_at", "commence_time", "kickoff"):
        dt = _parse_dt(payload.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    return None


def _semantic_key(v18: Any, row: dict[str, Any]) -> str:
    for key in ("ledger_semantic_key", "canonical_publication_key", "dedupe_key", "fingerprint", "prediction_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    try:
        sig = v18._candidate_signature(row)  # type: ignore[attr-defined]
        key = "|".join([
            sig.get("match_key") or f"{sig.get('home')}--{sig.get('away')}",
            sig.get("family"),
            sig.get("selection"),
            sig.get("point"),
        ])
        if key.strip("|"):
            return key
    except Exception:
        pass
    return json.dumps(row, ensure_ascii=False, sort_keys=True)[:500]


def _is_published(v18: Any, row: dict[str, Any]) -> bool:
    try:
        if v18._is_published_pick_row(row):  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    status = str(row.get("status") or row.get("publication_lifecycle_status") or "").strip().lower()
    if status in {"pending", "published", "telegram_sent", "sent", "won", "lost", "push", "void", "half_won", "half_lost"}:
        return True
    return bool(row.get("telegram_sent") or row.get("published") or row.get("published_at_utc") or row.get("published_at") or row.get("sent_at"))


def _ledger_rows() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in (
        BET_DIR / "published_bets.jsonl",
        BET_DIR / "pending_bets.json",
        EXPORT_DIR / "latest-picks.json",
        EXPORT_DIR / "latest-pending-bets.json",
        EXPORT_DIR / "latest-controlled-fallback-report.json",
    ):
        payload: Any
        if path.suffix == ".jsonl":
            payload = _load_jsonl(path)
        else:
            payload = _load_json(path, [])
        for row in _iter_rows(payload):
            out.append((str(path), row))
    return out


def _semantic_ledger_count(v18: Any) -> dict[str, Any]:
    tz = v18._local_tz()  # type: ignore[attr-defined]
    today = datetime.now(UTC).astimezone(tz).date().isoformat()
    seen: set[str] = set()
    samples: list[dict[str, Any]] = []
    source_rows = 0
    for source_path, row in _ledger_rows():
        source_rows += 1
        if not _is_published(v18, row):
            continue
        if _row_local_day(row, tz) != today:
            continue
        key = _semantic_key(v18, row)
        if key in seen:
            continue
        seen.add(key)
        if len(samples) < 10:
            samples.append({
                "source_path": source_path,
                "key": key,
                "home_team": row.get("home_team") or row.get("home"),
                "away_team": row.get("away_team") or row.get("away"),
                "selection": row.get("selection"),
                "point": row.get("point"),
                "published_at": row.get("published_at_utc") or row.get("published_at") or row.get("sent_at") or row.get("created_at_utc") or row.get("created_at"),
                "commence_time": row.get("commence_time") or row.get("kickoff") or row.get("start_time"),
            })
    return {
        "date": today,
        "count": len(seen),
        "samples": samples,
        "source": "semantic_publication_ledger",
        "source_rows": source_rows,
    }


def install(v18: Any) -> None:
    original = getattr(v18, "_daily_existing_fallback_count", None)
    if not callable(original):
        return
    if getattr(v18, "_daily_slot_semantic_ledger_count_patch_installed", False):
        return

    def daily_existing_fallback_count_semantic() -> dict[str, Any]:
        semantic = _semantic_ledger_count(v18)
        # If the ledger has rows for today, trust the semantic ledger over legacy
        # fallback-sent-index/state counts.  If it is empty, fall back to legacy.
        if semantic.get("count", 0) > 0 or semantic.get("source_rows", 0) > 0:
            try:
                legacy = original()
            except Exception:
                legacy = None
            if isinstance(legacy, dict):
                semantic["legacy_count"] = legacy.get("count")
                semantic["legacy_samples"] = legacy.get("samples", [])[:5]
            try:
                v18._GUARD_EVENTS.append({"guard": "controlled_fallback_daily_limit_semantic_ledger_count", **semantic})
            except Exception:
                pass
            return semantic
        return original()

    v18._daily_existing_fallback_count = daily_existing_fallback_count_semantic
    v18._daily_slot_semantic_ledger_count_patch_installed = True
