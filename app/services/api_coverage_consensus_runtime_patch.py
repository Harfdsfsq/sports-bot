from __future__ import annotations

"""HARIZON API coverage + consensus price runtime layer.

This layer is deliberately conservative. It does not weaken model/quality
thresholds; it makes candidates less likely to pass when the exact market line is
covered by only one odds source, by one bookmaker, or by inconsistent prices.

Goals:
- every publishable candidate must carry 2+ independent exact odds sources;
- every publishable candidate must carry 2+ independent context sources;
- selected odds are rebased to an exact-line consensus price, not to a stray high
  quote from one provider/bookmaker;
- suspicious price dispersion is rejected before quality/fallback publication.
"""

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-api-coverage-consensus-runtime-patch.json"
_INSTALLED = False

PATCH_MARKER = "_harizon_api_coverage_consensus_v1"

PRICE_SOURCE_ALIASES = {
    "odds_api_io": "odds_api_io",
    "odds_api": "odds_api_io",
    "oddsapiio": "odds_api_io",
    "odds-api.io": "odds_api_io",
    "bzzoiro": "bzzoiro",
    "bzzoiro_odds": "bzzoiro",
    "bzzoiro_event_odds": "bzzoiro",
    "allsportsapi": "allsportsapi",
    "all_sports_api": "allsportsapi",
    "sportlogic": "sportlogic",
    "oddsfeed": "oddsfeed",
    "odds_feed": "oddsfeed",
    "sportsbook_api": "sportsbook_api",
    "rapidapi_odds_bridge": "rapidapi_odds_bridge",
    "highlightly": "highlightly",
}

CONTEXT_SOURCE_ALIASES = {
    "sstats": "sstats",
    "soccerstats": "sstats",
    "bzzoiro": "bzzoiro",
    "api_football": "api_football",
    "api-football": "api_football",
    "football_data": "football_data",
    "football-data": "football_data",
    "football_data_org": "football_data",
    "thesportsdb": "thesportsdb",
    "sportsdb": "thesportsdb",
    "clubelo": "clubelo",
    "sportlogic": "sportlogic",
    "futrixmetrics": "futrixmetrics",
    "self_history": "self_history",
    "weather": "weather",
    "open_meteo": "weather",
    "open-meteo": "weather",
    "weatherapi": "weather",
    "news": "news",
    "newsapi": "news",
    "gnews": "news",
    "currents": "news",
    "newsdata": "news",
    "guardian": "news",
}

CONTEXT_ONLY_SOURCES = {
    "sstats",
    "api_football",
    "football_data",
    "football_data_org",
    "thesportsdb",
    "clubelo",
    "weather",
    "news",
    "newsapi",
    "gnews",
    "currents",
    "newsdata",
    "guardian",
    "futrixmetrics",
    "self_history",
}

NON_FULL_TIME_MARKET_RE = re.compile(
    r"\b("
    r"ht|1h|2h|1st\s*half|2nd\s*half|first\s*half|second\s*half|half\s*time|"
    r"corners?|cards?|bookings?|offsides?|throw\s*ins?|shots?|saves?|player|"
    r"penalt(?:y|ies)|free\s*kicks?|goal\s*kicks?|period|quarter|set|map|"
    r"перв(?:ый|ом)\s*тайм|втор(?:ой|ом)\s*тайм|тайм|углов|карточ"
    r")\b",
    re.IGNORECASE,
)


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        number = float(str(value).replace(",", "."))
        if math.isfinite(number):
            return number
    except Exception:
        return default
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(".", "_")
    text = re.sub(r"[^a-z0-9а-я_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _canonical_price_source(value: Any) -> str | None:
    key = _norm(value)
    if not key:
        return None
    if key in CONTEXT_ONLY_SOURCES:
        return None
    if key in PRICE_SOURCE_ALIASES:
        return PRICE_SOURCE_ALIASES[key]
    for needle, canonical in PRICE_SOURCE_ALIASES.items():
        if needle and needle in key:
            return canonical
    return key


def _canonical_context_source(value: Any) -> str | None:
    key = _norm(value)
    if not key or key == "ensemble":
        return None
    if key in CONTEXT_SOURCE_ALIASES:
        return CONTEXT_SOURCE_ALIASES[key]
    for needle, canonical in CONTEXT_SOURCE_ALIASES.items():
        if needle and needle in key:
            return canonical
    return key


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _offer_rows(candidate: Any) -> list[Any]:
    raw = getattr(candidate, "raw_bucket_offers", None)
    if isinstance(raw, list) and raw:
        return raw
    for container_name in ("source_summary", "diagnostics", "analysis"):
        container = getattr(candidate, container_name, None)
        if isinstance(container, dict):
            for key in ("raw_bucket_offers", "bucket_offers", "selected_offers", "offers"):
                value = container.get(key)
                if isinstance(value, list) and value:
                    return value
    return []


def _line_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("over", "больше", "тб")):
        return "over"
    if any(token in text for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in text for token in ("yes", "да", "btts_yes")):
        return "yes"
    if any(token in text for token in ("no", "нет", "btts_no")):
        return "no"
    if any(token in text for token in ("home", "1", "хозя", " хозя")):
        return "home"
    if any(token in text for token in ("away", "2", "гост", " гост")):
        return "away"
    if "draw" in text or "x" == text.strip():
        return "draw"
    return _norm(text)


def _candidate_side(candidate: Any) -> str:
    parts = [
        getattr(candidate, "selection", ""),
        getattr(candidate, "selection_key", ""),
        getattr(candidate, "team_side", ""),
    ]
    return _line_side(" ".join(str(p or "") for p in parts))


def _offer_text(offer: Any) -> str:
    fields = (
        "selection",
        "selection_key",
        "team_side",
        "name",
        "label",
        "market_name",
        "market_key",
        "market_subtype",
    )
    return " ".join(str(_field(offer, key) or "") for key in fields).strip()


def _is_full_time_market_name(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return NON_FULL_TIME_MARKET_RE.search(text) is None


def _same_family(candidate_family: str, offer_family: str) -> bool:
    if not candidate_family or not offer_family:
        return True
    if candidate_family == offer_family:
        return True
    if candidate_family == "totals" and ("total" in offer_family or "goals" in offer_family):
        return True
    if candidate_family == "spreads" and any(token in offer_family for token in ("spread", "handicap", "asian")):
        return True
    if candidate_family == "btts" and any(token in offer_family for token in ("btts", "both_teams")):
        return True
    if candidate_family == "h2h" and any(token in offer_family for token in ("h2h", "winner", "match_winner", "1x2")):
        return True
    return False


def _same_exact_line(candidate: Any, offer: Any) -> bool:
    cand_family = _norm(getattr(candidate, "family", ""))
    offer_family = _norm(_field(offer, "family", "market_key", "market_name", "market_subtype"))
    if not _same_family(cand_family, offer_family):
        return False
    if not _is_full_time_market_name(_field(offer, "market_name", "market_key", "market_subtype")):
        return False

    cand_point = getattr(candidate, "point", None)
    offer_point = _field(offer, "point", "line", "handicap", "hdp", "total")
    if cand_point not in (None, "") or offer_point not in (None, ""):
        cp = _as_float(cand_point, None)
        op = _as_float(offer_point, None)
        if cp is None or op is None or abs(cp - op) > 0.001:
            return False

    side = _candidate_side(candidate)
    text = _offer_text(offer).lower()
    if side == "over" and not any(token in text for token in ("over", "больше", "тб")):
        return False
    if side == "under" and not any(token in text for token in ("under", "меньше", "тм")):
        return False
    if side == "yes" and not any(token in text for token in ("yes", "да", "btts_yes")):
        return False
    if side == "no" and not any(token in text for token in ("no", "нет", "btts_no")):
        return False
    if cand_family == "spreads" and side in {"home", "away"} and side not in text:
        pass
    return True


def _median(values: list[float]) -> float | None:
    clean = [v for v in values if v and v > 1.0 and math.isfinite(v)]
    if not clean:
        return None
    return float(median(clean))


def _source_from_offer(offer: Any) -> str | None:
    source = _canonical_price_source(_field(offer, "source", "provider", "site"))
    if source:
        return source
    metadata = _field(offer, "metadata")
    if isinstance(metadata, dict):
        return _canonical_price_source(metadata.get("source") or metadata.get("provider") or metadata.get("origin"))
    return None


def _exact_price_inventory(candidate: Any) -> dict[str, Any]:
    by_source: dict[str, list[float]] = {}
    books: set[str] = set()
    exact_offers = 0
    market_names: set[str] = set()

    for offer in _offer_rows(candidate):
        if not _same_exact_line(candidate, offer):
            continue
        price = _as_float(_field(offer, "price", "odds", "decimal"), None)
        if price is None or price <= 1.0:
            continue
        source = _source_from_offer(offer)
        if not source:
            continue
        by_source.setdefault(source, []).append(price)
        exact_offers += 1
        book = _norm(_field(offer, "bookmaker", "book", "site", "sportsbook"))
        if book:
            books.add(book)
        market_name = str(_field(offer, "market_name", "market_key", "market_subtype") or "").strip()
        if market_name:
            market_names.add(market_name)

    source_prices = {source: round(_median(prices) or 0.0, 4) for source, prices in by_source.items()}
    source_prices = {source: price for source, price in source_prices.items() if price > 1.0}
    price_values = list(source_prices.values())
    consensus_avg = sum(price_values) / len(price_values) if price_values else None
    consensus_median = _median(price_values)
    min_price = min(price_values) if price_values else None
    max_price = max(price_values) if price_values else None
    dispersion_pct = 0.0
    if min_price and max_price and consensus_median:
        dispersion_pct = ((max_price - min_price) / consensus_median) * 100.0

    return {
        "exact_odds_sources_count": len(source_prices),
        "exact_odds_sources": sorted(source_prices),
        "exact_source_prices": dict(sorted(source_prices.items())),
        "exact_books_count": len(books),
        "exact_books": sorted(books),
        "exact_offers_count": exact_offers,
        "exact_market_names": sorted(market_names),
        "consensus_price_avg": round(consensus_avg, 4) if consensus_avg else None,
        "consensus_price_median": round(consensus_median, 4) if consensus_median else None,
        "min_source_price": round(min_price, 4) if min_price else None,
        "max_source_price": round(max_price, 4) if max_price else None,
        "source_price_dispersion_pct": round(dispersion_pct, 4),
    }


def _flatten(value: Any, depth: int = 0) -> list[Any]:
    if depth > 5:
        return []
    if isinstance(value, dict):
        rows: list[Any] = []
        for key, item in value.items():
            rows.append(key)
            rows.extend(_flatten(item, depth + 1))
        return rows
    if isinstance(value, (list, tuple, set)):
        rows = []
        for item in list(value)[:80]:
            rows.extend(_flatten(item, depth + 1))
        return rows
    return [value]


def _context_tokens(value: Any) -> set[str]:
    text = " ".join(str(item) for item in _flatten(value) if item not in (None, ""))[:25000].lower()
    found: set[str] = set()
    for alias, canonical in CONTEXT_SOURCE_ALIASES.items():
        if alias in text or alias.replace("_", "") in text.replace("_", ""):
            found.add(canonical)
    return found


def _context_sources(value: Any) -> set[str]:
    sources: set[str] = set()
    if value is None:
        return sources
    if isinstance(value, dict):
        direct = _canonical_context_source(value.get("source") or value.get("provider") or value.get("name"))
        if direct:
            sources.add(direct)
        for item in value.values():
            sources |= _context_sources(item)
        sources |= _context_tokens(value)
        return sources
    if isinstance(value, (list, tuple, set)):
        for item in value:
            sources |= _context_sources(item)
        return sources
    direct = _canonical_context_source(getattr(value, "source", None))
    if direct:
        sources.add(direct)
    for attr in ("payload", "details", "profits", "analysis", "diagnostics"):
        payload = getattr(value, attr, None)
        if payload:
            sources |= _context_tokens(payload)
    return sources


def _candidate_context_sources(candidate: Any, contexts_by_match: Any) -> set[str]:
    sources: set[str] = set()
    key = getattr(candidate, "match_key", "")
    if isinstance(contexts_by_match, dict):
        sources |= _context_sources(contexts_by_match.get(key))
        if not sources:
            loose = f"{getattr(candidate, 'home_team', '')}|{getattr(candidate, 'away_team', '')}"
            sources |= _context_sources(contexts_by_match.get(loose))
    summary = getattr(candidate, "source_summary", None)
    if isinstance(summary, dict):
        for field in ("context_sources", "context_source_names", "confirmation_sources", "sources"):
            value = summary.get(field)
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    src = _canonical_context_source(item)
                    if src:
                        sources.add(src)
            elif value:
                for item in re.split(r"[,;/|]+", str(value)):
                    src = _canonical_context_source(item)
                    if src:
                        sources.add(src)
        sources |= _context_tokens(summary)
    if _truthy(os.getenv("API_COVERAGE_CONTEXT_EXCLUDE_NEWS_WEATHER_FROM_CORE"), True):
        core = {s for s in sources if s not in {"weather", "news"}}
        return core if core else sources
    return sources


def _probability_for_ev(candidate: Any) -> float:
    for attr in ("canonical_adjusted_probability", "probability_used_for_ev", "adjusted_probability", "final_probability", "model_probability"):
        value = _as_float(getattr(candidate, attr, None), None)
        if value and value > 0:
            return value
    return 0.0


def _reprice_candidate(candidate: Any, price: float) -> None:
    implied = 1.0 / price if price > 1.0 else 0.0
    probability = _probability_for_ev(candidate)
    try:
        candidate.odds = round(price, 4)
        candidate.selected_odds = round(price, 4)
        candidate.price_used_for_ev = round(price, 4)
        candidate.implied_probability = implied
        candidate.selected_implied_probability = implied
        if probability > 0:
            candidate.probability_used_for_ev = probability
            candidate.ev_pct = (probability * price - 1.0) * 100.0
            candidate.edge_pct = (probability - implied) * 100.0
    except Exception:
        pass


def _annotate(candidate: Any, inventory: dict[str, Any], context_sources: set[str]) -> None:
    try:
        summary = dict(getattr(candidate, "source_summary", {}) or {})
        summary.update(inventory)
        summary["context_sources_count"] = len(context_sources)
        summary["context_sources"] = sorted(context_sources)
        summary["api_coverage_consensus_guard"] = True
        candidate.source_summary = summary
    except Exception:
        pass
    try:
        diag = dict(getattr(candidate, "diagnostics", {}) or {})
        diag["api_coverage_consensus"] = {
            **inventory,
            "context_sources_count": len(context_sources),
            "context_sources": sorted(context_sources),
        }
        candidate.diagnostics = diag
    except Exception:
        pass


def _guard_candidate(candidate: Any, contexts_by_match: Any) -> tuple[bool, str, dict[str, Any]]:
    inv = _exact_price_inventory(candidate)
    ctx_sources = _candidate_context_sources(candidate, contexts_by_match)
    _annotate(candidate, inv, ctx_sources)

    min_odds_sources = max(1, _as_int(os.getenv("API_COVERAGE_MIN_EXACT_ODDS_SOURCES"), 2))
    min_books = max(1, _as_int(os.getenv("API_COVERAGE_MIN_EXACT_BOOKS"), 2))
    min_context_sources = max(1, _as_int(os.getenv("API_COVERAGE_MIN_CONTEXT_SOURCES"), 2))
    max_dispersion = max(0.0, float(os.getenv("API_COVERAGE_MAX_SOURCE_PRICE_DISPERSION_PCT") or 18.0))
    max_selected_drift = max(0.0, float(os.getenv("API_COVERAGE_MAX_SELECTED_PRICE_DRIFT_PCT") or 8.0))

    if int(inv.get("exact_odds_sources_count") or 0) < min_odds_sources:
        return False, "api_coverage_missing_2_exact_odds_sources", inv
    if int(inv.get("exact_books_count") or 0) < min_books:
        return False, "api_coverage_missing_2_exact_books", inv
    if len(ctx_sources) < min_context_sources:
        return False, "api_coverage_missing_2_context_sources", inv

    dispersion = _as_float(inv.get("source_price_dispersion_pct"), 0.0) or 0.0
    if dispersion > max_dispersion:
        return False, "api_coverage_price_sources_disagree", inv

    consensus = _as_float(inv.get("consensus_price_avg"), None) or _as_float(inv.get("consensus_price_median"), None)
    if not consensus or consensus <= 1.0:
        return False, "api_coverage_missing_consensus_price", inv

    selected = _as_float(getattr(candidate, "selected_odds", None), None) or _as_float(getattr(candidate, "odds", None), None)
    if not selected or selected <= 1.0:
        return False, "api_coverage_missing_selected_price", inv

    if abs(selected - consensus) / consensus * 100.0 > max_selected_drift:
        try:
            candidate.reasons.append(
                f"odds_rebased_to_exact_consensus:{selected:.3f}->{consensus:.3f}"
            )
        except Exception:
            pass
    _reprice_candidate(candidate, consensus)

    probability = _probability_for_ev(candidate)
    implied = 1.0 / consensus
    ev_pct = (probability * consensus - 1.0) * 100.0 if probability > 0 else -999.0
    edge_pp = (probability - implied) * 100.0 if probability > 0 else -999.0
    min_ev = float(os.getenv("API_COVERAGE_MIN_CANONICAL_EV_PCT") or 0.0)
    min_edge = float(os.getenv("API_COVERAGE_MIN_CANONICAL_EDGE_PP") or 0.0)
    if ev_pct < min_ev or edge_pp < min_edge:
        return False, "api_coverage_consensus_value_not_positive", inv

    try:
        candidate.books_count = max(int(getattr(candidate, "books_count", 0) or 0), int(inv.get("exact_books_count") or 0))
        candidate.sources_count = max(int(getattr(candidate, "sources_count", 0) or 0), int(inv.get("exact_odds_sources_count") or 0))
        candidate.integrity_status = "api_coverage_consensus_checked"
        report = dict(getattr(candidate, "integrity_report", {}) or {})
        report["api_coverage_consensus"] = inv
        candidate.integrity_report = report
    except Exception:
        pass

    return True, "ok", inv


def _patch_candidate_factory() -> dict[str, Any]:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, PATCH_MARKER, False):
        return {"candidate_factory": "already_patched"}

    original_build = CandidateFactory.build_candidates

    def build_candidates_with_api_coverage(self: Any, matches: Any, offers_by_match: Any, contexts_by_match: Any, market_signals_by_match: Any = None):
        candidates, rejections, debug = original_build(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        if not _truthy(os.getenv("API_COVERAGE_CONSENSUS_GUARD_ENABLED"), True):
            return candidates, rejections, debug

        rejections = dict(rejections or {})
        debug = dict(debug or {})
        kept: list[Any] = []
        rejection_rows: dict[str, int] = {}
        samples: list[dict[str, Any]] = []

        for candidate in list(candidates or []):
            ok, reason, inv = _guard_candidate(candidate, contexts_by_match)
            if ok:
                kept.append(candidate)
            else:
                rejections[reason] = int(rejections.get(reason, 0) or 0) + 1
                rejection_rows[reason] = int(rejection_rows.get(reason, 0) or 0) + 1
            if len(samples) < 30:
                samples.append({
                    "match_key": getattr(candidate, "match_key", ""),
                    "home": getattr(candidate, "home_team", ""),
                    "away": getattr(candidate, "away_team", ""),
                    "family": getattr(candidate, "family", ""),
                    "selection": getattr(candidate, "selection", ""),
                    "point": getattr(candidate, "point", None),
                    "result": "kept" if ok else reason,
                    "odds": round(_as_float(getattr(candidate, "odds", None), 0.0) or 0.0, 4),
                    "ev_pct": round(_as_float(getattr(candidate, "ev_pct", None), 0.0) or 0.0, 4),
                    **{k: inv.get(k) for k in (
                        "exact_odds_sources_count",
                        "exact_odds_sources",
                        "exact_books_count",
                        "consensus_price_avg",
                        "source_price_dispersion_pct",
                    )},
                })

        kept.sort(
            key=lambda c: (
                _as_float(getattr(c, "ev_pct", None), -999.0) or -999.0,
                _as_float(getattr(c, "edge_pct", None), -999.0) or -999.0,
                _as_float(getattr(c, "confidence", None), 0.0) or 0.0,
                _as_int(getattr(c, "sources_count", None), 0),
                _as_int(getattr(c, "books_count", None), 0),
            ),
            reverse=True,
        )

        report = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "input_candidates": len(list(candidates or [])),
            "kept_candidates": len(kept),
            "rejections": rejection_rows,
            "min_exact_odds_sources": _as_int(os.getenv("API_COVERAGE_MIN_EXACT_ODDS_SOURCES"), 2),
            "min_exact_books": _as_int(os.getenv("API_COVERAGE_MIN_EXACT_BOOKS"), 2),
            "min_context_sources": _as_int(os.getenv("API_COVERAGE_MIN_CONTEXT_SOURCES"), 2),
            "max_source_price_dispersion_pct": float(os.getenv("API_COVERAGE_MAX_SOURCE_PRICE_DISPERSION_PCT") or 18.0),
            "sample": samples,
        }
        debug["api_coverage_consensus_runtime_patch"] = report
        _write_report(report)
        return kept, rejections, debug

    CandidateFactory.build_candidates = build_candidates_with_api_coverage  # type: ignore[assignment]
    setattr(CandidateFactory, PATCH_MARKER, True)
    return {"candidate_factory": "patched", "guard": "api_coverage_consensus"}


def _install_env_defaults() -> None:
    defaults = {
        "API_COVERAGE_CONSENSUS_GUARD_ENABLED": "true",
        "API_COVERAGE_MIN_EXACT_ODDS_SOURCES": "2",
        "API_COVERAGE_MIN_EXACT_BOOKS": "2",
        "API_COVERAGE_MIN_CONTEXT_SOURCES": "2",
        "API_COVERAGE_CONTEXT_EXCLUDE_NEWS_WEATHER_FROM_CORE": "true",
        "API_COVERAGE_MAX_SOURCE_PRICE_DISPERSION_PCT": "18.0",
        "API_COVERAGE_MAX_SELECTED_PRICE_DRIFT_PCT": "8.0",
        "API_COVERAGE_MIN_CANONICAL_EV_PCT": "0.0",
        "API_COVERAGE_MIN_CANONICAL_EDGE_PP": "0.0",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    _install_env_defaults()
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        payload.update(_patch_candidate_factory())
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write_report(payload)
    return payload
