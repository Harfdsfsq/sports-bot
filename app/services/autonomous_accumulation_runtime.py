from __future__ import annotations

"""Strict public picks plus complete shadow accumulation for a seven-day audit."""

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "autonomous-accumulation"
COVERAGE = OUT / "latest-coverage-matrix.json"
COVERAGE_LEDGER = OUT / "coverage-run-ledger.jsonl"
PREDICTION_LEDGER = OUT / "prediction-ledger.jsonl"
LATEST = OUT / "latest-accumulation-report.json"
_INSTALLED = False
_SEEN_ROWS: set[str] = set()
_SEEN_RUNS: set[str] = set()

SOURCE_ALIASES = {
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "account1": "odds_api_io",
    "account2": "odds_api_io",
    "bzzoiro_v2": "bzzoiro",
    "sportlogic_controlled": "sportlogic",
    "sstats_current_odds": "sstats",
}
NON_CORE_CONTEXT = {
    "ensemble", "market", "market_signal", "unknown", "self_history",
    "weather", "weatherapi", "open_meteo", "openweathermap",
    "news", "newsapi", "gnews", "currents",
}
PUBLIC_MODES = {"xg_total", "xg_spread"}
PUBLIC_FAMILIES = {"totals", "spreads"}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _env_float(name: str, default: float) -> float:
    value = _float(os.getenv(name))
    return value if value is not None else default


def _source(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return SOURCE_ALIASES.get(raw, raw)


def _csv(name: str, default: set[str]) -> set[str]:
    raw = str(os.getenv(name) or "").strip()
    return {part.strip().lower() for part in raw.split(",") if part.strip()} if raw else set(default)


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _append(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _run_id(now: datetime | None = None) -> str:
    github = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    if github:
        return f"github:{github}:{os.getenv('GITHUB_RUN_ATTEMPT') or '1'}"
    return f"local:{(now or datetime.now(UTC)).strftime('%Y%m%dT%H%M%S%fZ')}"


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value or "").strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def _bucket(value: Any, now: datetime) -> tuple[str, float, int]:
    kickoff = _utc(value)
    if kickoff is None:
        return "unknown", 999999.0, 99
    hours = (kickoff - now).total_seconds() / 3600
    if hours <= 0:
        return "started", hours, 98
    for rank, (upper, label) in enumerate(((4, "0-4h"), (8, "4-8h"), (12, "8-12h"), (16, "12-16h"), (20, "16-20h"), (24, "20-24h"))):
        if hours <= upper:
            return label, hours, rank
    return "24h+", hours, 6


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _offer_stats(offers: list[Any]) -> dict[str, Any]:
    sources: set[str] = set()
    books: set[str] = set()
    families: set[str] = set()
    exact: dict[tuple[str, str, str, str], tuple[set[str], set[str]]] = defaultdict(lambda: (set(), set()))
    for offer in offers:
        src = _source(_get(offer, "source", ""))
        book = str(_get(offer, "bookmaker", "") or "").strip().lower()
        family = str(_get(offer, "family", "") or "").strip().lower()
        selection = str(_get(offer, "selection", "") or "").strip().lower()
        point_raw = _get(offer, "point", None)
        point = "" if point_raw in (None, "") else f"{float(point_raw):.4f}"
        side = str(_get(offer, "team_side", "") or "").strip().lower()
        if src:
            sources.add(src)
        if book:
            books.add(book)
        if family:
            families.add(family)
        bucket_sources, bucket_books = exact[(family, selection, point, side)]
        if src:
            bucket_sources.add(src)
        if book:
            bucket_books.add(book)
    best = max(exact.values(), key=lambda x: (len(x[0]), len(x[1])), default=(set(), set()))
    return {
        "offer_count": len(offers),
        "odds_sources": sorted(sources),
        "odds_source_count": len(sources),
        "books": sorted(books),
        "book_count": len(books),
        "families": sorted(families),
        "line_family_count": len(families),
        "max_exact_odds_sources": len(best[0]),
        "max_exact_books": len(best[1]),
    }


def _context_sources(value: Any, depth: int = 0) -> set[str]:
    if value is None or depth > 5:
        return set()
    found: set[str] = set()
    for attr in ("provider", "source"):
        src = _source(getattr(value, attr, ""))
        if src:
            found.add(src)
    details = getattr(value, "details", None)
    if isinstance(details, dict):
        value = {"object_source": getattr(value, "source", ""), **details}
    if isinstance(value, dict):
        for key in ("provider", "source", "context_source"):
            src = _source(value.get(key))
            if src:
                found.add(src)
        for key in ("context_sources", "merged_sources", "source_tokens", "confirmation_sources"):
            raw = value.get(key) or []
            raw = raw.replace(";", ",").split(",") if isinstance(raw, str) else raw
            if isinstance(raw, (list, tuple, set)):
                found.update(_source(x) for x in raw if _source(x))
        for key, nested in list(value.items())[:50]:
            normalized = _source(key)
            if normalized in {"bzzoiro", "sstats", "sportlogic", "football_data", "thesportsdb", "api_football", "futrixmetrics", "clubelo"} and nested:
                found.add(normalized)
            if isinstance(nested, (dict, list, tuple, set)) or hasattr(nested, "source") or hasattr(nested, "provider"):
                found.update(_context_sources(nested, depth + 1))
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            found.update(_context_sources(nested, depth + 1))
    contexts = getattr(value, "contexts", None)
    if isinstance(contexts, (list, tuple, set)):
        for nested in contexts:
            found.update(_context_sources(nested, depth + 1))
    return {x for x in found if x}


def _context_flags(value: Any) -> dict[str, bool]:
    text = json.dumps(_serialize(value), ensure_ascii=False).lower()
    merged = getattr(value, "merged_context", None)
    expected_home = getattr(merged or value, "expected_home", None)
    expected_away = getattr(merged or value, "expected_away", None)
    return {
        "has_expected_goals": (expected_home is not None and expected_away is not None) or any(x in text for x in ('"xg"', "expected_home", "expected_away")),
        "has_probabilities": any(x in text for x in ("home_win_probability", "away_win_probability", "prob_over", "probability")),
        "has_lineups": any(x in text for x in ("lineup", "starting_xi", "home_starting", "away_starting")),
        "has_injuries": any(x in text for x in ("injur", "absence", "suspend")),
        "has_form": any(x in text for x in ("form", "last5", "last_5", "rolling")),
    }


def _coverage_report(matches: list[Any], offers: dict[str, list[Any]], contexts: dict[str, Any], now: datetime, run_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    levels: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    for match in matches:
        key = str(getattr(match, "match_key", "") or "")
        odds = _offer_stats(list(offers.get(key) or []))
        all_context = _context_sources(contexts.get(key))
        core_context = sorted(x for x in all_context if x not in NON_CORE_CONTEXT)
        flags = _context_flags(contexts.get(key))
        time_bucket, hours, rank = _bucket(getattr(match, "commence_time", None), now)
        strict_lines = odds["max_exact_odds_sources"] >= 2 and odds["max_exact_books"] >= 2
        if strict_lines and len(core_context) >= 2:
            level = "L3"
        elif strict_lines and core_context:
            level = "L2"
        elif odds["offer_count"] and core_context:
            level = "L1"
        else:
            level = "L0"
        gaps: list[str] = []
        if not odds["offer_count"]:
            gaps.append("odds")
        if odds["max_exact_odds_sources"] < 2:
            gaps.append("second_independent_odds_source")
        if odds["max_exact_books"] < 2:
            gaps.append("second_bookmaker")
        if len(core_context) < 2:
            gaps.append("second_core_context")
        if not flags["has_expected_goals"]:
            gaps.append("expected_goals")
        missing.update(gaps)
        levels[level] += 1
        buckets[time_bucket] += 1
        rows.append({
            "match_key": key,
            "home_team": str(getattr(match, "home_team", "") or ""),
            "away_team": str(getattr(match, "away_team", "") or ""),
            "league_name": str(getattr(match, "league_name", "") or ""),
            "kickoff_utc": _serialize(getattr(match, "commence_time", None)),
            "time_bucket": time_bucket,
            "hours_to_kickoff": round(hours, 3),
            "priority_rank": rank,
            **odds,
            "all_context_sources": sorted(all_context),
            "all_context_source_count": len(all_context),
            "core_context_sources": core_context,
            "core_context_source_count": len(core_context),
            **flags,
            "strict_line_coverage": strict_lines,
            "strict_context_coverage": len(core_context) >= 2,
            "full_2plus_coverage": level == "L3",
            "coverage_level": level,
            "missing_roles": gaps,
        })
    rows.sort(key=lambda r: (r["priority_rank"], r["hours_to_kickoff"], len(r["missing_roles"]), r["match_key"]))
    total = len(rows)
    full = levels.get("L3", 0)
    summary = {
        "matches_total": total,
        "matches_with_any_odds": sum(r["offer_count"] > 0 for r in rows),
        "matches_with_2plus_exact_odds_sources": sum(r["max_exact_odds_sources"] >= 2 for r in rows),
        "matches_with_2plus_bookmakers": sum(r["max_exact_books"] >= 2 for r in rows),
        "matches_with_2plus_core_contexts": sum(r["core_context_source_count"] >= 2 for r in rows),
        "matches_full_2plus_coverage": full,
        "full_coverage_pct": round(full / total * 100, 2) if total else 0.0,
        "coverage_levels": dict(levels),
        "time_buckets": dict(buckets),
        "missing_roles": dict(missing),
    }
    return {
        "schema_version": 1,
        "created_at_utc": now.isoformat(),
        "run_id": run_id,
        "summary": summary,
        "matches": rows,
        "gap_queue": [r for r in rows if not r["full_2plus_coverage"]],
    }


def _candidate_coverage(candidate: Any, settings: Any) -> dict[str, Any]:
    try:
        from app.services.coverage_contract import sync_candidate_publish_coverage
        report = dict(sync_candidate_publish_coverage(candidate, settings).report)
        all_context = {_source(x) for x in report.get("context_sources") or [] if _source(x)}
        core = sorted(x for x in all_context if x not in NON_CORE_CONTEXT)
        report["core_context_sources"] = core
        report["core_context_sources_count"] = len(core)
        return report
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "core_context_sources_count": 0}


def _candidate_id(candidate: Any) -> str:
    return "|".join(str(getattr(candidate, key, "") or "") for key in ("match_key", "family", "selection_key", "point", "team_side"))


def _conflict(candidate: Any) -> float | None:
    def walk(value: Any, depth: int = 0) -> float | None:
        if depth > 5:
            return None
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {"provider_conflict_score", "context_conflict_score"}:
                    number = _float(item)
                    if number is not None:
                        return number
            for item in value.values():
                found = walk(item, depth + 1)
                if found is not None:
                    return found
        elif isinstance(value, (list, tuple)):
            for item in value[:30]:
                found = walk(item, depth + 1)
                if found is not None:
                    return found
        return None
    for value in (
        getattr(candidate, "source_summary", {}) or {},
        getattr(candidate, "diagnostics", {}) or {},
        getattr(candidate, "analysis", {}) or {},
    ):
        found = walk(value)
        if found is not None:
            return found
    return None


def _calibrate(candidate: Any, settings: Any) -> None:
    if str(getattr(candidate, "model_mode", "") or "").lower() not in PUBLIC_MODES:
        return
    market = _float(getattr(candidate, "market_probability", None))
    model = _float(getattr(candidate, "model_probability", None))
    current = _float(getattr(candidate, "adjusted_probability", None))
    if market is None or model is None or current is None:
        return
    coverage = _candidate_coverage(candidate, settings)
    core = int(coverage.get("core_context_sources_count") or 0)
    odds_sources = int(coverage.get("odds_sources_count") or 0)
    books = int(coverage.get("books_count") or 0)
    weight = 0.55
    weight += min(0.12, max(0, core - 2) * 0.04)
    weight += min(0.08, max(0, odds_sources - 2) * 0.04)
    weight += min(0.06, max(0, books - 2) * 0.03)
    if core < 2:
        weight = min(weight, 0.45)
    if odds_sources < 2 or books < 2:
        weight = min(weight, 0.50)
    conflict = _conflict(candidate)
    if conflict is not None:
        weight -= min(0.25, max(0, conflict) * 0.35)
    weight = min(0.85, max(0.30, weight))
    conservative = market + (model - market) * weight
    probability = min(current, conservative) if model >= market else conservative
    probability = min(0.98, max(0.02, probability))
    odds = _float(getattr(candidate, "selected_odds", None)) or _float(getattr(candidate, "odds", None)) or 0.0
    implied = _float(getattr(candidate, "selected_implied_probability", None))
    if implied is None:
        implied = _float(getattr(candidate, "implied_probability", None))
    if implied is None:
        implied = 1 / odds if odds > 1 else 0.0
    for attr in ("adjusted_probability", "final_probability", "canonical_adjusted_probability", "probability_used_for_ev"):
        setattr(candidate, attr, probability)
    if odds > 1:
        candidate.fair_odds = 1 / probability
        candidate.edge_pct = round((probability - implied) * 100, 4)
        candidate.ev_pct = round((probability * odds - 1) * 100, 4)
        candidate.price_used_for_ev = odds
    details = {
        "applied": True,
        "reliability_weight": round(weight, 4),
        "original_probability": current,
        "calibrated_probability": probability,
        "post_shrink_ev_pct": getattr(candidate, "ev_pct", None),
    }
    diagnostics = dict(getattr(candidate, "diagnostics", {}) or {})
    diagnostics["autonomous_probability_calibration"] = details
    candidate.diagnostics = diagnostics
    summary = dict(getattr(candidate, "source_summary", {}) or {})
    summary["autonomous_probability_calibration"] = details
    candidate.source_summary = summary


def _safety(candidate: Any, settings: Any) -> list[str]:
    mode = str(getattr(candidate, "model_mode", "") or "").lower()
    family = str(getattr(candidate, "family", "") or "").lower()
    coverage = _candidate_coverage(candidate, settings)
    reasons: list[str] = []
    if mode not in _csv("AUTONOMOUS_PUBLIC_MODEL_MODES", PUBLIC_MODES):
        reasons.append(f"shadow_only_model_mode:{mode or 'unknown'}")
    if family not in _csv("AUTONOMOUS_PUBLIC_FAMILIES", PUBLIC_FAMILIES):
        reasons.append(f"shadow_only_market_family:{family or 'unknown'}")
    min_odds_sources = max(1, int(getattr(settings, "min_sources_publish", 2) or 2))
    min_books = max(1, int(getattr(settings, "min_books_publish", 2) or 2))
    min_context_sources = max(1, int(getattr(settings, "min_context_sources_publish", 2) or 2))
    if int(coverage.get("odds_sources_count") or 0) < min_odds_sources:
        reasons.append(f"strict_odds_sources_below_{min_odds_sources}")
    if int(coverage.get("books_count") or 0) < min_books:
        reasons.append(f"strict_bookmakers_below_{min_books}")
    if int(coverage.get("core_context_sources_count") or 0) < min_context_sources:
        reasons.append(f"strict_core_context_sources_below_{min_context_sources}")
    if mode in PUBLIC_MODES:
        if getattr(candidate, "expected_home", None) is None or getattr(candidate, "expected_away", None) is None:
            reasons.append("xg_inputs_missing")
        model = _float(getattr(candidate, "model_probability", None))
        market = _float(getattr(candidate, "market_probability", None))
        if model is None or market is None:
            reasons.append("model_market_probability_missing")
        else:
            gap = (model - market) * 100
            minimum = _env_float("AUTONOMOUS_MIN_MODEL_EDGE_PP", 1.0)
            maximum = _env_float("AUTONOMOUS_MAX_MODEL_EDGE_PP", 12.0)
            if gap < minimum:
                reasons.append(f"independent_model_edge_below_min:{gap:.3f}/{minimum:.3f}")
            if gap > maximum:
                reasons.append(f"suspicious_model_market_gap:{gap:.3f}/{maximum:.3f}")
    min_ev = _env_float("AUTONOMOUS_MIN_POST_SHRINK_EV_PCT", 1.5)
    if float(getattr(candidate, "ev_pct", 0.0) or 0.0) < min_ev:
        reasons.append(f"post_shrink_ev_below_min:{float(getattr(candidate, 'ev_pct', 0.0) or 0.0):.3f}/{min_ev:.3f}")
    min_conf = _env_float("AUTONOMOUS_MIN_CONFIDENCE", 55.0)
    if float(getattr(candidate, "confidence", 0.0) or 0.0) < min_conf:
        reasons.append(f"confidence_below_min:{float(getattr(candidate, 'confidence', 0.0) or 0.0):.3f}/{min_conf:.3f}")
    conflict = _conflict(candidate)
    max_conflict = _env_float("AUTONOMOUS_MAX_PROVIDER_CONFLICT_SCORE", 0.45)
    if conflict is not None and conflict > max_conflict:
        reasons.append(f"provider_conflict_above_max:{conflict:.3f}/{max_conflict:.3f}")
    return reasons


def _candidate_row(candidate: Any, settings: Any, run_id: str, stage: str, status: str, safety: list[str] | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    time_bucket, hours, _ = _bucket(getattr(candidate, "commence_time", None), now)
    return {
        "schema_version": 1,
        "created_at_utc": now.isoformat(),
        "run_id": run_id,
        "stage": stage,
        "status": status,
        "candidate_id": _candidate_id(candidate),
        "match_key": str(getattr(candidate, "match_key", "") or ""),
        "home_team": str(getattr(candidate, "home_team", "") or ""),
        "away_team": str(getattr(candidate, "away_team", "") or ""),
        "league_name": str(getattr(candidate, "league_name", "") or ""),
        "kickoff_utc": _serialize(getattr(candidate, "commence_time", None)),
        "time_bucket": time_bucket,
        "hours_to_kickoff": round(hours, 3),
        "family": str(getattr(candidate, "family", "") or ""),
        "selection": str(getattr(candidate, "selection", "") or ""),
        "selection_key": str(getattr(candidate, "selection_key", "") or ""),
        "point": getattr(candidate, "point", None),
        "team_side": getattr(candidate, "team_side", None),
        "model_mode": str(getattr(candidate, "model_mode", "") or ""),
        "odds": _float(getattr(candidate, "odds", None)),
        "selected_odds": _float(getattr(candidate, "selected_odds", None)),
        "market_probability": _float(getattr(candidate, "market_probability", None)),
        "model_probability": _float(getattr(candidate, "model_probability", None)),
        "adjusted_probability": _float(getattr(candidate, "adjusted_probability", None)),
        "edge_pct": _float(getattr(candidate, "edge_pct", None)),
        "ev_pct": _float(getattr(candidate, "ev_pct", None)),
        "confidence": _float(getattr(candidate, "confidence", None)),
        "expected_home": _float(getattr(candidate, "expected_home", None)),
        "expected_away": _float(getattr(candidate, "expected_away", None)),
        "coverage": _candidate_coverage(candidate, settings),
        "autonomous_safety_reasons": list(safety or []),
        "reasons": list(getattr(candidate, "reasons", []) or []),
        "source_summary": _serialize(getattr(candidate, "source_summary", {}) or {}),
        "diagnostics": _serialize(getattr(candidate, "diagnostics", {}) or {}),
        "raw_bucket_offers": _serialize(list(getattr(candidate, "raw_bucket_offers", []) or [])[:50]),
    }


def _write_candidate_rows(rows: Iterable[dict[str, Any]]) -> None:
    fresh = []
    for row in rows:
        key = "|".join(str(row.get(x) or "") for x in ("run_id", "stage", "candidate_id", "status"))
        if key not in _SEEN_ROWS:
            _SEEN_ROWS.add(key)
            fresh.append(row)
    _append(PREDICTION_LEDGER, fresh)


def _update_latest(**updates: Any) -> None:
    payload: dict[str, Any] = {}
    try:
        if LATEST.exists():
            payload = json.loads(LATEST.read_text(encoding="utf-8"))
    except Exception:
        pass
    payload.update(updates)
    payload["updated_at_utc"] = datetime.now(UTC).isoformat()
    payload["artifacts"] = {
        "coverage": str(COVERAGE),
        "coverage_ledger": str(COVERAGE_LEDGER),
        "predictions": str(PREDICTION_LEDGER),
    }
    _write_json(LATEST, payload)


def _patch_factory() -> dict[str, Any]:
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    original = CandidateFactory.build_candidates
    if getattr(original, "_harizon_autonomous_accumulation", False):
        return {"status": "already_wrapped"}

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        candidates, rejections, debug = original(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        if not _truthy(os.getenv("AUTONOMOUS_ACCUMULATION_LEDGER_ENABLED"), True):
            return candidates, rejections, debug
        now = datetime.now(UTC)
        run_id = _run_id(now)
        try:
            report = _coverage_report(
                list(matches or []),
                dict(offers_by_match or {}),
                dict(contexts_by_match or {}),
                now,
                run_id,
            )
            _write_json(COVERAGE, report)
            if run_id not in _SEEN_RUNS:
                _SEEN_RUNS.add(run_id)
                _append(COVERAGE_LEDGER, [{
                    "created_at_utc": now.isoformat(),
                    "run_id": run_id,
                    "summary": report["summary"],
                    "nearest_gaps": report["gap_queue"][:30],
                }])
            _write_candidate_rows(
                _candidate_row(c, self.settings, run_id, "pre_quality", "candidate_built")
                for c in candidates
            )
            debug = dict(debug or {})
            debug["autonomous_accumulation"] = {
                "run_id": run_id,
                "coverage_summary": report["summary"],
            }
            _update_latest(
                run_id=run_id,
                stage="pre_quality",
                coverage_summary=report["summary"],
                raw_candidates=len(candidates),
            )
        except Exception as exc:
            debug = dict(debug or {})
            debug["autonomous_accumulation"] = {"error": f"{type(exc).__name__}: {exc}"}
        return candidates, rejections, debug

    wrapped._harizon_autonomous_accumulation = True
    CandidateFactory.build_candidates = wrapped
    return {"status": "wrapped"}


def _patch_quality() -> dict[str, Any]:
    try:
        from app.services.quality import PredictionQualityService
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    original = PredictionQualityService.apply_to_candidates
    if getattr(original, "_harizon_autonomous_accumulation", False):
        return {"status": "already_wrapped"}

    def wrapped(self, candidates, quality_report, now_utc):
        candidates = list(candidates or [])
        for candidate in candidates:
            try:
                _calibrate(candidate, self.settings)
            except Exception:
                pass
        passed, rejections, debug = original(self, candidates, quality_report, now_utc)
        safe = []
        blocked = []
        for candidate in list(passed or []):
            reasons = _safety(candidate, self.settings)
            summary = dict(getattr(candidate, "source_summary", {}) or {})
            summary["autonomous_publication_safety_passed"] = not reasons
            summary["autonomous_publication_safety_reasons"] = reasons
            candidate.source_summary = summary
            if reasons:
                candidate.reasons.extend(f"autonomous_safety={x}" for x in reasons)
                blocked.append((candidate, reasons))
            else:
                safe.append(candidate)
        counter = Counter(rejections or {})
        for _, reasons in blocked:
            counter[f"autonomous_safety_{reasons[0]}"] += 1
        run_id = _run_id(_utc(now_utc) or datetime.now(UTC))
        debug = dict(debug or {})
        debug["autonomous_accumulation"] = {
            "run_id": run_id,
            "quality_passed_before_safety": len(passed or []),
            "public_safe": len(safe),
            "shadow_blocked": len(blocked),
            "blocked_reasons": dict(Counter(x for _, reasons in blocked for x in reasons)),
        }
        if _truthy(os.getenv("AUTONOMOUS_ACCUMULATION_LEDGER_ENABLED"), True):
            safe_ids = {_candidate_id(c) for c in safe}
            blocked_map = {_candidate_id(c): r for c, r in blocked}
            _write_candidate_rows(
                _candidate_row(
                    c,
                    self.settings,
                    run_id,
                    "post_quality",
                    "public_quality_safe" if _candidate_id(c) in safe_ids else "shadow_autonomous_safety" if _candidate_id(c) in blocked_map else "rejected_or_shadow_quality",
                    blocked_map.get(_candidate_id(c), []),
                )
                for c in candidates
            )
            _update_latest(
                run_id=run_id,
                stage="post_quality",
                candidates_evaluated=len(candidates),
                quality_passed_before_safety=len(passed or []),
                public_safe=len(safe),
                shadow_blocked=len(blocked),
            )
        return safe, dict(counter), debug

    wrapped._harizon_autonomous_accumulation = True
    PredictionQualityService.apply_to_candidates = wrapped
    return {"status": "wrapped"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if not _truthy(os.getenv("HARIZON_AUTONOMOUS_ACCUMULATION_MODE"), True):
        return {"status": "disabled_by_env"}
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    result = {
        "status": "installed",
        "candidate_factory": _patch_factory(),
        "quality_service": _patch_quality(),
        "artifacts": {
            "coverage": str(COVERAGE),
            "coverage_ledger": str(COVERAGE_LEDGER),
            "prediction_ledger": str(PREDICTION_LEDGER),
        },
    }
    _update_latest(install=result)
    return result
