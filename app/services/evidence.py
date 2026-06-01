from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any

from app.schemas import (
    ConsensusLine,
    ContextObservation,
    LineSnapshot,
    Match,
    MatchContext,
    MatchContextBundle,
    MatchServing,
    Offer,
)

UTC = timezone.utc

CONTEXT_KIND_BY_PROVIDER = {
    "api_football": "fixture_team_stats",
    "bzzoiro": "xg_prediction",
    "espn": "schedule_form",
    "football_data": "standings_form",
    "futrixmetrics": "rating_prediction",
    "gnews": "news",
    "newsapi": "news",
    "openfootball": "historical_results",
    "openligadb": "fixture_results",
    "self_history": "self_history",
    "sportlogic": "fixture_context",
    "sstats": "xg_form",
    "thesportsdb": "team_profile",
    "weather": "weather",
}

WEATHER_SOURCES = {"weather", "weatherapi", "openweathermap", "open_meteo"}
NEWS_SOURCES = {"news", "newsapi", "gnews", "currents"}


def build_context_bundles(
    context_maps: dict[str, dict[str, MatchContext]],
    merged_contexts: dict[str, MatchContext],
    observed_at: datetime,
) -> dict[str, MatchContextBundle]:
    observations_by_match: dict[str, list[ContextObservation]] = defaultdict(list)
    for provider_name, mapping in (context_maps or {}).items():
        provider = _normalize_source(provider_name)
        for match_key, raw_context in (mapping or {}).items():
            context = _coerce_context(raw_context)
            if context is None:
                continue
            observations_by_match[str(match_key)].append(
                context_observation_from_context(
                    match_key=str(match_key),
                    provider=provider or _normalize_source(context.source),
                    context=context,
                    observed_at=observed_at,
                )
            )

    bundles: dict[str, MatchContextBundle] = {}
    all_match_keys = set(observations_by_match) | set(merged_contexts or {})
    for match_key in all_match_keys:
        observations = sorted(
            observations_by_match.get(match_key, []),
            key=lambda item: (item.provider, item.kind, item.provenance_hash),
        )
        providers = _unique(item.provider for item in observations if item.provider)
        merged_context = _coerce_context((merged_contexts or {}).get(match_key))
        details = dict(getattr(merged_context, "details", {}) or {}) if merged_context is not None else {}
        if merged_context is not None:
            details["context_observations"] = [serialize_dataclass(item) for item in observations]
            details["context_sources"] = providers
            details["merged_sources"] = _unique(list(details.get("merged_sources") or []) + providers)
            details["context_source_count"] = len(providers)
            details["context_agreement_score"] = _context_agreement_score(observations)
            details["provider_conflict_score"] = _provider_conflict_score(observations)
            details["has_weather_context"] = _has_source_or_kind(observations, WEATHER_SOURCES, {"weather"})
            details["has_lineup_context"] = _has_detail_key(observations, ("lineup", "starting"))
            details["has_injury_context"] = _has_detail_key(observations, ("injur", "absence", "absences"))
            details["has_news_context"] = _has_source_or_kind(observations, NEWS_SOURCES, {"news", "injury_news"})
            merged_context.details = details
        bundles[match_key] = MatchContextBundle(
            match_key=match_key,
            contexts=observations,
            merged_context=merged_context,
            context_source_count=len(providers),
            agreement_score=_context_agreement_score(observations),
            provider_conflict_score=_provider_conflict_score(observations),
            has_weather=_has_source_or_kind(observations, WEATHER_SOURCES, {"weather"}),
            has_lineups=_has_detail_key(observations, ("lineup", "starting")),
            has_injuries=_has_detail_key(observations, ("injur", "absence", "absences")),
            has_news=_has_source_or_kind(observations, NEWS_SOURCES, {"news", "injury_news"}),
        )
    return bundles


def context_observation_from_context(
    *,
    match_key: str,
    provider: str,
    context: MatchContext,
    observed_at: datetime,
) -> ContextObservation:
    source = _normalize_source(provider or context.source) or "unknown"
    details = dict(getattr(context, "details", {}) or {})
    payload = dict(getattr(context, "payload", {}) or {}) if isinstance(getattr(context, "payload", {}), dict) else {}
    metrics = _context_metrics(context, details)
    effective_at = _parse_datetime(
        details.get("effective_at")
        or details.get("as_of")
        or details.get("observed_at")
        or payload.get("effective_at")
        or payload.get("as_of")
    )
    observed = _ensure_utc(observed_at)
    freshness_sec = None
    if effective_at is not None:
        freshness_sec = max(0, int((observed - effective_at).total_seconds()))
    provenance_hash = _hash_payload(
        {
            "match_key": match_key,
            "provider": source,
            "kind": _context_kind(source, context, details),
            "metrics": metrics,
            "details": details,
        }
    )
    return ContextObservation(
        match_key=match_key,
        provider=source,
        kind=_context_kind(source, context, details),
        observed_at=observed,
        effective_at=effective_at,
        freshness_sec=freshness_sec,
        confidence=float(getattr(context, "confidence", 0.0) or 0.0),
        metrics=metrics,
        payload=payload,
        details=details,
        provenance_hash=provenance_hash,
    )


def build_line_snapshots(offers_by_match: dict[str, list[Offer]], observed_at: datetime) -> list[LineSnapshot]:
    observed = _ensure_utc(observed_at)
    rows: list[LineSnapshot] = []
    for match_key, offers in (offers_by_match or {}).items():
        for offer in offers or []:
            price = _to_float(getattr(offer, "price", None))
            if price is None or price <= 1.0:
                continue
            rows.append(
                LineSnapshot(
                    match_key=str(match_key),
                    market_key=market_key(offer),
                    provider=_normalize_source(getattr(offer, "source", "")),
                    bookmaker=str(getattr(offer, "bookmaker", "") or "").strip(),
                    family=offer.family,
                    selection=str(getattr(offer, "selection", "") or "").strip(),
                    price=float(price),
                    point=getattr(offer, "point", None),
                    team_side=getattr(offer, "team_side", None),
                    source_event_id=getattr(offer, "source_event_id", None),
                    observed_at=observed,
                    metadata=dict(getattr(offer, "metadata", {}) or {}),
                )
            )
    return rows


def build_consensus_lines(
    offers_by_match: dict[str, list[Offer]],
    market_signals_by_match: dict[str, dict[str, Any]] | None = None,
) -> list[ConsensusLine]:
    signal_map = market_signals_by_match or {}
    grouped: dict[tuple[str, str], list[Offer]] = defaultdict(list)
    for match_key, offers in (offers_by_match or {}).items():
        for offer in offers or []:
            grouped[(str(match_key), market_key(offer))].append(offer)

    rows: list[ConsensusLine] = []
    for (match_key, key), offers in grouped.items():
        prices = [_to_float(getattr(item, "price", None)) for item in offers]
        valid_prices = [float(item) for item in prices if item is not None and float(item) > 1.0]
        if not valid_prices:
            continue
        first = offers[0]
        signal = (signal_map.get(match_key) or {}).get(key) or {}
        consensus = _to_float(signal.get("consensus_fair_odds"))
        dispersion = _to_float(signal.get("consensus_dispersion_pct") or signal.get("dispersion_pct"))
        delta = _to_float(signal.get("delta_prob_pp"))
        rows.append(
            ConsensusLine(
                match_key=match_key,
                market_key=key,
                family=first.family,
                selection=str(getattr(first, "selection", "") or ""),
                point=getattr(first, "point", None),
                team_side=getattr(first, "team_side", None),
                best_price=max(valid_prices),
                consensus_fair_odds=consensus,
                books=_unique(str(getattr(item, "bookmaker", "") or "").strip() for item in offers),
                sources=_unique(_normalize_source(getattr(item, "source", "")) for item in offers),
                snapshots_count=len(valid_prices),
                dispersion_pct=dispersion,
                steam_score=round(float(delta) / 10.0, 4) if delta is not None else None,
            )
        )
    return rows


def build_match_serving(
    matches: list[Match],
    offers_by_match: dict[str, list[Offer]],
    context_bundles: dict[str, MatchContextBundle],
    market_signals_by_match: dict[str, dict[str, Any]] | None,
    observed_at: datetime,
) -> dict[str, MatchServing]:
    snapshots = build_line_snapshots(offers_by_match, observed_at)
    snapshots_by_match: dict[str, list[LineSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        snapshots_by_match[snapshot.match_key].append(snapshot)

    result: dict[str, MatchServing] = {}
    signals = market_signals_by_match or {}
    for match in matches:
        match_key = match.match_key
        bundle = context_bundles.get(match_key)
        match_snapshots = snapshots_by_match.get(match_key, [])
        signal_rows = list((signals.get(match_key) or {}).values())
        steam_values = [_to_float(row.get("delta_prob_pp")) for row in signal_rows if isinstance(row, dict)]
        steam_values = [float(item) for item in steam_values if item is not None]
        movement = _best_movement(signal_rows)
        result[match_key] = MatchServing(
            match_key=match_key,
            context_source_count=int(getattr(bundle, "context_source_count", 0) or 0),
            line_family_count=len(_unique(item.family for item in match_snapshots)),
            line_source_count=len(_unique(item.provider for item in match_snapshots)),
            line_snapshot_count=len(match_snapshots),
            line_snapshot_count_6h=len(match_snapshots),
            agreement_score=getattr(bundle, "agreement_score", None),
            provider_conflict_score=getattr(bundle, "provider_conflict_score", None),
            has_weather=bool(getattr(bundle, "has_weather", False)),
            has_lineups=bool(getattr(bundle, "has_lineups", False)),
            has_injuries=bool(getattr(bundle, "has_injuries", False)),
            has_news=bool(getattr(bundle, "has_news", False)),
            steam_score=round(max(steam_values, key=abs) / 10.0, 4) if steam_values else None,
            best_market_movement=movement,
            context_sources=_unique(item.provider for item in getattr(bundle, "contexts", []) or []),
            line_sources=_unique(item.provider for item in match_snapshots),
            line_families=_unique(str(item.family) for item in match_snapshots),
        )
    return result


def serialize_dataclass(value: Any) -> Any:
    if is_dataclass(value):
        row = asdict(value)
        return {key: serialize_dataclass(item) for key, item in row.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_dataclass(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_dataclass(item) for key, item in value.items()}
    return value


def market_key(offer: Offer) -> str:
    point = "" if getattr(offer, "point", None) in (None, "") else f"{float(getattr(offer, 'point')):.2f}"
    team_side = str(getattr(offer, "team_side", "") or "").strip().lower()
    selection = str(getattr(offer, "selection", "") or "").strip().lower()
    return "|".join([str(getattr(offer, "family", "") or ""), selection, point, team_side])


def _context_metrics(context: MatchContext, details: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "expected_home": getattr(context, "expected_home", None),
        "expected_away": getattr(context, "expected_away", None),
        "home_win_probability": getattr(context, "home_win_probability", None),
        "away_win_probability": getattr(context, "away_win_probability", None),
        "home_starting": getattr(context, "home_starting", None),
        "away_starting": getattr(context, "away_starting", None),
    }
    for key, value in details.items():
        lowered = str(key).lower()
        if any(token in lowered for token in ("xg", "prob", "form", "ppg", "rank", "injur", "absence", "weather", "lineup")):
            keys[str(key)] = value
    return {key: value for key, value in keys.items() if value not in (None, "")}


def _context_kind(source: str, context: MatchContext, details: dict[str, Any]) -> str:
    explicit = details.get("context_kind") or details.get("kind")
    if explicit:
        return str(explicit)
    if _has_weather_details(details):
        return "weather"
    if source in NEWS_SOURCES:
        return "news"
    return CONTEXT_KIND_BY_PROVIDER.get(source, "context")


def _context_agreement_score(observations: list[ContextObservation]) -> float | None:
    xg_totals: list[float] = []
    h2h_edges: list[float] = []
    for obs in observations:
        metrics = obs.metrics or {}
        home = _to_float(metrics.get("expected_home"))
        away = _to_float(metrics.get("expected_away"))
        if home is not None and away is not None:
            xg_totals.append(home + away)
        home_p = _to_float(metrics.get("home_win_probability"))
        away_p = _to_float(metrics.get("away_win_probability"))
        if home_p is not None and away_p is not None:
            h2h_edges.append(home_p - away_p)
    scores: list[float] = []
    if len(xg_totals) >= 2:
        avg = max(mean(xg_totals), 0.01)
        scores.append(max(0.0, 1.0 - min(1.0, pstdev(xg_totals) / avg)))
    if len(h2h_edges) >= 2:
        scores.append(max(0.0, 1.0 - min(1.0, pstdev(h2h_edges))))
    if not scores:
        return None
    return round(mean(scores), 4)


def _provider_conflict_score(observations: list[ContextObservation]) -> float | None:
    agreement = _context_agreement_score(observations)
    if agreement is None:
        return None
    return round(1.0 - agreement, 4)


def _has_source_or_kind(observations: list[ContextObservation], sources: set[str], kinds: set[str]) -> bool:
    return any(obs.provider in sources or obs.kind in kinds for obs in observations)


def _has_detail_key(observations: list[ContextObservation], needles: tuple[str, ...]) -> bool:
    for obs in observations:
        haystack = " ".join([*(obs.metrics or {}).keys(), *(obs.details or {}).keys()]).lower()
        if any(needle in haystack for needle in needles):
            return True
    return False


def _has_weather_details(details: dict[str, Any]) -> bool:
    return any(str(key).startswith("weather_") for key in details)


def _best_movement(rows: list[dict[str, Any]]) -> str | None:
    ranked: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("movement_label") or "").strip()
        delta = _to_float(row.get("delta_prob_pp"))
        if label and delta is not None:
            ranked.append((abs(float(delta)), label))
    if not ranked:
        return None
    return max(ranked, key=lambda item: item[0])[1]


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _normalize_source(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "_".join(part for part in raw.split("_") if part)


def _coerce_context(value: Any) -> MatchContext | None:
    if isinstance(value, MatchContext):
        return value
    if isinstance(value, MatchContextBundle):
        return value.merged_context
    if isinstance(value, dict):
        return MatchContext(
            source=str(value.get("source", "unknown")),
            payload=value.get("payload", value),
            expected_home=value.get("expected_home"),
            expected_away=value.get("expected_away"),
            home_win_probability=value.get("home_win_probability"),
            away_win_probability=value.get("away_win_probability"),
            home_starting=value.get("home_starting"),
            away_starting=value.get("away_starting"),
            confidence=float(value.get("confidence", 58.0) or 58.0),
            profits=value.get("profits", {}),
            details=value.get("details", {}),
        )
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if not value:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
