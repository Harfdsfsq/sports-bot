from __future__ import annotations

"""Candidate factory diagnostics without relaxing guards.

When a run has lines and contexts but ``raw candidates = 0``, the normal report
has too little evidence: quality rejects are empty because quality never saw a
candidate.  This wrapper audits the exact input buckets sent to
CandidateFactory.build_candidates and writes a compact artifact explaining where
candidate construction stops: family availability, source/book counts, context
shape, market-signal readiness and post-build candidate counts.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import MatchContext, Offer

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT_PATH = EXPORT_DIR / "latest-candidate-factory-diagnostics.json"

_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _context_list(value: Any) -> list[MatchContext]:
    if isinstance(value, MatchContext):
        return [value]
    if isinstance(value, dict):
        out: list[MatchContext] = []
        for item in value.values():
            out.extend(_context_list(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_context_list(item))
        return out
    return []


def _context_sources(value: Any) -> set[str]:
    sources: set[str] = set()
    for ctx in _context_list(value):
        src = _norm(getattr(ctx, "source", ""))
        if src:
            if "bzzoiro" in src:
                sources.add("bzzoiro")
            elif "sstats" in src:
                sources.add("sstats")
            elif "sportlogic" in src:
                sources.add("sportlogic")
            else:
                sources.add(src)
        details = getattr(ctx, "details", {}) or {}
        if isinstance(details, dict):
            for token in details.get("source_tokens") or []:
                token_s = _norm(token)
                if token_s:
                    sources.add(token_s)
            if details.get("bzzoiro_context_gap_pass") or details.get("bzzoiro_v2"):
                sources.add("bzzoiro")
            if details.get("sstats_deep_endpoints") or details.get("sstats_deep_endpoint_count"):
                sources.add("sstats")
    return sources


def _context_shape(value: Any) -> dict[str, Any]:
    contexts = _context_list(value)
    if not contexts:
        return {
            "present": False,
            "sources": [],
            "has_expected_goals": False,
            "has_probabilities": False,
            "confidence_max": 0.0,
        }
    has_xg = any(getattr(ctx, "expected_home", None) is not None and getattr(ctx, "expected_away", None) is not None for ctx in contexts)
    has_probs = any(getattr(ctx, "home_win_probability", None) is not None or getattr(ctx, "away_win_probability", None) is not None for ctx in contexts)
    max_conf = max(float(getattr(ctx, "confidence", 0.0) or 0.0) for ctx in contexts)
    hints = 0
    metric_hints = 0
    for ctx in contexts:
        details = getattr(ctx, "details", {}) or {}
        if isinstance(details, dict):
            hints += int(details.get("provider_odds_hints_count") or details.get("bzzoiro_odds_hint_count") or 0)
            metric_hints += int(details.get("provider_metric_hints_count") or 0)
    return {
        "present": True,
        "count": len(contexts),
        "sources": sorted(_context_sources(value)),
        "has_expected_goals": has_xg,
        "has_probabilities": has_probs,
        "confidence_max": round(max_conf, 2),
        "provider_odds_hints": hints,
        "provider_metric_hints": metric_hints,
    }


def _offer_stats(offers: list[Offer]) -> dict[str, Any]:
    by_family: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers or []:
        by_family[str(getattr(offer, "family", "") or "unknown")].append(offer)
    families: dict[str, Any] = {}
    for family, rows in sorted(by_family.items()):
        sources = {_norm(getattr(o, "source", "")) for o in rows if _norm(getattr(o, "source", ""))}
        books = {_norm(getattr(o, "bookmaker", "")) for o in rows if _norm(getattr(o, "bookmaker", ""))}
        selections = Counter(str(getattr(o, "selection", "") or "") for o in rows)
        points = Counter("" if getattr(o, "point", None) is None else str(getattr(o, "point")) for o in rows)
        source_by_bucket: dict[str, set[str]] = defaultdict(set)
        book_by_bucket: dict[str, set[str]] = defaultdict(set)
        for offer in rows:
            key = f"{getattr(offer, 'selection', '')}|{getattr(offer, 'point', None)}|{getattr(offer, 'team_side', '')}"
            src = _norm(getattr(offer, "source", ""))
            book = _norm(getattr(offer, "bookmaker", ""))
            if src:
                source_by_bucket[key].add(src)
            if book:
                book_by_bucket[key].add(book)
        max_bucket_sources = max((len(v) for v in source_by_bucket.values()), default=0)
        max_bucket_books = max((len(v) for v in book_by_bucket.values()), default=0)
        families[family] = {
            "offers": len(rows),
            "sources": sorted(sources),
            "books": sorted(books),
            "source_count": len(sources),
            "book_count": len(books),
            "max_bucket_sources": max_bucket_sources,
            "max_bucket_books": max_bucket_books,
            "top_selections": selections.most_common(8),
            "top_points": points.most_common(8),
        }
    return {
        "offers": len(offers or []),
        "sources": sorted({_norm(getattr(o, "source", "")) for o in offers or [] if _norm(getattr(o, "source", ""))}),
        "books": sorted({_norm(getattr(o, "bookmaker", "")) for o in offers or [] if _norm(getattr(o, "bookmaker", ""))}),
        "families": families,
    }


def _market_signal_shape(signal: Any) -> dict[str, Any]:
    if not isinstance(signal, dict):
        return {"present": False}
    return {
        "present": True,
        "keys": sorted(str(k) for k in signal.keys())[:20],
        "history_ready_any": bool(signal.get("history_ready") or any(isinstance(v, dict) and v.get("history_ready") for v in signal.values())),
        "observation_count": int(signal.get("observation_count") or 0) if str(signal.get("observation_count") or "").replace(".", "", 1).isdigit() else 0,
    }


def _likely_blockers(match_diag: dict[str, Any], settings: Any) -> list[str]:
    blockers: list[str] = []
    offers = match_diag.get("offers") or {}
    ctx = match_diag.get("context") or {}
    families = offers.get("families") or {}
    min_sources = int(getattr(settings, "min_sources_publish", 2) or 2)
    target_families = {"totals", "spreads"}
    if not families:
        blockers.append("no_offers_for_match")
    if not any(name in families for name in target_families):
        blockers.append("no_allowed_family_totals_or_spreads")
    for name in sorted(target_families & set(families.keys())):
        fam = families[name]
        if int(fam.get("max_bucket_sources") or 0) < min_sources:
            blockers.append(f"{name}:bucket_sources_below_{min_sources}")
        if int(fam.get("max_bucket_books") or 0) < 2:
            blockers.append(f"{name}:bucket_books_below_2")
    if not ctx.get("present"):
        blockers.append("missing_context")
    elif not ctx.get("has_expected_goals"):
        blockers.append("context_missing_expected_goals")
    if not match_diag.get("market_signal", {}).get("present"):
        blockers.append("missing_market_signal")
    return blockers[:12]


def _candidate_counts(candidates: list[Any]) -> dict[str, Any]:
    by_family = Counter(str(getattr(c, "family", "") or "unknown") for c in candidates or [])
    by_mode = Counter(str(getattr(c, "model_mode", "") or "unknown") for c in candidates or [])
    return {
        "total": len(candidates or []),
        "by_family": dict(by_family.most_common()),
        "by_mode": dict(by_mode.most_common()),
    }


def _build_report(self: Any, matches: list[Any], offers_by_match: dict[str, list[Offer]], contexts_by_match: dict[str, Any], market_signals_by_match: dict[str, Any] | None, candidates: list[Any], rejections: dict[str, int], debug: dict[str, Any]) -> dict[str, Any]:
    match_by_key = {str(getattr(m, "match_key", "") or ""): m for m in matches or []}
    per_match: list[dict[str, Any]] = []
    blocker_counter: Counter[str] = Counter()
    offer_source_combos: Counter[str] = Counter()
    context_source_combos: Counter[str] = Counter()
    candidate_keys = {str(getattr(c, "match_key", "") or "") for c in candidates or []}

    for match_key, offers in dict(offers_by_match or {}).items():
        key = str(match_key)
        offer_diag = _offer_stats(list(offers or []))
        ctx_diag = _context_shape(dict(contexts_by_match or {}).get(key))
        market_diag = _market_signal_shape((market_signals_by_match or {}).get(key))
        diag = {
            "match_key": key,
            "home_team": getattr(match_by_key.get(key), "home_team", ""),
            "away_team": getattr(match_by_key.get(key), "away_team", ""),
            "league_name": getattr(match_by_key.get(key), "league_name", ""),
            "kickoff_utc": getattr(getattr(match_by_key.get(key), "commence_time", None), "isoformat", lambda: "")(),
            "offers": offer_diag,
            "context": ctx_diag,
            "market_signal": market_diag,
            "candidate_built": key in candidate_keys,
        }
        blockers = _likely_blockers(diag, getattr(self, "settings", None))
        diag["likely_blockers"] = blockers
        for reason in blockers:
            blocker_counter[reason] += 1
        if offer_diag.get("sources"):
            offer_source_combos["+".join(offer_diag["sources"])] += 1
        if ctx_diag.get("sources"):
            context_source_combos["+".join(ctx_diag["sources"])] += 1
        if len(per_match) < 120:
            per_match.append(diag)

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": True,
        "matches_seen": len(matches or []),
        "matches_with_offers": len([1 for v in dict(offers_by_match or {}).values() if v]),
        "matches_with_context": len([1 for v in dict(contexts_by_match or {}).values() if v]),
        "raw_candidates": len(candidates or []),
        "candidate_counts": _candidate_counts(candidates),
        "rejections": dict(sorted((rejections or {}).items(), key=lambda item: (-int(item[1]), item[0]))[:50]),
        "top_likely_blockers": dict(blocker_counter.most_common(30)),
        "offer_source_combinations": dict(offer_source_combos.most_common(30)),
        "context_source_combinations": dict(context_source_combos.most_common(30)),
        "sample_matches": per_match,
        "debug_rows_in_factory": len((debug or {}).get("matches") or []),
    }
    return payload


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    if str(os.getenv("CANDIDATE_FACTORY_DIAGNOSTICS_ENABLED") or "true").strip().lower() in {"0", "false", "off", "no"}:
        return {"status": "disabled_by_env"}
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = CandidateFactory.build_candidates
    if getattr(current, "_harizon_candidate_factory_diagnostics", False):
        _INSTALLED = True
        return {"status": "already_wrapped"}

    original = current

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=market_signals_by_match)
        try:
            payload = _build_report(self, list(matches or []), dict(offers_by_match or {}), dict(contexts_by_match or {}), market_signals_by_match or {}, list(candidates or []), dict(rejections or {}), dict(debug or {}))
            _write(payload)
            debug = dict(debug or {})
            debug["candidate_factory_diagnostics"] = {
                "artifact": str(REPORT_PATH),
                "top_likely_blockers": payload.get("top_likely_blockers", {}),
                "offer_source_combinations": payload.get("offer_source_combinations", {}),
                "context_source_combinations": payload.get("context_source_combinations", {}),
            }
        except Exception as exc:
            debug = dict(debug or {})
            debug["candidate_factory_diagnostics"] = {"error": f"{type(exc).__name__}: {exc}"}
        return candidates, rejections, debug

    wrapped._harizon_candidate_factory_diagnostics = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = wrapped
    _INSTALLED = True
    _write({"created_at_utc": datetime.now(UTC).isoformat(), "status": "installed"})
    return {"status": "installed", "artifact": str(REPORT_PATH)}
