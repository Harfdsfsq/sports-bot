from __future__ import annotations

"""Final CandidateFactory output de-duplication layer.

Several runtime rescue/coverage wrappers can materialize the same logical pick
from two sources.  That is useful during discovery, but the runner/quality/fallback
must see one row per match+market+selection+line.  This wrapper is deliberately
last in the runtime chain and only removes exact logical duplicates.  It does not
loosen any quality, value or Telegram publication guard.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-candidate-factory-output-dedup.json"
PATCH_MARKER = "_harizon_candidate_factory_output_dedup_v1"


def _field(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.replace("ё", "е").split())


def _point(value: Any) -> str:
    try:
        if value in (None, ""):
            return ""
        return f"{float(str(value).replace(',', '.')):.3f}"
    except Exception:
        return _norm(value)


def _key(candidate: Any) -> tuple[str, str, str, str, str, str]:
    return (
        _norm(_field(candidate, "match_key")) or f"{_norm(_field(candidate, 'home_team'))}|{_norm(_field(candidate, 'away_team'))}|{_norm(_field(candidate, 'commence_time'))[:10]}",
        _norm(_field(candidate, "family")),
        _norm(_field(candidate, "selection")),
        _point(_field(candidate, "point")),
        _norm(_field(candidate, "team_side")),
        _norm(_field(candidate, "period") or "full_time"),
    )


def _score(candidate: Any) -> tuple[float, float, float, int, int]:
    def f(name: str) -> float:
        try:
            return float(str(_field(candidate, name, 0.0)).replace(",", "."))
        except Exception:
            return 0.0
    def i(name: str) -> int:
        try:
            return int(float(str(_field(candidate, name, 0))))
        except Exception:
            return 0
    return (f("ev_pct"), f("edge_pct"), f("confidence"), i("sources_count"), i("books_count"))


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _dedupe(candidates: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    best: dict[tuple[str, str, str, str, str, str], Any] = {}
    duplicates: list[dict[str, Any]] = []
    for cand in candidates:
        key = _key(cand)
        current = best.get(key)
        if current is None:
            best[key] = cand
            continue
        if _score(cand) > _score(current):
            duplicates.append({"key": "|".join(key), "kept": _score(cand), "dropped": _score(current)})
            best[key] = cand
        else:
            duplicates.append({"key": "|".join(key), "kept": _score(current), "dropped": _score(cand)})
    return list(best.values()), duplicates


def install() -> dict[str, Any]:
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        _write(payload)
        return payload

    current = getattr(CandidateFactory, "build_candidates", None)
    if getattr(current, PATCH_MARKER, False):
        return {"status": "already_installed"}
    if not callable(current):
        payload = {"status": "error", "error": "build_candidates_missing"}
        _write(payload)
        return payload

    def build_candidates_dedup(self: Any, matches: Any, offers_by_match: Any, contexts_by_match: Any, market_signals_by_match: Any = None):
        candidates, rejections, debug = current(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        rows = list(candidates or [])
        deduped, duplicates = _dedupe(rows)
        debug = dict(debug or {})
        debug["candidate_factory_output_dedup"] = {
            "input_candidates": len(rows),
            "returned": len(deduped),
            "duplicates_removed": len(duplicates),
            "sample": duplicates[:20],
        }
        if duplicates and isinstance(rejections, dict):
            rejections["duplicate_logical_candidate_removed"] = int(rejections.get("duplicate_logical_candidate_removed", 0) or 0) + len(duplicates)
        _write({
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "ok",
            "input_candidates": len(rows),
            "returned": len(deduped),
            "duplicates_removed": len(duplicates),
            "sample": duplicates[:30],
        })
        return deduped, rejections, debug

    setattr(build_candidates_dedup, PATCH_MARKER, True)
    CandidateFactory.build_candidates = build_candidates_dedup  # type: ignore[assignment]
    payload = {"status": "installed", "wrapper": "candidate_factory_output_dedup"}
    _write(payload)
    return payload
