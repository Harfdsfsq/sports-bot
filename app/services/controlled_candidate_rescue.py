from __future__ import annotations

import os
import re
from collections import defaultdict
from statistics import mean
from typing import Any

from app.schemas import CandidateBet, Offer


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(str(raw)) if raw not in (None, "") else default
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def _inc(rejections: dict[str, int], key: str, by: int = 1) -> None:
    try:
        rejections[key] = int(rejections.get(key) or 0) + by
    except Exception:
        pass


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _norm_book(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _obj_float(obj: Any, *names: str, default: float = 0.0) -> float:
    for name in names:
        value = _to_float(getattr(obj, name, None), None)
        if value is not None:
            return float(value)
    diag = getattr(obj, "diagnostics", None)
    if isinstance(diag, dict):
        for name in names:
            value = _to_float(diag.get(name), None)
            if value is not None:
                return float(value)
    return default


def _obj_int(obj: Any, *names: str, default: int = 0) -> int:
    for name in names:
        value = _to_float(getattr(obj, name, None), None)
        if value is not None:
            return int(value)
    summary = getattr(obj, "source_summary", None)
    if isinstance(summary, dict):
        for name in names:
            value = _to_float(summary.get(name), None)
            if value is not None:
                return int(value)
    return default


def _best_by_book(offers: list[Offer]) -> dict[str, Offer]:
    by_book: dict[str, Offer] = {}
    for offer in offers:
        key = _norm_book(offer.bookmaker)
        if not key:
            continue
        prev = by_book.get(key)
        if prev is None or float(offer.price) > float(prev.price):
            by_book[key] = offer
    return by_book


def _paired_fair_probability(side_a: list[Offer], side_b: list[Offer]) -> tuple[float | None, int]:
    a_by_book = _best_by_book(side_a)
    b_by_book = _best_by_book(side_b)
    values: list[float] = []
    for book in sorted(set(a_by_book) & set(b_by_book)):
        a = float(a_by_book[book].price)
        b = float(b_by_book[book].price)
        if a <= 1.0 or b <= 1.0:
            continue
        pa = 1.0 / a
        pb = 1.0 / b
        denom = pa + pb
        if denom > 0:
            values.append(pa / denom)
    if not values:
        return None, 0
    return mean(values), len(values)


def _context_xg(context: Any) -> tuple[float | None, float | None]:
    if context is None:
        return None, None
    return getattr(context, "expected_home", None), getattr(context, "expected_away", None)


def _display_selection(family: str, selection: str, point: float | None) -> str:
    if family == "totals":
        side = "Больше" if str(selection).lower().startswith("over") else "Меньше"
        return f"{side} {point:g}" if point is not None else side
    if family == "btts":
        return "Обе забьют: Да" if str(selection).lower().startswith("y") else "Обе забьют: Нет"
    if family == "dnb":
        return f"Фора 0 — {selection}"
    return str(selection)


def _selection_key(family: str, selection: str, point: float | None, team_side: str | None = None) -> str:
    p = "" if point is None else f"{float(point):g}"
    return "|".join([family, str(selection).lower(), p, str(team_side or "").lower()])


def _raw_rows(bucket: list[Offer], limit: int = 16) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for offer in sorted(bucket, key=lambda row: float(row.price), reverse=True)[:limit]:
        out.append({
            "source": offer.source,
            "bookmaker": offer.bookmaker,
            "family": offer.family,
            "selection": offer.selection,
            "price": offer.price,
            "point": offer.point,
            "team_side": offer.team_side,
            "market_name": offer.market_name,
        })
    return out


def _make_candidate(
    *,
    match: Any,
    family: str,
    selection: str,
    point: float | None,
    bucket: list[Offer],
    opposite: list[Offer],
    consensus_prob: float,
    paired_books: int,
    context: Any,
    rejections: dict[str, int],
    team_side: str | None = None,
) -> CandidateBet | None:
    if paired_books < _env_int("CONTROLLED_RESCUE_MIN_PAIRED_BOOKS", 2):
        _inc(rejections, "controlled_rescue_books_guard")
        return None
    best = max(bucket, key=lambda item: float(item.price))
    odds = float(best.price)
    if odds < _env_float("CONTROLLED_RESCUE_MIN_ODDS", 1.45) or odds > _env_float("CONTROLLED_RESCUE_MAX_ODDS", 2.75):
        _inc(rejections, "controlled_rescue_odds_range_guard")
        return None
    implied = 1.0 / odds
    raw_edge = float(consensus_prob) - implied
    model_prob = _clamp(float(consensus_prob) + min(0.035, max(0.0, raw_edge) * 0.55 + 0.012), 0.02, 0.98)
    edge_pp = (model_prob - implied) * 100.0
    ev_pct = (model_prob * odds - 1.0) * 100.0
    if edge_pp < _env_float("CONTROLLED_RESCUE_MIN_EDGE_PP", 1.2):
        _inc(rejections, "controlled_rescue_edge_guard")
        return None
    if ev_pct < _env_float("CONTROLLED_RESCUE_MIN_EV_PCT", 2.5):
        _inc(rejections, "controlled_rescue_ev_guard")
        return None
    all_offers = bucket + opposite
    books = {_norm_book(item.bookmaker) for item in all_offers if _norm_book(item.bookmaker)}
    sources = {str(item.source or "").strip().lower() for item in all_offers if str(item.source or "").strip()}
    expected_home, expected_away = _context_xg(context)
    confidence = min(78.5, 64.0 + paired_books * 2.3 + min(7.5, max(0.0, edge_pp) * 1.15))
    return CandidateBet(
        match_key=match.match_key,
        sport_key=match.sport_key,
        league_name=match.league_name,
        home_team=match.home_team,
        away_team=match.away_team,
        commence_time=match.commence_time,
        family=family,  # type: ignore[arg-type]
        selection=_display_selection(family, selection, point),
        selection_key=_selection_key(family, selection, point, team_side),
        odds=odds,
        fair_odds=1.0 / max(model_prob, 0.01),
        implied_probability=implied,
        market_probability=float(consensus_prob),
        consensus_probability=float(consensus_prob),
        model_probability=model_prob,
        final_probability=model_prob,
        adjusted_probability=model_prob,
        edge_pct=edge_pp,
        ev_pct=ev_pct,
        confidence=confidence,
        books_count=len(books),
        sources_count=max(1, len(sources)),
        model_mode="controlled_consensus_rescue",
        point=point,
        expected_home=expected_home,
        expected_away=expected_away,
        reasons=[
            "mode=controlled_consensus_rescue",
            "model=bookmaker_consensus_plus_best_price_guarded",
            f"paired_books={paired_books}",
            f"books={len(books)}",
            f"consensus_prob={float(consensus_prob):.4f}",
            f"best_price={odds:.2f}",
            "context=optional_not_primary_signal",
        ],
        source_summary={
            "context_source": getattr(context, "source", None) if context is not None else None,
            "context_mode": "controlled_consensus_rescue",
            "selected_bookmaker": best.bookmaker,
            "selected_source": best.source,
            "confirmation_sources": [getattr(context, "source", "market") if context is not None else "market"],
            "market_signal_derived": True,
            "odds_books_count": len(books),
            "paired_books_count": paired_books,
        },
        bookmaker=best.bookmaker,
        diagnostics={"controlled_consensus_rescue": True, "raw_consensus_edge_pp": raw_edge * 100.0, "paired_books_count": paired_books},
        analysis={"controlled_consensus_rescue": True},
        publication_score=confidence + min(12.0, max(0.0, ev_pct)) + min(5.0, paired_books),
        source_event_id=best.source_event_id,
        team_side=team_side,
        raw_bucket_offers=_raw_rows(bucket),
    )


def _build_rescue(factory: Any, matches: list[Any], offers_by_match: dict[str, list[Offer]], contexts_by_match: dict[str, Any], rejections: dict[str, int]) -> tuple[list[CandidateBet], list[dict[str, Any]]]:
    matches_by_key = {match.match_key: match for match in matches}
    allowed = {item.strip() for item in os.getenv("CONTROLLED_RESCUE_ALLOWED_FAMILIES", "totals,dnb,btts").split(",") if item.strip()}
    max_per_match = _env_int("CONTROLLED_RESCUE_MAX_PER_MATCH", 2)
    max_total = _env_int("CONTROLLED_RESCUE_MAX_TOTAL", 40)
    out: list[CandidateBet] = []
    debug: list[dict[str, Any]] = []
    for match_key, offers in offers_by_match.items():
        if len(out) >= max_total:
            break
        match = matches_by_key.get(match_key)
        if match is None:
            continue
        try:
            context = factory._coerce_context(contexts_by_match.get(match_key))
        except Exception:
            context = contexts_by_match.get(match_key)
        families: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            if str(offer.family) not in allowed:
                continue
            try:
                if not factory._is_target_or_consensus_book(offer.bookmaker):
                    continue
            except Exception:
                pass
            families[str(offer.family)].append(offer)
        current: list[CandidateBet] = []
        if "totals" in allowed and families.get("totals"):
            grouped: dict[tuple[float | None, str], list[Offer]] = defaultdict(list)
            for offer in families["totals"]:
                grouped[(offer.point, str(offer.selection or ""))].append(offer)
            for point in sorted([p for p, _ in grouped if p is not None]):
                over = grouped.get((point, "Over"), [])
                under = grouped.get((point, "Under"), [])
                if not over or not under:
                    continue
                p_over, paired = _paired_fair_probability(over, under)
                if p_over is None:
                    continue
                current.extend([item for item in (
                    _make_candidate(match=match, family="totals", selection="Over", point=float(point), bucket=over, opposite=under, consensus_prob=p_over, paired_books=paired, context=context, rejections=rejections),
                    _make_candidate(match=match, family="totals", selection="Under", point=float(point), bucket=under, opposite=over, consensus_prob=1.0 - p_over, paired_books=paired, context=context, rejections=rejections),
                ) if item is not None])
        if "btts" in allowed and families.get("btts"):
            yes = [x for x in families["btts"] if str(x.selection).lower().startswith("y")]
            no = [x for x in families["btts"] if str(x.selection).lower().startswith("n")]
            if yes and no:
                p_yes, paired = _paired_fair_probability(yes, no)
                if p_yes is not None:
                    current.extend([item for item in (
                        _make_candidate(match=match, family="btts", selection="Yes", point=None, bucket=yes, opposite=no, consensus_prob=p_yes, paired_books=paired, context=context, rejections=rejections),
                        _make_candidate(match=match, family="btts", selection="No", point=None, bucket=no, opposite=yes, consensus_prob=1.0 - p_yes, paired_books=paired, context=context, rejections=rejections),
                    ) if item is not None])
        if "dnb" in allowed and families.get("dnb"):
            home = [x for x in families["dnb"] if str(x.team_side or "").lower() == "home"]
            away = [x for x in families["dnb"] if str(x.team_side or "").lower() == "away"]
            if home and away:
                p_home, paired = _paired_fair_probability(home, away)
                if p_home is not None:
                    current.extend([item for item in (
                        _make_candidate(match=match, family="dnb", selection=match.home_team, point=0.0, bucket=home, opposite=away, consensus_prob=p_home, paired_books=paired, context=context, rejections=rejections, team_side="home"),
                        _make_candidate(match=match, family="dnb", selection=match.away_team, point=0.0, bucket=away, opposite=home, consensus_prob=1.0 - p_home, paired_books=paired, context=context, rejections=rejections, team_side="away"),
                    ) if item is not None])
        current.sort(key=lambda item: (float(item.publication_score), float(item.ev_pct), float(item.edge_pct)), reverse=True)
        for candidate in current[:max_per_match]:
            out.append(candidate)
            debug.append({"match_key": match_key, "selection": candidate.selection, "family": candidate.family, "point": candidate.point, "model_mode": candidate.model_mode, "market_probability": round(float(candidate.market_probability), 4), "adjusted_probability": round(float(candidate.adjusted_probability), 4), "confidence": round(float(candidate.confidence), 2), "publication_score": round(float(candidate.publication_score), 3)})
            if len(out) >= max_total:
                break
    return out, debug


def _controlled_prefilter_allowed(candidate: CandidateBet, rejections: dict[str, int]) -> bool:
    allowed = {item.strip().lower() for item in os.getenv("CONTROLLED_PREFILTER_RESCUE_ALLOWED_FAMILIES", "totals,dnb,btts").split(",") if item.strip()}
    family = str(getattr(candidate, "family", "") or "").lower()
    if family not in allowed:
        _inc(rejections, "controlled_prefilter_family_guard")
        return False
    odds = _obj_float(candidate, "odds", default=0.0)
    if odds < _env_float("CONTROLLED_PREFILTER_MIN_ODDS", 1.35) or odds > _env_float("CONTROLLED_PREFILTER_MAX_ODDS", 3.4):
        _inc(rejections, "controlled_prefilter_odds_guard")
        return False
    books = _obj_int(candidate, "books_count", "odds_books_count", default=0)
    if books < _env_int("CONTROLLED_PREFILTER_MIN_BOOKS", 1):
        _inc(rejections, "controlled_prefilter_books_guard")
        return False
    confidence = _obj_float(candidate, "confidence", default=0.0)
    if confidence < _env_float("CONTROLLED_PREFILTER_MIN_CONFIDENCE", 50.0):
        _inc(rejections, "controlled_prefilter_confidence_guard")
        return False
    edge = _obj_float(candidate, "edge_pct", "edge_pp", default=0.0)
    ev = _obj_float(candidate, "ev_pct", default=0.0)
    if edge < _env_float("CONTROLLED_PREFILTER_MIN_EDGE_PP", 0.0):
        _inc(rejections, "controlled_prefilter_edge_guard")
        return False
    if ev < _env_float("CONTROLLED_PREFILTER_MIN_EV_PCT", 0.0):
        _inc(rejections, "controlled_prefilter_ev_guard")
        return False
    selection_text = f"{getattr(candidate, 'selection', '')} {getattr(candidate, 'selection_key', '')}".lower()
    point = _to_float(getattr(candidate, "point", None), None)
    if family == "totals" and point is not None and point <= 1.5 and ("over" in selection_text or "больше" in selection_text or "тб" in selection_text) and odds > _env_float("MATCH_TOTAL_OVER15_ABSOLUTE_MAX_ODDS", 1.85):
        _inc(rejections, "controlled_prefilter_low_total_price_guard")
        return False
    return True


def _patch_filter_and_rank(cls: type[Any]) -> bool:
    if getattr(cls, "_harizon_controlled_prefilter_patch", False):
        return False
    original = getattr(cls, "_filter_and_rank", None)
    if not callable(original):
        return False

    def filter_and_rank_patched(self: Any, candidates: list[CandidateBet], rejections: dict[str, int]):
        filtered = original(self, candidates, rejections)
        if filtered or not _env_bool("CONTROLLED_PREFILTER_RESCUE_ENABLED", True) or not candidates:
            return filtered
        rescue = [c for c in candidates if _controlled_prefilter_allowed(c, rejections)]
        if not rescue:
            _inc(rejections, "controlled_prefilter_rescue_empty")
            return filtered
        rescue.sort(key=lambda c: (_obj_float(c, "publication_score", default=0.0), _obj_float(c, "ev_pct", default=0.0), _obj_float(c, "confidence", default=0.0)), reverse=True)
        limit = _env_int("CONTROLLED_PREFILTER_RETURN_LIMIT", 24)
        for c in rescue[:limit]:
            reasons = list(getattr(c, "reasons", []) or [])
            reasons.append("controlled_prefilter_rescue:passed_to_quality_and_fallback_guards")
            try:
                c.reasons = reasons
                diag = getattr(c, "diagnostics", None)
                if isinstance(diag, dict):
                    diag["controlled_prefilter_rescue"] = True
                summary = getattr(c, "source_summary", None)
                if isinstance(summary, dict):
                    summary["controlled_prefilter_rescue"] = True
            except Exception:
                pass
        _inc(rejections, "controlled_prefilter_rescue_candidates_built", len(rescue[:limit]))
        return rescue[:limit]

    cls._filter_and_rank = filter_and_rank_patched
    cls._harizon_controlled_prefilter_patch = True
    return True


def install() -> dict[str, Any]:
    try:
        from app.services import model
    except Exception as exc:
        return {"status": "skipped", "reason": f"model_import_failed:{exc}"}
    cls = getattr(model, "CandidateFactory", None)
    if cls is None:
        return {"status": "skipped", "reason": "candidate_factory_missing"}
    filter_patch = _patch_filter_and_rank(cls)
    if getattr(cls, "_harizon_controlled_rescue_patch", False):
        return {"status": "already_installed", "filter_patch": filter_patch}
    original = getattr(cls, "build_candidates", None)
    if not callable(original):
        return {"status": "skipped", "reason": "build_candidates_missing", "filter_patch": filter_patch}

    def build_candidates_patched(self: Any, matches: list[Any], offers_by_match: dict[str, list[Offer]], contexts_by_match: dict[str, Any], market_signals_by_match: dict[str, dict[str, Any]] | None = None):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        if not _env_bool("CONTROLLED_CONSENSUS_CANDIDATE_RESCUE_ENABLED", True) or not offers_by_match:
            return candidates, rejections, debug
        if not isinstance(rejections, dict):
            rejections = {}
        rescue_candidates, rescue_debug = _build_rescue(self, matches, offers_by_match, contexts_by_match, rejections)
        if not rescue_candidates:
            _inc(rejections, "controlled_rescue_no_candidate")
            return candidates, rejections, debug
        rescue_candidates.sort(key=lambda item: (float(item.publication_score), float(item.ev_pct), float(item.confidence)), reverse=True)
        debug = dict(debug or {})
        debug["matches"] = (list(debug.get("matches") or []) + rescue_debug)[:200]
        limit = _env_int("CONTROLLED_RESCUE_RETURN_LIMIT", 24)
        returned = rescue_candidates[:limit]
        if _env_bool("CONTROLLED_RESCUE_APPEND_TO_EXISTING_CANDIDATES", True) and candidates:
            seen = {(c.match_key, c.family, c.selection_key, c.point, c.team_side) for c in candidates}
            merged = list(candidates)
            appended = 0
            for item in returned:
                key = (item.match_key, item.family, item.selection_key, item.point, item.team_side)
                if key in seen:
                    continue
                seen.add(key)
                item.reasons.append("controlled_rescue_append:main_pool_not_empty")
                item.source_summary["controlled_rescue_append"] = True
                merged.append(item)
                appended += 1
            debug["controlled_consensus_rescue"] = {"enabled": True, "mode": "append", "built": len(rescue_candidates), "returned": len(returned), "appended": appended, "input_candidates": len(candidates), "output_candidates": len(merged)}
            _inc(rejections, "controlled_rescue_candidates_built", len(rescue_candidates))
            _inc(rejections, "controlled_rescue_candidates_appended", appended)
            try:
                return self._filter_and_rank(merged, rejections), rejections, debug
            except Exception:
                return merged, rejections, debug
        debug["controlled_consensus_rescue"] = {"enabled": True, "mode": "replace_empty", "built": len(rescue_candidates), "returned": len(returned)}
        _inc(rejections, "controlled_rescue_candidates_built", len(rescue_candidates))
        return returned, rejections, debug

    cls.build_candidates = build_candidates_patched
    cls._harizon_controlled_rescue_patch = True
    return {"status": "installed", "version": "controlled-consensus-rescue-v3-append", "filter_patch": filter_patch}
