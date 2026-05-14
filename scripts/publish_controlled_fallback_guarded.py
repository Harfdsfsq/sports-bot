from __future__ import annotations

"""Guarded entrypoint for controlled fallback publication.

The normal model pipeline already has a windowed coverage/movement publication
filter. Live runs showed that the controlled fallback script could still publish
the same candidate after the main publish filter rejected it with
`needs_next_cron_line_movement_recheck`. It also wrote the fallback sent-index to
.data/fallback-sent-index.json, but that file was not committed, so the same
match/market could be sent again in the next run.

This wrapper keeps the original controlled-fallback evaluator but adds two hard
prepublish guards:
1. respect latest-windowed-core-publication-filter movement blocks;
2. dedupe against previous controlled-fallback reports and sent-index.
"""

import importlib.util
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = Path(__file__).resolve().with_name("publish_controlled_fallback.py")
REPORT_PATH = ROOT / ".data" / "exports" / "latest-controlled-fallback-prepublish-guard.json"

_GUARD_EVENTS: list[dict[str, Any]] = []


def _load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_publish_controlled_fallback_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base_module()


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: str | Path, payload: Any) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    if value in (None, "", "null"):
        return ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return _norm(value)


def _parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _candidate_signature(row: dict[str, Any]) -> dict[str, str]:
    return {
        "match_key": _norm(row.get("match_key")),
        "family": _norm(row.get("family") or row.get("market_family")),
        "selection": _norm(row.get("selection")),
        "point": _point(row.get("point")),
        "home": _norm(row.get("home_team")),
        "away": _norm(row.get("away_team")),
    }


def _same_candidate(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    cand = _candidate_signature(candidate)
    other = _candidate_signature(row)
    if cand["match_key"] and other["match_key"] and cand["match_key"] != other["match_key"]:
        return False
    if cand["family"] and other["family"] and cand["family"] != other["family"]:
        return False
    if cand["selection"] and other["selection"] and cand["selection"] != other["selection"]:
        return False
    if cand["point"] and other["point"] and cand["point"] != other["point"]:
        return False
    if not cand["match_key"] or not other["match_key"]:
        if cand["home"] and other["home"] and cand["home"] != other["home"]:
            return False
        if cand["away"] and other["away"] and cand["away"] != other["away"]:
            return False
    return True


def _row_from_windowed_block(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    if "family" not in row and isinstance(coverage, dict):
        row["family"] = coverage.get("family")
    return row


def _windowed_movement_reasons(candidate: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_RESPECT_WINDOWED_MOVEMENT_GUARD"), True):
        return []
    payload = _load_json(ROOT / ".data" / "exports" / "latest-windowed-core-publication-filter.json", {})
    blocked = payload.get("blocked_sample") if isinstance(payload, dict) else []
    if not isinstance(blocked, list):
        return []
    for item in blocked:
        if not isinstance(item, dict):
            continue
        row = _row_from_windowed_block(item)
        if not _same_candidate(candidate, row):
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        reject_reasons = list(coverage.get("reject_reasons") or item.get("reject_reasons") or [])
        movement = coverage.get("movement") if isinstance(coverage.get("movement"), dict) else {}
        out: list[str] = []
        if "needs_next_cron_line_movement_recheck" in reject_reasons or movement.get("reason") == "needs_next_cron_line_movement_recheck":
            out.append("controlled_fallback_windowed_line_movement_recheck_required")
        elif reject_reasons and _truthy(os.getenv("CONTROLLED_FALLBACK_RESPECT_ALL_WINDOWED_BLOCKS"), True):
            out.extend(f"controlled_fallback_windowed_block:{reason}" for reason in reject_reasons[:3])
        if out:
            _GUARD_EVENTS.append({
                "guard": "windowed_publication_filter",
                "match_key": candidate.get("match_key"),
                "home_team": candidate.get("home_team"),
                "away_team": candidate.get("away_team"),
                "family": candidate.get("family"),
                "selection": candidate.get("selection"),
                "point": candidate.get("point"),
                "reasons": out,
                "windowed_reject_reasons": reject_reasons,
                "movement": movement,
            })
        return out
    return []


def _duplicate_previous_report_reason(candidate: dict[str, Any]) -> str | None:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DEDUPE_PREVIOUS_REPORT"), True):
        return None
    report = _load_json(ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json", {})
    if not isinstance(report, dict) or not report.get("published"):
        return None
    rows = report.get("selected_all") or ([report.get("selected")] if isinstance(report.get("selected"), dict) else [])
    if not isinstance(rows, list):
        return None
    max_hours = int(float(os.getenv("CONTROLLED_FALLBACK_PREVIOUS_REPORT_DEDUPE_HOURS") or 72))
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, max_hours))
    for row in rows:
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff"))
        if kickoff is not None and kickoff < datetime.now(UTC):
            continue
        sent_at = _parse_dt(report.get("created_at") or row.get("sent_at"))
        if sent_at is not None and sent_at < cutoff:
            continue
        if _same_candidate(candidate, row):
            return "duplicate_previous_controlled_fallback_report"
    return None


def _duplicate_sent_index_reason(candidate: dict[str, Any]) -> str | None:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DEDUPE_SENT_INDEX_STRICT"), True):
        return None
    payload = _load_json(ROOT / ".data" / "fallback-sent-index.json", {})
    if not isinstance(payload, dict):
        return None
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff"))
        if kickoff is not None and kickoff < datetime.now(UTC):
            continue
        if _same_candidate(candidate, row):
            return "duplicate_persisted_fallback_sent_index"
    return None


_original_hard_reject_reasons = base.hard_reject_reasons


def hard_reject_reasons_guarded(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
    reasons = list(_original_hard_reject_reasons(candidate, metrics, sent_index) or [])
    extra = []
    duplicate = _duplicate_sent_index_reason(candidate) or _duplicate_previous_report_reason(candidate)
    if duplicate:
        extra.append(duplicate)
    extra.extend(_windowed_movement_reasons(candidate))
    if extra:
        _GUARD_EVENTS.append({
            "guard": "controlled_fallback_prepublish",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "reasons": extra,
        })
    return reasons + extra


base.hard_reject_reasons = hard_reject_reasons_guarded


def main() -> int:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "windowed_filter_path": str(ROOT / ".data" / "exports" / "latest-windowed-core-publication-filter.json"),
        "events": [],
    }
    try:
        code = int(base.main() or 0)
        payload["status"] = "ok" if code == 0 else "base_returned_nonzero"
        payload["base_exit_code"] = code
        return code
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        payload["status"] = "system_exit"
        payload["base_exit_code"] = code
        return code
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        payload["events"] = _GUARD_EVENTS[:100]
        payload["blocked_events"] = len(_GUARD_EVENTS)
        _write_json(REPORT_PATH, payload)


if __name__ == "__main__":
    raise SystemExit(main())
