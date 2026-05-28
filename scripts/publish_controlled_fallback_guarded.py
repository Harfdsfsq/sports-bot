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


def _movement_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _candidate_confirmed_movement(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """Return a fresh/current movement payload already attached to the candidate.

    The line-movement job may update latest-rescue-candidates after the earlier
    windowed audit has written a stale `needs_next_cron_line_movement_recheck`
    block. Controlled fallback must prefer the candidate-level guard when it is
    positive, otherwise the same candidate keeps waiting forever even though the
    second snapshot is already confirmed.
    """
    if not isinstance(candidate, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for key in ("line_movement_guard", "movement", "market_movement_guard"):
        payload = candidate.get(key)
        if isinstance(payload, dict):
            candidates.append(payload)
    diagnostics = candidate.get("diagnostics")
    if isinstance(diagnostics, dict):
        payload = diagnostics.get("line_movement_guard")
        if isinstance(payload, dict):
            candidates.append(payload)
    source_summary = candidate.get("source_summary")
    if isinstance(source_summary, dict):
        payload = source_summary.get("line_movement_guard")
        if isinstance(payload, dict):
            candidates.append(payload)
        status = _movement_status(source_summary.get("line_movement_lifecycle_status") or source_summary.get("market_movement") or source_summary.get("market_move"))
        publication_status = _movement_status(source_summary.get("publication_lifecycle_status"))
        if status in {"movement_confirmed", "publish_now_no_next_cron"} and publication_status in {"", "movement_ready", "publishable", "published"}:
            candidates.append({
                "passed": True,
                "status": status,
                "line_movement_lifecycle_status": status,
                "reasons": [],
                "source": "candidate.source_summary",
            })

    for payload in candidates:
        status = _movement_status(payload.get("status") or payload.get("line_movement_lifecycle_status") or payload.get("market_move"))
        if bool(payload.get("passed")) and status in {"movement_confirmed", "publish_now_no_next_cron"}:
            out = dict(payload)
            out["passed"] = True
            out["status"] = status
            out["line_movement_lifecycle_status"] = status
            out["reasons"] = []
            out.setdefault("source", "candidate.line_movement_guard")
            return out
    return None


def _windowed_movement_reasons(candidate: dict[str, Any]) -> list[str]:
    if _candidate_confirmed_movement(candidate) is not None:
        _GUARD_EVENTS.append({
            "guard": "windowed_publication_filter",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "decision": "ignored_stale_windowed_block_candidate_movement_confirmed",
        })
        return []
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


_original_controlled_line_movement_report = getattr(base, "controlled_line_movement_report", None)


def controlled_line_movement_report_guarded(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    confirmed = _candidate_confirmed_movement(candidate)
    if confirmed is not None:
        metrics["line_movement"] = confirmed
        _GUARD_EVENTS.append({
            "guard": "controlled_line_movement_report",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "decision": "used_candidate_confirmed_movement",
            "movement": confirmed,
        })
        return confirmed
    if callable(_original_controlled_line_movement_report):
        return _original_controlled_line_movement_report(candidate, metrics)
    return {"passed": False, "status": "unavailable", "reasons": ["line_movement_report_unavailable"]}


if hasattr(base, "controlled_line_movement_report"):
    base.controlled_line_movement_report = controlled_line_movement_report_guarded



_original_tier_reasons = getattr(base, "tier_reasons", None)


def _tier_name(value: Any) -> str:
    return str(value or "").replace("уровень", "").strip().upper()


def tier_reasons_guarded(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons = list(_original_tier_reasons(tier, candidate, metrics) or []) if callable(_original_tier_reasons) else []
    tier_name = _tier_name(tier)

    # Project contract: A-tier means 2+ independent odds sources, 2+ context/confirmation
    # sources, confirmed movement and value. The base fallback used bookmaker/line count
    # as enough for "уровень A", which made a 3-book / 1-provider pick look like A-tier.
    # Keep such candidates publishable as B-tier when they satisfy all safety checks, but
    # do not label them A-tier unless there are at least two independent odds providers.
    if tier_name == "A" and _truthy(os.getenv("CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES"), True):
        min_odds_sources = int(float(os.getenv("CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES") or 2))
        odds_sources = int(metrics.get("odds_sources_count") or 0)
        if odds_sources < min_odds_sources:
            reasons.append(f"tier_a_odds_sources_below_min:{odds_sources}/{min_odds_sources}")
    return reasons


if callable(_original_tier_reasons):
    base.tier_reasons = tier_reasons_guarded


_original_final_publish_guard_reasons = getattr(base, "final_publish_guard_reasons", None)


def final_publish_guard_reasons_guarded(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> list[str]:
    reasons = list(_original_final_publish_guard_reasons(candidate, metrics, tier) or []) if callable(_original_final_publish_guard_reasons) else []
    tier_name = _tier_name(tier)

    # B-tier lifecycle rule: if the match will start before the next regular cron pass,
    # the candidate may be published after the same final checks as A-tier. The original
    # fallback only allowed publish_now_no_next_cron for non-B tiers, which encouraged
    # false A-tier labelling for safe but one-provider B-tier candidates.
    movement = metrics.get("line_movement") if isinstance(metrics.get("line_movement"), dict) else _candidate_confirmed_movement(candidate)
    status = _movement_status((movement or {}).get("status") or (movement or {}).get("line_movement_lifecycle_status")) if isinstance(movement, dict) else ""
    if tier_name == "B" and status == "publish_now_no_next_cron" and bool((movement or {}).get("passed")):
        reasons = [r for r in reasons if r != "line_movement_not_confirmed:publish_now_no_next_cron"]
        _GUARD_EVENTS.append({
            "guard": "final_publish_guard",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "decision": "allowed_b_tier_publish_now_no_next_cron",
            "movement": movement,
        })
    return reasons


if callable(_original_final_publish_guard_reasons):
    base.final_publish_guard_reasons = final_publish_guard_reasons_guarded


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
