"""Strict evidence truth for Focused Alpha candidates and promotions.

The runtime has many cumulative and diagnostic counters.  They are useful for
coverage planning, but they are not proof that one exact market currently has
independent providers or bookmakers.  This module derives publication evidence
only from explicit identities and exact raw offers.
"""

from __future__ import annotations

import math
import re
from typing import Any

_ODDS_SOURCE_KEYS = (
    "verified_odds_sources",
    "exact_odds_sources",
    "independent_odds_sources",
    "odds_sources",
)
_CONTEXT_SOURCE_KEYS = (
    "verified_context_sources",
    "exact_context_sources",
    "independent_context_sources",
    "context_sources",
    "confirmation_sources",
)
_BOOKMAKER_KEYS = (
    "verified_bookmakers",
    "exact_bookmakers",
    "bookmakers",
    "bookmaker_names",
    "books",
)

_PSEUDO_SOURCES = {
    "",
    "unknown",
    "none",
    "null",
    "market",
    "market_signal",
    "market_implied",
    "raw_offer_artifacts",
    "line_history",
    "ensemble",
    "day_inventory",
    "dayinventory",
    "inventory_context",
    "runtime_context",
    "xg_model_context",
    "form_context",
    "a_cover_market_promotion",
    "b_cover_market_promotion",
    "controlled_consensus_rescue",
}
_BOOKMAKER_SOURCE_NAMES = {
    "bet365",
    "betfair",
    "betfair_exchange",
    "pinnacle",
    "sbobet",
    "unibet",
}
_HARD_XG_SOURCES = {
    "api_football",
    "bzzoiro",
    "football_data",
    "futrixmetrics",
    "highlightly",
    "opta",
    "sstats",
    "statsbomb",
    "understat",
    "wyscout",
}
_PROXY_XG_TOKENS = (
    "market_implied",
    "market implied",
    "consensus",
    "proxy",
    "placeholder",
    "default",
    "synthetic",
    "market_probability",
)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", "_", text)
    return text.strip("_")


def _iter_dicts(value: Any, *, depth: int = 0, path: tuple[str, ...] = ()):
    if depth > 7:
        return
    if isinstance(value, dict):
        yield value, path
        for key, nested in value.items():
            if isinstance(nested, (dict, list, tuple)):
                yield from _iter_dicts(nested, depth=depth + 1, path=path + (_norm(key),))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_dicts(nested, depth=depth + 1, path=path)


def _as_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [item for item in re.split(r"[,;+|/]", value) if item.strip()]
    return []


def canonical_source(value: Any, role: str) -> str:
    name = _norm(value)
    if not name:
        return ""
    aliases = (
        (("odds_api_io", "oddsapiio", "odds_api"), "odds_api_io"),
        (("bzzoiro", "bsd_sports", "bsd"), "bzzoiro"),
        (("sstats_pari", "sstatspari"), "sstats_pari"),
        (("sstats_form", "sstats"), "sstats"),
        (("sport_logic", "sportlogic"), "sportlogic"),
        (("football_data_org", "football_data"), "football_data"),
        (("club_elo", "clubelo"), "clubelo"),
        (("the_sports_db", "thesportsdb"), "thesportsdb"),
        (("open_liga_db", "openligadb"), "openligadb"),
        (("open_meteo", "openmeteo"), "openmeteo"),
        (("weather_api", "weatherapi"), "weatherapi"),
    )
    for variants, canonical in aliases:
        if name in variants:
            name = canonical
            break
    if name in _PSEUDO_SOURCES or name.startswith("book_"):
        return ""
    if role == "odds" and name in _BOOKMAKER_SOURCE_NAMES:
        return ""
    if role == "context" and name in {"odds_api_io", "sstats_pari", "line_history"}:
        return ""
    return name


def _verified_sources(row: dict[str, Any], role: str) -> list[str] | None:
    key = "verified_odds_sources" if role == "odds" else "verified_context_sources"
    for container, _ in _iter_dicts(row):
        if key not in container:
            continue
        return sorted(
            {
                source
                for value in _as_values(container.get(key))
                if (source := canonical_source(value, role))
            }
        )
    return None


def strict_sources(row: dict[str, Any], role: str) -> list[str]:
    verified = _verified_sources(row, role)
    if verified is not None:
        return verified
    keys = _ODDS_SOURCE_KEYS if role == "odds" else _CONTEXT_SOURCE_KEYS
    names: set[str] = set()
    for container, path in _iter_dicts(row):
        for key in keys:
            for value in _as_values(container.get(key)):
                source = canonical_source(value, role)
                if source:
                    names.add(source)
        if role == "odds" and path and path[-1] in {"raw_bucket_offers", "offers"}:
            source = canonical_source(container.get("source") or container.get("provider"), role)
            if source:
                names.add(source)
    return sorted(names)


def strict_bookmakers(row: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for container, path in _iter_dicts(row):
        for key in _BOOKMAKER_KEYS:
            for value in _as_values(container.get(key)):
                name = _norm(value)
                if name and not name.isdigit():
                    names.add(name)
        if path and path[-1] in {"raw_bucket_offers", "offers"}:
            name = _norm(
                container.get("bookmaker")
                or container.get("bookmaker_slug")
                or container.get("book")
            )
            if name:
                names.add(name)
    return sorted(names)[:12]


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "."))
    except Exception:
        return None
    return parsed if math.isfinite(parsed) else None


def _quality_truth(row: dict[str, Any]) -> tuple[float | None, str]:
    score: float | None = None
    source = ""
    for container, path in _iter_dicts(row):
        if (path and path[-1] == "quality") or "quality_score" in container:
            candidate = _finite_float(container.get("quality_score"))
            if candidate is not None and (score is None or candidate > score):
                score = candidate
                source = _norm(
                    container.get("quality_score_source")
                    or container.get("source")
                    or row.get("quality_score_source")
                )
    return score, source


def _xg_truth(row: dict[str, Any]) -> tuple[bool, list[str]]:
    values_present = any(
        _finite_float(container.get(key)) is not None
        for container, _ in _iter_dicts(row)
        for key in (
            "total_xg",
            "expected_home",
            "expected_away",
            "home_xg",
            "away_xg",
            "xg_home",
            "xg_away",
        )
    )
    sources: set[str] = set()
    for container, path in _iter_dicts(row):
        xg_path = any("xg" in item or "expected_goal" in item for item in path)
        for key in ("xg_source", "expected_goals_source", "xg_provider"):
            if container.get(key):
                sources.add(_norm(container.get(key)))
        if xg_path:
            for key in ("source", "source_mode", "context_path", "provider"):
                if container.get(key):
                    sources.add(_norm(container.get(key)))
    model_mode = _norm(row.get("model_mode"))
    if model_mode:
        sources.add(model_mode)
    hard_sources = {
        source
        for source in sources
        if canonical_source(source, "context") in _HARD_XG_SOURCES
    }
    return bool(values_present and hard_sources), sorted(sources)


def evidence_truth(row: dict[str, Any], *, inventory_row: dict[str, Any] | None = None) -> dict[str, Any]:
    odds_sources = strict_sources(row, "odds")
    context_sources = strict_sources(inventory_row or row, "context")
    bookmakers = strict_bookmakers(row)
    hard_xg, xg_sources = _xg_truth(row)
    quality_score, quality_source = _quality_truth(row)
    return {
        "odds_sources": odds_sources,
        "odds_sources_count": len(odds_sources),
        "context_sources": context_sources,
        "context_sources_count": len(context_sources),
        "bookmakers": bookmakers,
        "books_count": len(bookmakers),
        "hard_xg": hard_xg,
        "xg_sources": xg_sources,
        "quality_score": quality_score,
        "quality_score_source": quality_source,
        "a_cover": len(odds_sources) >= 2 and len(context_sources) >= 2 and len(bookmakers) >= 2,
    }


def repair_candidate_evidence(
    row: dict[str, Any],
    *,
    inventory_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repaired = dict(row)
    truth = evidence_truth(repaired, inventory_row=inventory_row)
    repaired["odds_sources"] = truth["odds_sources"]
    repaired["odds_sources_count"] = truth["odds_sources_count"]
    repaired["independent_odds_sources_count"] = truth["odds_sources_count"]
    repaired["confirmation_sources"] = truth["context_sources"]
    repaired["context_sources"] = truth["context_sources"]
    repaired["confirmation_sources_count"] = truth["context_sources_count"]
    repaired["context_sources_count"] = truth["context_sources_count"]
    repaired["books_count"] = truth["books_count"]
    if truth["bookmakers"]:
        repaired["bookmakers"] = truth["bookmakers"]
    if truth["quality_score"] is not None:
        repaired["quality_score"] = truth["quality_score"]
    if truth["quality_score_source"]:
        repaired["quality_score_source"] = truth["quality_score_source"]
    if truth["hard_xg"]:
        if not repaired.get("xg_source") and truth["xg_sources"]:
            repaired["xg_source"] = truth["xg_sources"][0]
    elif truth["xg_sources"]:
        repaired["xg_source"] = "market_implied_or_proxy"
    elif any(repaired.get(key) not in (None, "") for key in ("expected_home", "expected_away", "total_xg")):
        repaired["xg_source"] = "unverified_xg"
    repaired["tier_a_coverage_ready"] = truth["a_cover"]

    diagnostics = repaired.get("diagnostics") if isinstance(repaired.get("diagnostics"), dict) else {}
    diagnostics = dict(diagnostics)
    diagnostics["focused_alpha_evidence_truth"] = truth
    repaired["diagnostics"] = diagnostics

    source_summary = repaired.get("source_summary") if isinstance(repaired.get("source_summary"), dict) else {}
    source_summary = dict(source_summary)
    source_summary["exact_odds_sources"] = truth["odds_sources"]
    source_summary["context_sources"] = truth["context_sources"]
    source_summary["books"] = truth["bookmakers"]
    source_summary["books_count"] = truth["books_count"]
    source_summary["publish_coverage_contract"] = {
        "tier": "A" if truth["a_cover"] else "below_A",
        "odds_sources_count": truth["odds_sources_count"],
        "bookmakers_count": truth["books_count"],
        "context_sources_count": truth["context_sources_count"],
        "hard_xg": truth["hard_xg"],
        "basis": "explicit_provider_and_exact_offer_identities",
    }
    repaired["source_summary"] = source_summary
    return repaired


__all__ = [
    "canonical_source",
    "evidence_truth",
    "repair_candidate_evidence",
    "strict_bookmakers",
    "strict_sources",
]
