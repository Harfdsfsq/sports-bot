from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENT_LIFECYCLE_STATUSES = {"telegram_sent", "published", "sent"}
NOT_SENT_LIFECYCLE_STATUSES = {
    "generated",
    "generated_not_sent",
    "send_failed",
    "blocked",
    "dry_run_selected",
    "shadow_pending",
}
ACTIVE_BET_STATUSES = {"pending", "open", "published", "new", "active"}
SETTLED_BET_STATUSES = {"won", "lost", "push", "void", "cancelled", "canceled", "settled"}


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force", "sent", "ok"}


def _nested_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    for container_name in ("source_summary", "diagnostics", "publication_lifecycle"):
        container = row.get(container_name)
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    return container.get(key)
    return None


def is_sent_pick_row(row: Any) -> bool:
    """Return True only for candidates that were actually sent to Telegram.

    Generated/exported candidates are diagnostics. They must not block later sends and must not
    be counted as published in HARIZON reports.
    """
    if not isinstance(row, dict):
        return False

    lifecycle = str(
        _nested_value(row, "publication_lifecycle_status", "publication_lifecycle_stage") or ""
    ).strip().lower()
    if lifecycle in SENT_LIFECYCLE_STATUSES:
        return True
    if lifecycle in NOT_SENT_LIFECYCLE_STATUSES:
        return False

    if "telegram_sent" in row or isinstance(row.get("source_summary"), dict):
        telegram_sent = _nested_value(row, "telegram_sent")
        if telegram_sent is not None:
            return truthy(telegram_sent, False)

    status = str(row.get("status") or "").strip().lower()
    if status in SETTLED_BET_STATUSES:
        return False
    if status in ACTIVE_BET_STATUSES:
        return True

    return False


def _get(candidate: Any, field: str) -> Any:
    if isinstance(candidate, dict):
        if field in candidate:
            return candidate.get(field)
        summary = candidate.get("source_summary")
        if isinstance(summary, dict) and field in summary:
            return summary.get(field)
        diagnostics = candidate.get("diagnostics")
        if isinstance(diagnostics, dict) and field in diagnostics:
            return diagnostics.get(field)
        return None
    return getattr(candidate, field, None)


def _norm_text(value: Any) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = {
        "sd": "",
        "cd": "",
        "fc": "",
        "cf": "",
        "afc": "",
        "club": "",
    }
    parts = [aliases.get(part, part) for part in text.split()]
    text = " ".join(part for part in parts if part).strip()
    return text or str(value or "").casefold().strip()


def _norm_point(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g}"
    except Exception:
        return str(value).strip().casefold()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_date(candidate: Any) -> str:
    for field in ("commence_time", "commence_time_utc", "start_time", "event_time"):
        dt = _parse_dt(_get(candidate, field))
        if dt is not None:
            return dt.date().isoformat()
    match_key = str(_get(candidate, "match_key") or "")
    match = re.search(r"20\d{2}-\d{2}-\d{2}", match_key)
    return match.group(0) if match else ""


def _selection_key(candidate: Any) -> str:
    raw = _get(candidate, "selection_key") or _get(candidate, "selection") or ""
    text = str(raw).casefold().strip()
    if text in {"больше", "over", "o", "tb", "тб"}:
        return "over"
    if text in {"меньше", "under", "u", "tm", "тм"}:
        return "under"
    return _norm_text(text)


def _canonical_match_key(candidate: Any) -> str:
    match_key = str(_get(candidate, "match_key") or "").strip().casefold()
    if match_key:
        # Keep the provider-independent normalized match key when available.
        return re.sub(r"\s+", "", match_key)
    home = _norm_text(_get(candidate, "home_team") or _get(candidate, "home"))
    away = _norm_text(_get(candidate, "away_team") or _get(candidate, "away"))
    date = _event_date(candidate)
    sport = str(_get(candidate, "sport_key") or "soccer").casefold().strip()
    return f"{sport}|{home}|{away}|{date}"


def candidate_dedupe_keys(candidate: Any) -> set[str]:
    """Return all publication identity keys for a pick/candidate.

    The bot used to rely on one exact fingerprint. That is too brittle because different exporters
    write different time fields (commence_time vs commence_time_utc), translated selection labels,
    or older fingerprint formats.  A Telegram publication should be deduped by exact fingerprint
    AND by semantic identity: match + market family + side/selection + point until kickoff.
    """
    keys: set[str] = set()
    for field in ("fingerprint", "prediction_id", "id"):
        raw = _get(candidate, field)
        if raw:
            keys.add("exact:" + str(raw).strip().casefold())

    match_key = _canonical_match_key(candidate)
    family = _norm_text(_get(candidate, "family"))
    selection = _selection_key(candidate)
    point = _norm_point(_get(candidate, "point"))
    team_side = _norm_text(_get(candidate, "team_side"))
    date = _event_date(candidate)
    if match_key and family and selection:
        keys.add(f"semantic:{match_key}|{family}|{selection}|{team_side}|{point}")
        # Commence-time-free key is the important one for exports that omit/alter kickoff time.
        keys.add(f"match_market:{match_key}|{family}|{selection}|{team_side}|{point}")
        if date:
            home = _norm_text(_get(candidate, "home_team") or _get(candidate, "home"))
            away = _norm_text(_get(candidate, "away_team") or _get(candidate, "away"))
            if home and away:
                keys.add(f"teams_market:{home}|{away}|{date}|{family}|{selection}|{team_side}|{point}")
    return {key for key in keys if key and not key.endswith("||||")}


def collect_sent_candidate_keys(value: Any, *, require_sent: bool = True) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        if not require_sent or is_sent_pick_row(value):
            keys.update(candidate_dedupe_keys(value))
        for child in value.values():
            keys.update(collect_sent_candidate_keys(child, require_sent=require_sent))
    elif isinstance(value, list):
        for child in value:
            keys.update(collect_sent_candidate_keys(child, require_sent=require_sent))
    return keys


def load_sent_candidate_keys(paths: list[str | Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        p = Path(path)
        if not p.exists() or not p.is_file() or p.stat().st_size <= 0:
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        keys.update(collect_sent_candidate_keys(payload, require_sent=True))
    return keys


def append_sent_candidate_index(path: str | Path, candidates: list[Any]) -> None:
    """Persist an append-only sent index immediately after Telegram confirms a send."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.stat().st_size > 0 else {}
    except Exception:
        payload = {}
    rows = payload.get("sent") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    existing_keys = collect_sent_candidate_keys(rows, require_sent=True)
    now = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        keys = sorted(candidate_dedupe_keys(candidate))
        if not keys or any(key in existing_keys for key in keys):
            continue
        rows.append({
            "recorded_at": now,
            "telegram_sent": True,
            "publication_lifecycle_status": "telegram_sent",
            "fingerprint": _get(candidate, "fingerprint") or _get(candidate, "prediction_id"),
            "match_key": _get(candidate, "match_key"),
            "sport_key": _get(candidate, "sport_key"),
            "home_team": _get(candidate, "home_team"),
            "away_team": _get(candidate, "away_team"),
            "commence_time": str(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc") or ""),
            "family": _get(candidate, "family"),
            "selection_key": _selection_key(candidate),
            "selection": _get(candidate, "selection"),
            "point": _get(candidate, "point"),
            "team_side": _get(candidate, "team_side"),
            "dedupe_keys": keys,
        })
        existing_keys.update(keys)
    payload = {"updated_at": now, "sent": rows[-500:]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mark_candidate_lifecycle(candidate: Any, *, telegram_sent: bool, failure_reason: str | None = None) -> None:
    status = "telegram_sent" if telegram_sent else "generated_not_sent"
    if failure_reason and not telegram_sent:
        status = "send_failed"
    summary = getattr(candidate, "source_summary", None)
    if not isinstance(summary, dict):
        if isinstance(candidate, dict):
            summary = dict(candidate.get("source_summary") or {})
        else:
            summary = {}
    summary["telegram_sent"] = bool(telegram_sent)
    summary["publication_lifecycle_status"] = status
    summary["publication_lifecycle_stage"] = status
    if failure_reason:
        summary["publication_failure_reason"] = str(failure_reason)
    if isinstance(candidate, dict):
        candidate["source_summary"] = summary
        candidate["telegram_sent"] = bool(telegram_sent)
        candidate["publication_lifecycle_status"] = status
        candidate["publication_lifecycle_stage"] = status
        candidate["status"] = "pending" if telegram_sent else "generated"
    else:
        try:
            candidate.source_summary = summary
        except Exception:
            pass
        diagnostics = getattr(candidate, "diagnostics", None)
        if isinstance(diagnostics, dict):
            diagnostics["publication_lifecycle"] = {
                "telegram_sent": bool(telegram_sent),
                "status": status,
                "failure_reason": failure_reason,
            }
