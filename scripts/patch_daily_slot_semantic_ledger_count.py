from __future__ import annotations

"""Use actual semantic Telegram sends as daily-limit source of truth.

The publication ledger can contain old rows that were re-normalized during a sync
and received a fresh ``published_at`` timestamp.  Counting those rows made the
fallback cap report values such as 7/5 even though only two controlled fallback
picks were actually sent during the current Moscow day.  Daily cap checks should
therefore count real send timestamps first (sent_at / telegram_sent_at) and
dedupe by match-market-selection-line, not by fingerprint/prediction_id.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
BET_DIR = ROOT / ".data" / "bets"
EXPORT_DIR = ROOT / ".data" / "exports"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
        for key in ("rows", "bets", "items", "pending", "published", "selected", "selected_all", "telegram_picks", "sent_picks"):
            value = payload.get(key)
            if isinstance(value, dict):
                rows.append(value)
            elif isinstance(value, list):
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


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    try:
        if value in (None, ""):
            return ""
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return _norm(value)


def _selection(row: dict[str, Any]) -> str:
    explicit = _norm(row.get("selection_key"))
    if explicit in {"under", "over", "home", "away", "draw"}:
        return explicit
    text = _norm(row.get("selection"))
    if any(x in text for x in ("under", "menshe", "меньше", "тм", "tm")):
        return "under"
    if any(x in text for x in ("over", "bolshe", "больше", "тб", "tb")):
        return "over"
    return explicit or text


def _date_from_row(row: dict[str, Any]) -> str:
    for key in ("commence_time", "commence_time_utc", "kickoff_utc", "kickoff", "start_time"):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt.date().isoformat()
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", str(row.get(key) or ""))
        if m:
            return m.group(1)
    for key in ("canonical_publication_key", "canonical_match_id", "match_key", "ledger_semantic_key_raw"):
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", str(row.get(key) or ""))
        if m:
            return m.group(1)
    return ""


def _semantic_key(v18: Any, row: dict[str, Any]) -> str:
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    match_key = _norm(row.get("canonical_match_id") or row.get("match_key") or payload.get("match_key"))
    home = _norm(row.get("home_team") or row.get("home") or payload.get("home_team") or payload.get("home"))
    away = _norm(row.get("away_team") or row.get("away") or payload.get("away_team") or payload.get("away"))
    family = _norm(row.get("family") or row.get("market_family") or payload.get("family") or payload.get("market_family") or "totals")
    selection = _selection(row) or _selection(payload)
    point = _point(row.get("point") or row.get("line") or row.get("handicap") or payload.get("point") or payload.get("line"))
    day = _date_from_row(row)
    parts = [match_key or f"{day}|{home}|{away}", family, selection, point]
    key = "|".join(parts)
    if key.strip("|"):
        return key
    try:
        sig = v18._candidate_signature(row)  # type: ignore[attr-defined]
        return "|".join([sig.get("match_key") or f"{sig.get('home')}--{sig.get('away')}", sig.get("family"), sig.get("selection"), sig.get("point")])
    except Exception:
        return json.dumps(row, ensure_ascii=False, sort_keys=True)[:500]


def _actual_send_dt(row: dict[str, Any]) -> datetime | None:
    # Only true send stamps count for the daily cap.  A plain published_at_utc in
    # state/latest-bets can be created by ledger sync and is not an actual send.
    for key in ("sent_at", "telegram_sent_at_utc", "telegram_sent_at"):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt
    source = _norm(row.get("source") or row.get("ledger_source_file") or row.get("publication_lifecycle_status"))
    if any(token in source for token in ("telegram", "controlled fallback", "latest controlled fallback report", "fallback sent index")):
        dt = _parse_dt(row.get("published_at"))
        if dt is not None:
            return dt
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    for key in ("sent_at", "telegram_sent_at_utc", "telegram_sent_at"):
        dt = _parse_dt(payload.get(key))
        if dt is not None:
            return dt
    return None


def _is_published(v18: Any, row: dict[str, Any]) -> bool:
    # For daily cap, require an actual send timestamp.  This prevents old pending
    # ledger rows from being counted after a ledger-sync rewrite.
    if _actual_send_dt(row) is None:
        return False
    try:
        if v18._is_published_pick_row(row):  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    status = str(row.get("status") or row.get("publication_lifecycle_status") or "").strip().lower()
    if status in {"pending", "published", "telegram_sent", "sent", "won", "lost", "push", "void", "half_won", "half_lost"}:
        return True
    return bool(row.get("telegram_sent") or row.get("published") or row.get("sent_at") or row.get("telegram_sent_at_utc"))


def _ledger_rows() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in (
        ROOT / ".data" / "fallback-sent-index.json",
        ROOT / ".data" / "published-candidate-index.json",
        ROOT / ".data" / "state.json",
        BET_DIR / "published_bets.jsonl",
        BET_DIR / "pending_bets.json",
        EXPORT_DIR / "latest-picks.json",
        EXPORT_DIR / "latest-pending-bets.json",
        EXPORT_DIR / "latest-controlled-fallback-report.json",
    ):
        payload: Any = _load_jsonl(path) if path.suffix == ".jsonl" else _load_json(path, [])
        if isinstance(payload, dict) and path.name in {"fallback-sent-index.json", "published-candidate-index.json"}:
            iterable = [x for x in payload.values() if isinstance(x, dict)]
        else:
            iterable = _iter_rows(payload)
        for row in iterable:
            out.append((str(path), row))
    return out


def _semantic_ledger_count(v18: Any) -> dict[str, Any]:
    tz = v18._local_tz()  # type: ignore[attr-defined]
    today = datetime.now(UTC).astimezone(tz).date().isoformat()
    seen: set[str] = set()
    samples: list[dict[str, Any]] = []
    skipped_no_actual_send = 0
    source_rows = 0
    for source_path, row in _ledger_rows():
        source_rows += 1
        send_dt = _actual_send_dt(row)
        if send_dt is None:
            skipped_no_actual_send += 1
            continue
        if send_dt.astimezone(tz).date().isoformat() != today:
            continue
        if not _is_published(v18, row):
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
                "sent_at": send_dt.isoformat(),
                "commence_time": row.get("commence_time") or row.get("kickoff") or row.get("start_time") or row.get("commence_time_utc"),
            })
    return {
        "date": today,
        "count": len(seen),
        "samples": samples,
        "source": "actual_semantic_send_index",
        "source_rows": source_rows,
        "skipped_no_actual_send": skipped_no_actual_send,
    }


def install(v18: Any) -> None:
    original = getattr(v18, "_daily_existing_fallback_count", None)
    if not callable(original):
        return
    if getattr(v18, "_daily_slot_semantic_ledger_count_patch_installed", False):
        return

    def daily_existing_fallback_count_semantic() -> dict[str, Any]:
        semantic = _semantic_ledger_count(v18)
        try:
            legacy = original()
        except Exception:
            legacy = None
        if isinstance(legacy, dict):
            semantic["legacy_count"] = legacy.get("count")
            semantic["legacy_samples"] = legacy.get("samples", [])[:5]
        try:
            v18._GUARD_EVENTS.append({"guard": "controlled_fallback_daily_limit_actual_semantic_send_count", **semantic})
        except Exception:
            pass
        return semantic

    v18._daily_existing_fallback_count = daily_existing_fallback_count_semantic
    v18._daily_slot_semantic_ledger_count_patch_installed = True
