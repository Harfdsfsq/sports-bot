from __future__ import annotations

"""Late CandidateFactory offer bridge for Bzzoiro/SStats mined odds hints.

The signal stack already mines Bzzoiro v2 odds into `provider_odds_hints`, but it
runs inside a stack of other CandidateFactory wrappers.  Some later guards and
reports can therefore still see the original `offers_by_match`, where every
current line is `odds_api_io` only.  That creates the observed state:

* Bzzoiro secondary offers are counted in signal-stack diagnostics;
* CandidateFactory/debug still has zero raw candidates and/or only odds_api_io
  buckets at the decisive stage.

This bridge is installed last.  It materializes provider odds hints into real
`Offer` rows *before* every inner factory wrapper runs, then writes a compact
merged-bucket diagnostic.  It does not lower thresholds, create synthetic prices,
or change publication policy.
"""

import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Match, MatchContext, Offer

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-exact-offer-bridge.json"

_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).strip().replace(",", "."))
        if math.isfinite(number):
            return number
    except Exception:
        return None
    return None


def _iter_contexts(value: Any):
    if isinstance(value, MatchContext):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_contexts(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_contexts(item)


def _clean_family(value: Any) -> str:
    raw = str(value or "").strip()
    mapping = {
        "total": "totals",
        "totals": "totals",
        "over_under": "totals",
        "goals_over_under": "totals",
        "spread": "spreads",
        "spreads": "spreads",
        "handicap": "spreads",
        "h2h": "h2h",
        "1x2": "h2h",
        "match_winner": "h2h",
        "btts": "btts",
    }
    return mapping.get(raw.lower(), raw)


def _clean_selection(value: Any, family: str, match: Match | None) -> tuple[str, str | None]:
    raw = str(value or "").strip()
    low = raw.lower()
    if family == "totals":
        if low.startswith("over") or "больше" in low:
            return "Over", None
        if low.startswith("under") or "меньше" in low:
            return "Under", None
    if family == "btts":
        if low in {"yes", "да", "btts yes", "both teams to score yes"} or low.endswith(" yes"):
            return "Yes", None
        if low in {"no", "нет", "btts no", "both teams to score no"} or low.endswith(" no"):
            return "No", None
    if family == "h2h" and match is not None:
        if low in {"home", "1"}:
            return match.home_team, "home"
        if low in {"away", "2"}:
            return match.away_team, "away"
        if low in {"draw", "x"}:
            return "draw", None
    return raw, None


def _offer_identity(offer: Offer) -> tuple[Any, ...]:
    return (
        _norm(getattr(offer, "source", "")),
        _norm(getattr(offer, "bookmaker", "")),
        _norm(getattr(offer, "family", "")),
        _norm(getattr(offer, "selection", "")),
        None if getattr(offer, "point", None) is None else round(float(getattr(offer, "point")), 3),
        _norm(getattr(offer, "team_side", "")),
        round(float(getattr(offer, "price", 0.0) or 0.0), 4),
    )


def _offer_from_hint(hint: dict[str, Any], match: Match | None) -> Offer | None:
    price = _to_float(hint.get("price"))
    if price is None or price < 1.01 or price > 50.0:
        return None
    family = _clean_family(hint.get("family") or hint.get("market_key") or hint.get("market"))
    if family not in {"totals", "spreads", "h2h", "btts"}:
        return None
    selection, inferred_side = _clean_selection(hint.get("selection") or hint.get("outcome") or hint.get("option_name"), family, match)
    if not selection:
        return None
    point = _to_float(hint.get("point") if hint.get("point") is not None else hint.get("line") if hint.get("line") is not None else hint.get("option_value"))
    team_side = str(hint.get("team_side") or inferred_side or "").strip().lower() or None
    source = str(hint.get("source") or "bzzoiro").strip().lower()
    if "bzzoiro" in source:
        source = "bzzoiro"
    elif "sstats" in source:
        source = "sstats"
    bookmaker = str(hint.get("bookmaker") or ("BzzoiroConsensus" if source == "bzzoiro" else "SStatsConsensus")).strip()
    if source == "bzzoiro" and bookmaker.lower() in {"bzzoiro", "bzzoiroconsensus", "consensus"}:
        bookmaker = "BzzoiroConsensus"
    if source == "sstats" and bookmaker.lower() in {"sstats", "sstatsconsensus", "consensus"}:
        bookmaker = "SStatsConsensus"
    return Offer(
        source=source,
        bookmaker=bookmaker,
        family=family,  # type: ignore[arg-type]
        selection=selection,
        price=round(float(price), 4),
        point=point,
        team_side=team_side,
        market_name=str(hint.get("market_name") or hint.get("market_key") or family),
        market_key=str(hint.get("market_key") or family),
        metadata={"exact_offer_bridge": True, "raw_hint": dict(hint)},
    )


def _maybe_enhance_context(context: MatchContext) -> None:
    # Reuse the signal-stack miners when available. This keeps all provider
    # parsing in one place and only adds a late bridge around the mined hints.
    try:
        from app.services.signal_stack_runtime_patch import _enhance_context  # type: ignore
        _enhance_context(context)
    except Exception:
        pass


def _merge_offers(matches: list[Match], offers_by_match: dict[str, list[Offer]], contexts_by_match: dict[str, Any]) -> tuple[dict[str, list[Offer]], dict[str, Any]]:
    match_by_key = {m.match_key: m for m in matches or []}
    merged: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
    stats: dict[str, Any] = {
        "matches_seen": len(matches or []),
        "input_matches_with_offers": len([1 for v in dict(offers_by_match or {}).values() if v]),
        "contexts_scanned": 0,
        "hints_seen": 0,
        "offers_added": 0,
        "offers_skipped_duplicate": 0,
        "offers_skipped_invalid": 0,
        "added_by_source": Counter(),
    }
    for match_key, ctx_value in dict(contexts_by_match or {}).items():
        key = str(match_key)
        match = match_by_key.get(key)
        for context in _iter_contexts(ctx_value):
            stats["contexts_scanned"] += 1
            _maybe_enhance_context(context)
            details = dict(getattr(context, "details", {}) or {})
            hints = list(details.get("provider_odds_hints") or [])
            stats["hints_seen"] += len(hints)
            if not hints:
                continue
            existing = {_offer_identity(offer) for offer in merged.get(key, [])}
            for hint in hints:
                if not isinstance(hint, dict):
                    stats["offers_skipped_invalid"] += 1
                    continue
                offer = _offer_from_hint(hint, match)
                if offer is None:
                    stats["offers_skipped_invalid"] += 1
                    continue
                ident = _offer_identity(offer)
                if ident in existing:
                    stats["offers_skipped_duplicate"] += 1
                    continue
                merged.setdefault(key, []).append(offer)
                existing.add(ident)
                stats["offers_added"] += 1
                stats["added_by_source"][_norm(offer.source)] += 1
    stats["output_matches_with_offers"] = len([1 for v in merged.values() if v])
    stats["added_by_source"] = dict(stats["added_by_source"].most_common())
    return merged, stats


def _bucket_key(offer: Offer) -> str:
    point = "" if getattr(offer, "point", None) is None else f"{float(getattr(offer, 'point')):.2f}"
    return "|".join([
        str(getattr(offer, "family", "") or ""),
        str(getattr(offer, "selection", "") or "").lower(),
        point,
        str(getattr(offer, "team_side", "") or "").lower(),
    ])


def _diagnose(matches: list[Match], merged: dict[str, list[Offer]], contexts_by_match: dict[str, Any], merge_stats: dict[str, Any]) -> dict[str, Any]:
    match_by_key = {m.match_key: m for m in matches or []}
    combos: Counter[str] = Counter()
    exact_2source_buckets = 0
    exact_allowed_2source_buckets = 0
    likely_candidate_buckets = 0
    samples: list[dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    for match_key, offers in merged.items():
        sources = sorted({_norm(o.source) for o in offers if _norm(o.source)})
        if sources:
            combos["+".join(sources)] += 1
        by_bucket: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            by_bucket[_bucket_key(offer)].append(offer)
        for key, bucket in by_bucket.items():
            b_sources = sorted({_norm(o.source) for o in bucket if _norm(o.source)})
            b_books = sorted({_norm(o.bookmaker) for o in bucket if _norm(o.bookmaker)})
            family = str(getattr(bucket[0], "family", "") or "") if bucket else ""
            if len(b_sources) >= 2:
                exact_2source_buckets += 1
                if family in {"totals", "spreads"}:
                    exact_allowed_2source_buckets += 1
            ctx_sources = []
            for ctx in _iter_contexts(dict(contexts_by_match or {}).get(match_key)):
                src = _norm(getattr(ctx, "source", ""))
                if src:
                    if "bzzoiro" in src:
                        src = "bzzoiro"
                    elif "sstats" in src:
                        src = "sstats"
                    ctx_sources.append(src)
            has_context = bool(ctx_sources)
            if family not in {"totals", "spreads"}:
                blockers["family_not_publishable"] += 1
            elif len(b_sources) < 2:
                blockers["exact_bucket_sources_below_2"] += 1
            elif len(b_books) < 2:
                blockers["exact_bucket_books_below_2"] += 1
            elif not has_context:
                blockers["missing_context"] += 1
            else:
                likely_candidate_buckets += 1
            if len(samples) < 80 and (len(b_sources) >= 2 or family in {"totals", "spreads"}):
                match = match_by_key.get(str(match_key))
                samples.append({
                    "match_key": match_key,
                    "home": getattr(match, "home_team", ""),
                    "away": getattr(match, "away_team", ""),
                    "kickoff": getattr(getattr(match, "commence_time", None), "isoformat", lambda: "")(),
                    "bucket": key,
                    "family": family,
                    "sources": b_sources,
                    "books": b_books,
                    "prices": [round(float(o.price), 4) for o in bucket[:10]],
                    "context_sources": sorted(set(ctx_sources)),
                })
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": True,
        "merge": merge_stats,
        "offer_source_combinations_after_bridge": dict(combos.most_common(30)),
        "exact_2source_buckets": exact_2source_buckets,
        "exact_allowed_2source_buckets": exact_allowed_2source_buckets,
        "likely_candidate_buckets_before_model": likely_candidate_buckets,
        "top_pre_model_blockers": dict(blockers.most_common(30)),
        "sample_buckets": samples,
    }


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    if not _truthy(os.getenv("BZZOIRO_EXACT_OFFER_BRIDGE_ENABLED"), True):
        return {"status": "disabled_by_env"}
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = CandidateFactory.build_candidates
    if getattr(current, "_harizon_bzzoiro_exact_offer_bridge", False):
        _INSTALLED = True
        return {"status": "already_wrapped"}

    original = current

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        try:
            for book in ("Bzzoiro", "BzzoiroConsensus", "SStats", "SStatsConsensus"):
                self.target_books.add(self._norm_book(book))
                self.consensus_books.add(self._norm_book(book))
        except Exception:
            pass
        merged, merge_stats = _merge_offers(list(matches or []), dict(offers_by_match or {}), dict(contexts_by_match or {}))
        diagnostic = _diagnose(list(matches or []), merged, dict(contexts_by_match or {}), merge_stats)
        _write(diagnostic)
        candidates, rejections, debug = original(self, matches, merged, contexts_by_match, market_signals_by_match=market_signals_by_match)
        try:
            debug = dict(debug or {})
            debug["bzzoiro_exact_offer_bridge"] = {
                "artifact": str(REPORT_PATH),
                "offers_added": merge_stats.get("offers_added", 0),
                "exact_allowed_2source_buckets": diagnostic.get("exact_allowed_2source_buckets", 0),
                "likely_candidate_buckets_before_model": diagnostic.get("likely_candidate_buckets_before_model", 0),
                "top_pre_model_blockers": diagnostic.get("top_pre_model_blockers", {}),
            }
        except Exception:
            pass
        return candidates, rejections, debug

    wrapped._harizon_bzzoiro_exact_offer_bridge = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = wrapped
    _INSTALLED = True
    _write({"created_at_utc": datetime.now(UTC).isoformat(), "status": "installed"})
    return {"status": "installed", "artifact": str(REPORT_PATH)}
