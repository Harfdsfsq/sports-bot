from __future__ import annotations

"""Deduplicate final CandidateFactory output.

Multiple late runtime wrappers can materialize the same logical candidate twice:
same match, market family, selection and line.  That inflates
`candidates_before_quality`, controlled-fallback pool counts and reject reasons.
This patch is intentionally conservative: it keeps the best-ranked candidate for
each logical key and does not alter probabilities, odds, EV or publish guards.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-candidate-factory-output-dedup.json"
_MARKER = "_harizon_candidate_factory_output_dedup_v3"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("|", " ").split())


def _line_value(candidate: Any) -> str:
    value = getattr(candidate, "point", None)
    if value in (None, ""):
        value = getattr(candidate, "line", None)
    if value not in (None, ""):
        try:
            return f"{float(str(value).replace(',', '.')):.3f}".rstrip("0").rstrip(".")
        except Exception:
            pass
    return _norm(value)


def candidate_key(candidate: Any) -> tuple[str, str, str, str]:
    match_key = getattr(candidate, "match_key", None) or (
        f"{getattr(candidate, 'sport', '')}|{getattr(candidate, 'home_team', '')}|"
        f"{getattr(candidate, 'away_team', '')}|{getattr(candidate, 'commence_time', '')}"
    )
    return (
        _norm(match_key),
        _norm(getattr(candidate, "family", None) or getattr(candidate, "market_family", None)),
        _norm(getattr(candidate, "selection", None) or getattr(candidate, "selection_key", None)),
        _line_value(candidate),
    )


def _rank(candidate: Any) -> tuple[float, float, float, float, float]:
    return (
        _as_float(getattr(candidate, "publication_score", None)),
        _as_float(getattr(candidate, "ev_pct", None)),
        _as_float(getattr(candidate, "edge_pct", None)),
        _as_float(getattr(candidate, "confidence", None)),
        _as_float(getattr(candidate, "quality_score", None)),
    )


def dedupe_candidates(candidates: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    best: dict[tuple[str, str, str, str], Any] = {}
    order: list[tuple[str, str, str, str]] = []
    duplicates: list[dict[str, Any]] = []
    for candidate in candidates or []:
        key = candidate_key(candidate)
        current = best.get(key)
        if current is None:
            best[key] = candidate
            order.append(key)
        elif _rank(candidate) > _rank(current):
            duplicates.append({
                "key": "|".join(key),
                "dropped_match_key": getattr(current, "match_key", ""),
                "kept_match_key": getattr(candidate, "match_key", ""),
                "reason": "lower_rank_duplicate",
            })
            best[key] = candidate
        else:
            duplicates.append({
                "key": "|".join(key),
                "dropped_match_key": getattr(candidate, "match_key", ""),
                "kept_match_key": getattr(current, "match_key", ""),
                "reason": "lower_rank_duplicate",
            })
    return [best[key] for key in order if key in best], duplicates


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    current = getattr(CandidateFactory, "build_candidates", None)
    if getattr(current, _MARKER, False):
        return {"status": "already_installed", "marker": _MARKER}

    original_build = current

    def build_candidates_dedup(self: Any, matches: Any, offers_by_match: Any, contexts_by_match: Any, market_signals_by_match: Any = None):
        candidates, rejections, debug = original_build(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        rows = list(candidates or [])
        kept, duplicates = dedupe_candidates(rows)
        rejections = dict(rejections or {})
        debug = dict(debug or {})
        report = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "input_candidates": len(rows),
            "returned": len(kept),
            "duplicates_removed": len(duplicates),
            "sample": duplicates[:20],
        }
        if duplicates:
            rejections["candidate_factory_duplicate_output"] = int(rejections.get("candidate_factory_duplicate_output", 0) or 0) + len(duplicates)
        debug["candidate_factory_output_dedup"] = report
        _write_report(report)
        return kept, rejections, debug

    setattr(build_candidates_dedup, _MARKER, True)
    CandidateFactory.build_candidates = build_candidates_dedup  # type: ignore[assignment]
    return {"status": "installed", "marker": _MARKER}
