from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

try:
    from app.services.telegram_i18n import (
        normalize_telegram_text,
        translate_league_name,
        translate_reject_reason,
        translate_selection_text,
        translate_team_name,
    )
except Exception:
    def normalize_telegram_text(text: Any) -> str: return str(text or "")
    def translate_league_name(name: Any) -> str: return str(name or "")
    def translate_reject_reason(reason: Any) -> str: return str(reason or "")
    def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str: return str(selection or "")
    def translate_team_name(name: Any) -> str: return str(name or "")

UTC = timezone.utc

try:
    from app.services.publication_lifecycle import is_sent_pick_row
except Exception:
    def is_sent_pick_row(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        return str(row.get("telegram_sent") or "").strip().lower() in {"1", "true", "yes", "on"}



def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except Exception:
        return default


def env_set(name: str, default: str) -> set[str]:
    raw = os.getenv(name, default)
    return {item.strip().lower() for item in str(raw).split(",") if item.strip()}


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def payload_timestamp(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("created_at_utc"),
        payload.get("created_at"),
        payload.get("reference_run_utc"),
        payload.get("updated_at"),
    ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    candidates.extend([
        summary.get("current_time_utc"),
        summary.get("started_time_utc"),
        summary.get("current_time_local"),
        summary.get("started_time_local"),
    ])
    for value in candidates:
        dt = parse_dt(value)
        if dt is not None:
            return dt
    return None


def newest_timestamp(*payloads: Any) -> datetime | None:
    timestamps = [payload_timestamp(payload) for payload in payloads if isinstance(payload, dict)]
    timestamps = [item for item in timestamps if item is not None]
    return max(timestamps) if timestamps else None


def is_payload_fresh(payload: Any, reference: datetime | None, max_minutes: int | None = None) -> bool:
    if max_minutes is None:
        max_minutes = env_int("CONTROLLED_FALLBACK_ARTIFACT_FRESHNESS_MINUTES", 90)
    if not isinstance(payload, dict) or not payload:
        return False
    ts = payload_timestamp(payload)
    if ts is None:
        return False
    if reference is None:
        return ts >= datetime.now(UTC) - timedelta(minutes=max_minutes)
    return abs((reference - ts).total_seconds()) <= max_minutes * 60


_CONTEXT_SOURCE_INDEX_CACHE: dict[str, Any] | None = None


def load_context_source_index() -> dict[str, Any]:
    """Independent context confirmations keyed by match_key."""
    global _CONTEXT_SOURCE_INDEX_CACHE
    if _CONTEXT_SOURCE_INDEX_CACHE is not None:
        return _CONTEXT_SOURCE_INDEX_CACHE

    paths = [
        Path(".data/exports/latest-context-source-index.json"),
        Path(".data/provider_cache/context-source-index/latest.json"),
    ]
    if not any(path.exists() for path in paths):
        try:
            import importlib.util

            builder_path = Path("scripts/build_context_source_index.py")
            if not builder_path.exists():
                builder_path = Path(__file__).resolve().with_name("build_context_source_index.py")
            if builder_path.exists():
                spec = importlib.util.spec_from_file_location("harizon_context_source_index_builder", builder_path)
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    build_main = getattr(module, "main", None)
                    if callable(build_main):
                        build_main()
        except Exception:
            pass

    for path in paths:
        payload = load_json(path, {})
        if isinstance(payload, dict) and isinstance(payload.get("by_match"), dict):
            _CONTEXT_SOURCE_INDEX_CACHE = payload
            return payload
    _CONTEXT_SOURCE_INDEX_CACHE = {}
    return _CONTEXT_SOURCE_INDEX_CACHE


def normalize_confirmation_source(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    aliases = {
        "sstats": "sstats",
        "sstats_direct": "sstats",
        "sstats_form": "sstats",
        "bzzoiro": "bzzoiro",
        "bzzoiro_event_odds": "bzzoiro",
        "weather": "weather",
        "weatherapi": "weather",
        "openweathermap": "weather",
        "openmeteo": "weather",
        "open_meteo": "weather",
        "meteostat": "weather",
        "football_data": "football_data",
        "football_data_org": "football_data",
        "thesportsdb": "thesportsdb",
        "espn": "espn",
        "futrixmetrics": "futrixmetrics",
        "gnews": "gnews",
        "newsapi": "newsapi",
        "currents": "newsapi",
        "sportlogic": "sportlogic",
        "scorebat": "scorebat",
        "openfootball": "openfootball",
        "clubelo": "clubelo",
        "wikidata": "wikidata",
        "guardian": "guardian",
        "highlightly": "highlightly",
    }
    if text in aliases:
        return aliases[text]
    for needle, canonical in aliases.items():
        if needle in text:
            return canonical
    return None


def _source_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return re.split(r"[,+;/|\s]+", value)
    return []


def weather_confirmation_state(candidate: dict[str, Any]) -> dict[str, Any]:
    family = str(candidate.get("family") or "").strip().lower()
    details: dict[str, Any] = {}
    for container_key in ("weather", "weather_context", "context", "details", "metadata"):
        value = candidate.get(container_key)
        if isinstance(value, dict):
            details.update(value)
    for container in (candidate.get("context") or {}, candidate.get("details") or {}):
        if isinstance(container, dict):
            nested = container.get("weather") or container.get("weather_context")
            if isinstance(nested, dict):
                details.update(nested)

    text = json.dumps(details, ensure_ascii=False).lower() if details else ""
    supports_under = any(token in text for token in ("heavy_rain", "rain", "wind", "storm", "weather_supports_under", "under_weather"))
    supports_over = any(token in text for token in ("fast_pitch", "clear_weather", "weather_supports_over", "over_weather"))
    risk_flag = any(token in text for token in ("risk", "postpon", "storm", "extreme", "weather_risk"))
    relevant = family in {"totals", "teamtotals", "btts"} and (supports_under or supports_over or risk_flag)
    return {
        "weather_supports_under": supports_under,
        "weather_supports_over": supports_over,
        "weather_neutral": not relevant,
        "weather_risk_flag": risk_flag,
        "weather_confirmation_relevant": relevant,
    }


def candidate_confirmation_sources(candidate: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    sources: set[str] = set()
    match_key = str(candidate.get("match_key") or "").strip().lower()
    index = load_context_source_index() if env_bool("CONTROLLED_FALLBACK_USE_CONTEXT_SOURCE_INDEX", True) else {}
    by_match = index.get("by_match") if isinstance(index, dict) else {}
    if match_key and isinstance(by_match, dict):
        indexed = by_match.get(match_key) or []
        if isinstance(indexed, list):
            for item in indexed:
                src = normalize_confirmation_source(item)
                if src:
                    sources.add(src)

    for field in ("confirmation_sources", "context_sources", "context_source_names", "merged_context_sources", "providers", "provider_names"):
        for item in _source_values(candidate.get(field)):
            src = normalize_confirmation_source(item)
            if src:
                sources.add(src)

    source_summary = candidate.get("source_summary") or {}
    if isinstance(source_summary, dict):
        for field in ("context_sources", "providers", "confirmation_sources"):
            for item in _source_values(source_summary.get(field)):
                src = normalize_confirmation_source(item)
                if src:
                    sources.add(src)

    sources.discard("odds_api_io")
    sources.discard("market")

    relevance = weather_confirmation_state(candidate)
    if "weather" in sources and not relevance["weather_confirmation_relevant"]:
        sources.discard("weather")
        relevance["weather_dropped_as_neutral"] = True
    else:
        relevance["weather_dropped_as_neutral"] = False
    return sorted(sources), relevance


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def effective_min_kickoff_lead_minutes() -> int:
    # Fixed publication policy: scan matches starting no sooner than MIN_KICKOFF_LEAD_MINUTES.
    # For this bundle the intended value is 30 minutes. Late/manual adaptive lead is disabled
    # unless explicitly re-enabled in env.
    base_lead = max(0, env_int("MIN_KICKOFF_LEAD_MINUTES", 30))
    if not env_bool("CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD", False):
        return base_lead
    if not env_bool("MANUAL_LATE_MODE_ENABLED", False):
        return base_lead
    adaptive = env_int("MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES", base_lead)
    late = env_int("MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES", base_lead)
    candidates = [base_lead]
    if adaptive > 0:
        candidates.append(adaptive)
    if late > 0:
        candidates.append(late)
    return max(0, min(candidates))


def quality_proxy_score(candidate: dict[str, Any], metrics: dict[str, Any], raw_quality: float) -> tuple[float, str]:
    if raw_quality > 0:
        return raw_quality, "raw"
    if not env_bool("CONTROLLED_FALLBACK_USE_QUALITY_PROXY", True):
        return raw_quality, "raw_missing"
    source = str(candidate.get("_candidate_source") or "").strip().lower()
    if source not in {"latest_rescue_candidates", "artifact_rescue_candidates", "debug_candidates_before_quality", "debug_candidates_after_quality"}:
        return raw_quality, "raw_missing"

    confidence = float(metrics.get("confidence") or 0.0)
    publication = float(metrics.get("publication_score") or 0.0)
    books = int(metrics.get("books_count") or 0)
    sources = int(metrics.get("sources_count") or 0)
    ev = max(0.0, float(metrics.get("canonical_ev_pct") or 0.0))
    edge = max(0.0, float(metrics.get("canonical_edge_pp") or 0.0))

    score = 38.0
    score += min(18.0, max(0.0, confidence - 50.0) * 0.75)
    score += min(12.0, max(0.0, publication) * 0.28)
    score += 5.0 if books >= 2 else 1.5 if books == 1 else 0.0
    score += 2.0 if sources >= 1 else 0.0
    score += min(10.0, ev * 1.05)
    score += min(7.0, edge * 1.35)

    cap = env_float("CONTROLLED_FALLBACK_PROXY_MAX_QUALITY", 76.0)
    score = max(0.0, min(cap, score))
    return round(score, 3), "proxy"



def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def telegram_match_tz() -> Any:
    name = (
        os.getenv("TELEGRAM_MATCH_TIMEZONE")
        or os.getenv("APP_TIMEZONE")
        or os.getenv("TZ")
        or "Europe/Moscow"
    )
    try:
        return ZoneInfo(str(name))
    except Exception:
        return UTC


def timezone_label(dt: datetime) -> str:
    raw = dt.tzname()
    if raw:
        return str(raw)
    name = str(os.getenv("TELEGRAM_MATCH_TIMEZONE") or os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "UTC")
    if name == "Europe/Moscow":
        return "MSK"
    return name


def format_match_time_for_telegram(value: Any) -> str:
    kickoff = parse_dt(value)
    if kickoff is None:
        return str(value or "н/д")
    local = kickoff.astimezone(telegram_match_tz())
    label = timezone_label(local)
    text = f"{local.strftime('%d.%m.%Y %H:%M')} {label}"
    if env_bool("TELEGRAM_SHOW_UTC_MATCH_TIME", False):
        text += f" / UTC {kickoff.strftime('%d.%m.%Y %H:%M')}"
    return text



def poisson_cdf(k: int, lam: float) -> float:
    if lam < 0:
        return 0.0
    if k < 0:
        return 0.0
    term = math.exp(-lam)
    total = term
    for i in range(1, int(k) + 1):
        term *= lam / i
        total += term
    return max(0.0, min(1.0, total))


def total_line_probability_from_xg(selection: Any, point: Any, expected_home: Any, expected_away: Any) -> float | None:
    try:
        line = float(point)
        home = float(expected_home)
        away = float(expected_away)
    except Exception:
        return None
    if line <= 0 or home < 0 or away < 0:
        return None

    total_xg = home + away
    selection_text = str(selection or "").lower()
    is_over = any(token in selection_text for token in ("over", "больше", "тб"))
    is_under = any(token in selection_text for token in ("under", "меньше", "тм"))
    if not (is_over or is_under):
        return None

    frac = round(line - math.floor(line), 2)

    def over_prob(single_line: float) -> float:
        if abs(single_line - round(single_line)) < 1e-9:
            return 1.0 - poisson_cdf(int(round(single_line)), total_xg)
        return 1.0 - poisson_cdf(int(math.floor(single_line)), total_xg)

    def under_prob(single_line: float) -> float:
        if abs(single_line - round(single_line)) < 1e-9:
            return poisson_cdf(int(round(single_line)) - 1, total_xg)
        return poisson_cdf(int(math.floor(single_line)), total_xg)

    if frac in {0.25, 0.75}:
        low = math.floor(line) if frac == 0.25 else math.floor(line) + 0.5
        high = math.floor(line) + 0.5 if frac == 0.25 else math.floor(line) + 1.0
        probability = (over_prob(low) + over_prob(high)) / 2.0 if is_over else (under_prob(low) + under_prob(high)) / 2.0
    else:
        probability = over_prob(line) if is_over else under_prob(line)

    return max(0.0, min(1.0, probability))


def xg_sanity_metrics(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
    if not env_bool("CONTROLLED_FALLBACK_XG_SANITY_ENABLED", True):
        return {"enabled": False}

    fam = family_norm(candidate)
    if fam not in {"totals", "teamtotals"}:
        return {"enabled": False, "reason": "family_not_total"}

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    if expected_home in (None, "") or expected_away in (None, ""):
        return {"enabled": False, "reason": "missing_xg"}

    probability = total_line_probability_from_xg(
        candidate.get("selection") or "",
        candidate.get("point"),
        expected_home,
        expected_away,
    )
    if probability is None:
        return {"enabled": False, "reason": "unsupported_total_selection"}

    try:
        total_xg = float(expected_home) + float(expected_away)
        line = float(candidate.get("point"))
    except Exception:
        total_xg = None
        line = None

    gap_pp = (float(adjusted_probability) - float(probability)) * 100.0
    abs_gap_pp = abs(gap_pp)
    optimism_gap_pp = max(0.0, gap_pp)
    conservative_gap_pp = max(0.0, -gap_pp)
    selection_text = str(candidate.get("selection") or "").lower()
    is_over = any(token in selection_text for token in ("over", "больше", "тб"))
    is_under = any(token in selection_text for token in ("under", "меньше", "тм"))

    direction_ok = True
    margin = env_float("CONTROLLED_FALLBACK_XG_DIRECTION_MARGIN", 0.18)
    if total_xg is not None and line is not None:
        if is_over and total_xg < line - margin:
            direction_ok = False
        if is_under and total_xg > line + margin:
            direction_ok = False

    return {
        "enabled": True,
        "xg_probability": round(probability, 6),
        "xg_probability_pct": round(probability * 100.0, 3),
        "xg_model_gap_pp": round(gap_pp, 3),
        "xg_abs_gap_pp": round(abs_gap_pp, 3),
        "xg_model_optimism_gap_pp": round(optimism_gap_pp, 3),
        "xg_model_conservative_gap_pp": round(conservative_gap_pp, 3),
        "xg_total": round(total_xg, 3) if total_xg is not None else None,
        "xg_direction_ok": direction_ok,
    }




def btts_probability_from_xg(selection: Any, expected_home: Any, expected_away: Any) -> float | None:
    try:
        home = float(expected_home)
        away = float(expected_away)
    except Exception:
        return None
    if home < 0 or away < 0:
        return None

    yes_probability = (1.0 - math.exp(-home)) * (1.0 - math.exp(-away))
    selection_text = str(selection or "").lower()
    is_yes = any(token in selection_text for token in ("yes", "да", "btts yes", "обе забьют: да"))
    is_no = any(token in selection_text for token in ("no", "нет", "btts no", "обе забьют: нет"))
    if is_yes:
        return max(0.0, min(1.0, yes_probability))
    if is_no:
        return max(0.0, min(1.0, 1.0 - yes_probability))
    return None


def btts_sanity_metrics(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
    if not env_bool("CONTROLLED_FALLBACK_BTTS_SANITY_ENABLED", True):
        return {"enabled": False}
    if family_norm(candidate) != "btts":
        return {"enabled": False, "reason": "family_not_btts"}

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    if expected_home in (None, "") or expected_away in (None, ""):
        return {"enabled": False, "reason": "missing_xg"}

    probability = btts_probability_from_xg(candidate.get("selection") or "", expected_home, expected_away)
    if probability is None:
        return {"enabled": False, "reason": "unsupported_btts_selection"}

    try:
        home = float(expected_home)
        away = float(expected_away)
        yes_probability = (1.0 - math.exp(-home)) * (1.0 - math.exp(-away))
    except Exception:
        home = away = yes_probability = None

    gap_pp = (float(adjusted_probability) - float(probability)) * 100.0
    abs_gap_pp = abs(gap_pp)

    selection_text = str(candidate.get("selection") or "").lower()
    is_yes = any(token in selection_text for token in ("yes", "да", "обе забьют: да"))
    is_no = any(token in selection_text for token in ("no", "нет", "обе забьют: нет"))

    direction_ok = True
    # Very simple xG sanity: BTTS Yes needs both teams' xG not too low; BTTS No is suspicious if both xG are clearly high.
    low_team_threshold = env_float("CONTROLLED_FALLBACK_BTTS_LOW_TEAM_XG_THRESHOLD", 0.72)
    high_team_threshold = env_float("CONTROLLED_FALLBACK_BTTS_HIGH_TEAM_XG_THRESHOLD", 1.15)
    if home is not None and away is not None:
        if is_yes and min(home, away) < low_team_threshold:
            direction_ok = False
        if is_no and min(home, away) > high_team_threshold:
            direction_ok = False

    return {
        "enabled": True,
        "btts_probability": round(probability, 6),
        "btts_probability_pct": round(probability * 100.0, 3),
        "btts_yes_probability_pct": round(float(yes_probability or 0.0) * 100.0, 3),
        "btts_model_gap_pp": round(gap_pp, 3),
        "btts_abs_gap_pp": round(abs_gap_pp, 3),
        "btts_direction_ok": direction_ok,
    }




def poisson_goal_probs(lam: float, max_goals: int = 12) -> list[float]:
    if lam < 0:
        return []
    probs = [math.exp(-lam)]
    for goal in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / goal)
    return probs


def dnb_probability_from_xg(selection: Any, home_team: Any, away_team: Any, expected_home: Any, expected_away: Any) -> dict[str, Any] | None:
    try:
        home_xg = float(expected_home)
        away_xg = float(expected_away)
    except Exception:
        return None
    if home_xg < 0 or away_xg < 0:
        return None

    home_probs = poisson_goal_probs(home_xg, 14)
    away_probs = poisson_goal_probs(away_xg, 14)
    if not home_probs or not away_probs:
        return None

    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    for hg, hp in enumerate(home_probs):
        for ag, ap in enumerate(away_probs):
            p = hp * ap
            if hg > ag:
                p_home += p
            elif hg == ag:
                p_draw += p
            else:
                p_away += p

    selection_text = str(selection or "").lower()
    home_text = str(home_team or "").lower()
    away_text = str(away_team or "").lower()

    # Detect side. If team name is unavailable, use side/key hints if present in selection.
    is_home = bool(home_text and home_text in selection_text)
    is_away = bool(away_text and away_text in selection_text)
    if not is_home and not is_away:
        # Last-resort heuristic for common side markers.
        if any(token in selection_text for token in ("home", "хозя", "п1", "1 (0)")):
            is_home = True
        if any(token in selection_text for token in ("away", "гост", "п2", "2 (0)")):
            is_away = True

    if is_home:
        p_win = p_home
        p_loss = p_away
        side = "home"
    elif is_away:
        p_win = p_away
        p_loss = p_home
        side = "away"
    else:
        return None

    no_push_probability = p_win / max(1e-9, (p_win + p_loss))
    return {
        "side": side,
        "p_win": round(p_win, 6),
        "p_draw": round(p_draw, 6),
        "p_loss": round(p_loss, 6),
        "dnb_no_push_probability": round(no_push_probability, 6),
        "dnb_no_push_probability_pct": round(no_push_probability * 100.0, 3),
        "dnb_fair_odds_no_push": round(1.0 / max(1e-9, no_push_probability), 4),
    }


def dnb_sanity_metrics(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
    if not env_bool("CONTROLLED_FALLBACK_DNB_SANITY_ENABLED", True):
        return {"enabled": False}
    if family_norm(candidate) != "dnb":
        return {"enabled": False, "reason": "family_not_dnb"}

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    if expected_home in (None, "") or expected_away in (None, ""):
        return {"enabled": False, "reason": "missing_xg"}

    payload = dnb_probability_from_xg(
        candidate.get("selection") or "",
        candidate.get("home_team") or "",
        candidate.get("away_team") or "",
        expected_home,
        expected_away,
    )
    if payload is None:
        return {"enabled": False, "reason": "unsupported_dnb_selection"}

    xg_prob = float(payload["dnb_no_push_probability"])
    gap_pp = (float(adjusted_probability) - xg_prob) * 100.0
    abs_gap_pp = abs(gap_pp)

    odds = as_float(candidate.get("odds"), 0.0)
    selected_implied = 1.0 / odds if odds > 1.0 else 0.0
    dnb_xg_no_push_edge_pp = (xg_prob - selected_implied) * 100.0 if odds > 1.0 else -999.0
    dnb_xg_ev_no_push_pct = ((xg_prob * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0
    dnb_xg_ev_unconditional_pct = (
        (float(payload.get("p_win") or 0.0) * (odds - 1.0) - float(payload.get("p_loss") or 0.0)) * 100.0
        if odds > 1.0
        else -999.0
    )

    # For DNB, if xG no-push probability is much higher than model adjusted probability,
    # it is not a conflict; the model is just conservative. Conflict only when model is
    # materially more optimistic than xG.
    optimism_gap_pp = max(0.0, gap_pp)
    direction_ok = optimism_gap_pp <= env_float("CONTROLLED_FALLBACK_DNB_MAX_MODEL_OPTIMISM_GAP_PP", 12.0)

    payload.update({
        "enabled": True,
        "dnb_model_gap_pp": round(gap_pp, 3),
        "dnb_abs_gap_pp": round(abs_gap_pp, 3),
        "dnb_model_optimism_gap_pp": round(optimism_gap_pp, 3),
        "dnb_selected_implied_probability": round(selected_implied, 6),
        "dnb_xg_no_push_edge_pp": round(dnb_xg_no_push_edge_pp, 3),
        "dnb_xg_ev_no_push_pct": round(dnb_xg_ev_no_push_pct, 3),
        "dnb_xg_ev_unconditional_pct": round(dnb_xg_ev_unconditional_pct, 3),
        "dnb_direction_ok": direction_ok,
    })
    return payload



def quality_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    diag = candidate.get("diagnostics") or {}
    q = diag.get("quality") if isinstance(diag, dict) else None
    return q if isinstance(q, dict) else {}


def quality_reasons(candidate: dict[str, Any]) -> list[str]:
    q = quality_payload(candidate)
    reasons = q.get("reasons") or candidate.get("quality_reasons") or []
    if isinstance(reasons, list):
        return [str(item) for item in reasons if str(item).strip()]
    if isinstance(reasons, str) and reasons.strip():
        return [reasons.strip()]
    return []


def selected_bookmaker(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(candidate.get("bookmaker") or ss.get("selected_bookmaker") or ss.get("bookmaker") or "").strip()


def selected_source(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(ss.get("selected_source") or ss.get("source") or "").strip()


def family_norm(candidate: dict[str, Any]) -> str:
    return str(candidate.get("family") or "").strip().lower()


def dedupe_key(candidate: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(candidate.get("match_key") or ""),
            str(candidate.get("family") or "").lower(),
            str(candidate.get("selection") or "").lower(),
            str(candidate.get("selection_key") or "").lower(),
            str(candidate.get("point") or ""),
            str(candidate.get("team_side") or "").lower(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_sent_index() -> dict[str, Any]:
    payload = load_json(".data/fallback-sent-index.json", {})
    return payload if isinstance(payload, dict) else {}


def save_sent_index(index: dict[str, Any]) -> None:
    write_json(".data/fallback-sent-index.json", index)


def prune_sent_index(index: dict[str, Any], hours: int) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, hours))
    result: dict[str, Any] = {}
    for key, row in index.items():
        if not isinstance(row, dict):
            continue
        ts = parse_dt(row.get("sent_at"))
        if ts is not None and ts >= cutoff:
            result[key] = row
    return result


def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:
    key = dedupe_key(candidate)
    if key in sent_index:
        return "duplicate_fallback_sent_index"
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return None
    collections: list[str] = []
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_BETS", True):
        collections.append("bets")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED", True):
        collections.append("published_candidates")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW", False):
        collections.append("shadow_bets")
    for collection in collections:
        rows = state.get(collection) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and dedupe_key(row) == key and is_sent_pick_row(row):
                return f"duplicate_state:{collection}"
    return None



def _dict_at_path(root: dict[str, Any], *path: str) -> dict[str, Any]:
    current: Any = root
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def publish_coverage_contract(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical publication coverage contract for this candidate.

    This is the source of truth for line-source counts.  Context providers such
    as SStats/ClubElo/Weather must never inflate odds_sources_count.
    """
    diag = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    direct = diag.get("publish_coverage_contract") if isinstance(diag.get("publish_coverage_contract"), dict) else {}
    if direct:
        return direct
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    direct = summary.get("publish_coverage_contract") if isinstance(summary.get("publish_coverage_contract"), dict) else {}
    return direct if isinstance(direct, dict) else {}


def api_coverage_consensus(candidate: dict[str, Any]) -> dict[str, Any]:
    diag = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    direct = diag.get("api_coverage_consensus") if isinstance(diag.get("api_coverage_consensus"), dict) else {}
    if direct:
        return direct
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    direct = summary.get("api_coverage_consensus") if isinstance(summary.get("api_coverage_consensus"), dict) else {}
    if direct:
        return direct
    # Older candidate rows flatten the consensus fields into source_summary.
    keys = {
        "exact_books", "exact_books_count", "exact_odds_sources", "exact_odds_sources_count",
        "exact_offers_count", "exact_source_prices", "source_price_dispersion_pct",
        "consensus_price_avg", "consensus_price_median",
    }
    return {key: summary.get(key) for key in keys if key in summary}


def _clean_source_list(values: Any, *, exclude_context: bool = False) -> list[str]:
    out: list[str] = []
    blocked = {"sstats", "sstats_form", "clubelo", "weather", "open_meteo", "weatherapi", "context_equiv_supplemental", "model_xg", "ensemble", "market"}
    for value in _source_values(values):
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not text:
            continue
        if exclude_context and text in blocked:
            continue
        if text not in out:
            out.append(text)
    return out


def odds_source_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    contract = publish_coverage_contract(candidate)
    consensus = api_coverage_consensus(candidate)
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}

    contract_sources = _clean_source_list(contract.get("odds_sources"), exclude_context=True)
    summary_sources = _clean_source_list(summary.get("odds_sources"), exclude_context=True)
    # For Telegram and tiering use the exact publication contract first.  A
    # broader source_summary.line_sources may include provider-level hints that
    # did not actually provide the selected market/line/side price.
    line_sources = contract_sources or summary_sources
    if not line_sources:
        line_sources = _clean_source_list(summary.get("line_sources"), exclude_context=True)

    provider_count = as_int(contract.get("odds_sources_count"), -1)
    if provider_count < 0:
        provider_count = as_int(summary.get("odds_sources_count"), 0)
    if provider_count <= 0:
        provider_count = len(contract_sources or summary_sources or line_sources)

    # Account-level exact odds sources are useful as price confirmations, but
    # they are not independent provider sources.  Example: odds_api_io account1
    # and account2 are bookmaker diversity, not two provider APIs.
    exact_account_sources = _clean_source_list(consensus.get("exact_odds_sources"), exclude_context=True)
    exact_accounts_count = as_int(consensus.get("exact_odds_sources_count"), len(exact_account_sources))
    exact_books = _clean_source_list(consensus.get("exact_books") or consensus.get("exact_line_bookmakers"), exclude_context=False)
    exact_books_count = as_int(consensus.get("exact_books_count"), len(exact_books))

    return {
        "odds_sources": line_sources or contract_sources or summary_sources,
        "odds_sources_count": max(0, provider_count),
        "exact_account_sources": exact_account_sources,
        "exact_account_sources_count": max(0, exact_accounts_count),
        "exact_books": exact_books,
        "exact_books_count": max(0, exact_books_count),
        "publish_coverage_passed": bool(summary.get("publish_coverage_passed")) or not bool(summary.get("publish_coverage_reasons")),
        "publish_coverage_reasons": summary.get("publish_coverage_reasons") or [],
    }


def context_source_metrics(candidate: dict[str, Any], fallback_sources: list[str]) -> dict[str, Any]:
    contract = publish_coverage_contract(candidate)
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    sources = _clean_source_list(contract.get("context_sources") or summary.get("context_sources") or fallback_sources, exclude_context=False)
    # odds_api_io is a line provider and should not be shown as context confirmation.
    sources = [src for src in sources if src not in {"odds_api_io", "market", "context_equiv_supplemental", "model_xg"}]
    return {
        "context_sources": sources,
        "context_sources_count": len(sources),
    }

def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    coverage = odds_source_metrics(candidate)
    confirmation_sources, confirmation_meta = candidate_confirmation_sources(candidate)
    context_coverage = context_source_metrics(candidate, confirmation_sources)
    books = max(as_int(candidate.get("books_count"), 0), int(coverage.get("exact_books_count") or 0))
    odds_sources = int(coverage.get("odds_sources_count") or 0)
    confirmation_sources = list(context_coverage.get("context_sources") or confirmation_sources)
    confirmation_sources_count = int(context_coverage.get("context_sources_count") or len(confirmation_sources))
    sources = confirmation_sources_count
    q = quality_payload(candidate)
    raw_quality_score = as_float(q.get("quality_score"), as_float(candidate.get("quality_score"), 0.0))
    publication_score = as_float(candidate.get("publication_score"), 0.0)
    selected_implied = 1.0 / odds if odds > 1.0 else 0.0
    canonical_edge_pp = (adjusted - selected_implied) * 100.0 if odds > 1.0 else -999.0
    canonical_ev_pct = ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0
    market_edge_pp = (adjusted - market_prob) * 100.0 if market_prob > 0 else 0.0

    seed_metrics = {
        "confidence": confidence,
        "publication_score": publication_score,
        "books_count": books,
        "odds_sources_count": odds_sources,
        "sources_count": sources,
        "confirmation_sources_count": confirmation_sources_count,
        "confirmation_sources": confirmation_sources,
        "confirmation_meta": confirmation_meta,
        "line_sources": coverage.get("odds_sources") or [],
        "exact_account_sources": coverage.get("exact_account_sources") or [],
        "exact_account_sources_count": int(coverage.get("exact_account_sources_count") or 0),
        "publish_coverage_passed": bool(coverage.get("publish_coverage_passed")),
        "publish_coverage_reasons": coverage.get("publish_coverage_reasons") or [],
        "canonical_edge_pp": canonical_edge_pp,
        "canonical_ev_pct": canonical_ev_pct,
    }
    quality_score, quality_score_source = quality_proxy_score(candidate, seed_metrics, raw_quality_score)
    xg_sanity = xg_sanity_metrics(candidate, adjusted)
    btts_sanity = btts_sanity_metrics(candidate, adjusted)
    dnb_sanity = dnb_sanity_metrics(candidate, adjusted)

    return {
        "odds": round(odds, 4),
        "selected_implied_probability": round(selected_implied, 6),
        "adjusted_probability": round(adjusted, 6),
        "model_probability": round(model_prob, 6),
        "market_probability": round(market_prob, 6),
        "canonical_edge_pp": round(canonical_edge_pp, 3),
        "market_edge_pp": round(market_edge_pp, 3),
        "canonical_ev_pct": round(canonical_ev_pct, 3),
        "confidence": round(confidence, 3),
        "quality_score": round(quality_score, 3),
        "quality_score_raw": round(raw_quality_score, 3),
        "quality_score_source": quality_score_source,
        "publication_score": round(publication_score, 3),
        "books_count": books,
        "odds_sources_count": odds_sources,
        "sources_count": sources,
        "confirmation_sources_count": confirmation_sources_count,
        "confirmation_sources": confirmation_sources,
        "confirmation_meta": confirmation_meta,
        "line_sources": coverage.get("odds_sources") or [],
        "exact_account_sources": coverage.get("exact_account_sources") or [],
        "exact_account_sources_count": int(coverage.get("exact_account_sources_count") or 0),
        "publish_coverage_passed": bool(coverage.get("publish_coverage_passed")),
        "publish_coverage_reasons": coverage.get("publish_coverage_reasons") or [],
        "quality_reasons": quality_reasons(candidate),
        "xg_sanity": xg_sanity,
        "btts_sanity": btts_sanity,
        "dnb_sanity": dnb_sanity,
    }


def kickoff_window_reasons(candidate: dict[str, Any]) -> list[str]:
    if not env_bool("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", True):
        return []
    kickoff = parse_dt(candidate.get("commence_time") or candidate.get("start_time") or candidate.get("kickoff"))
    if kickoff is None:
        return [] if env_bool("CONTROLLED_FALLBACK_ALLOW_UNKNOWN_TIME", False) else ["missing_commence_time"]
    now = datetime.now(UTC)
    min_lead = effective_min_kickoff_lead_minutes()
    window_hours = max(1, env_int("PUBLISH_WINDOW_HOURS", 24))
    earliest = now + timedelta(minutes=min_lead)
    latest = now + timedelta(hours=window_hours)
    if kickoff < now:
        return ["match_already_started"]
    if kickoff < earliest:
        return ["match_time_outside_window"]
    if kickoff > latest:
        return ["match_time_too_late"]
    return []


def hard_reject_reasons(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fam = family_norm(candidate)
    if fam not in env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,teamtotals,btts"):
        reasons.append(f"family_not_allowed:{fam}")
    reasons.extend(kickoff_window_reasons(candidate))
    dup = duplicate_reason(candidate, sent_index)
    if dup:
        reasons.append(dup)
    if metrics["odds"] < env_float("CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS", 1.55):
        reasons.append("odds_below_global_min")
    if metrics["odds"] > env_float("CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS", 3.05):
        reasons.append("odds_above_global_max")
    if metrics["canonical_edge_pp"] <= 0 or metrics["canonical_ev_pct"] <= 0:
        reasons.append("canonical_negative_value")
    if metrics["adjusted_probability"] <= 0 or metrics["adjusted_probability"] >= 0.98:
        reasons.append("bad_adjusted_probability")
    if metrics["books_count"] <= 0:
        reasons.append("missing_books")
    if metrics["sources_count"] <= 0:
        reasons.append("missing_sources")
    if fam == "h2h" and metrics["odds"] > 2.25:
        reasons.append("h2h_rescue_odds_too_high")

    xg = metrics.get("xg_sanity") or {}
    if bool(xg.get("enabled")):
        if not bool(xg.get("xg_direction_ok", True)):
            reasons.append("xg_direction_conflict")
        hard_gap = env_float("CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP", 14.0)
        optimism_gap = float(xg.get("xg_model_optimism_gap_pp") or max(0.0, float(xg.get("xg_model_gap_pp") or 0.0)))
        if optimism_gap > hard_gap:
            reasons.append("xg_probability_gap_hard_reject")

    btts = metrics.get("btts_sanity") or {}
    if bool(btts.get("enabled")):
        if not bool(btts.get("btts_direction_ok", True)):
            reasons.append("btts_direction_conflict")
        hard_gap = env_float("CONTROLLED_FALLBACK_BTTS_HARD_REJECT_GAP_PP", 16.0)
        if float(btts.get("btts_abs_gap_pp") or 0.0) > hard_gap:
            reasons.append("btts_probability_gap_hard_reject")

    dnb = metrics.get("dnb_sanity") or {}
    if bool(dnb.get("enabled")):
        if not bool(dnb.get("dnb_direction_ok", True)):
            reasons.append("dnb_direction_conflict")
        hard_gap = env_float("CONTROLLED_FALLBACK_DNB_HARD_REJECT_OPTIMISM_GAP_PP", 16.0)
        if float(dnb.get("dnb_model_optimism_gap_pp") or 0.0) > hard_gap:
            reasons.append("dnb_probability_gap_hard_reject")
    return reasons


def tier_reasons(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fam = family_norm(candidate)
    prefix = f"CONTROLLED_FALLBACK_TIER_{tier}_"
    allowed_families = env_set(prefix + "ALLOWED_FAMILIES", "")
    if allowed_families and fam not in allowed_families:
        reasons.append(f"tier_{tier.lower()}_family_not_allowed:{fam}")
    min_odds_sources = env_int(prefix + "MIN_ODDS_SOURCES", 2 if tier == "A" else 1)
    if int(metrics.get("odds_sources_count") or 0) < min_odds_sources:
        reasons.append(f"tier_{tier.lower()}_odds_sources_below_min:{int(metrics.get('odds_sources_count') or 0)}/{min_odds_sources}")
    if tier == "B":
        min_context_sources = env_int(prefix + "MIN_CONTEXT_SOURCES", 3)
        if int(metrics.get("confirmation_sources_count") or metrics.get("sources_count") or 0) < min_context_sources:
            reasons.append(f"tier_b_context_sources_below_min:{int(metrics.get('confirmation_sources_count') or metrics.get('sources_count') or 0)}/{min_context_sources}")
    if metrics["books_count"] < env_int(prefix + "MIN_BOOKS", 2):
        reasons.append(f"tier_{tier.lower()}_books_below_min")
    if metrics["confidence"] < env_float(prefix + "MIN_CONFIDENCE", 60.0):
        reasons.append(f"tier_{tier.lower()}_confidence_below_min")
    if metrics["quality_score"] < env_float(prefix + "MIN_QUALITY", 60.0):
        reasons.append(f"tier_{tier.lower()}_quality_below_min")
    if metrics["canonical_edge_pp"] < env_float(prefix + "MIN_EDGE_PP", 2.0):
        reasons.append(f"tier_{tier.lower()}_canonical_edge_below_min")
    if metrics["canonical_ev_pct"] < env_float(prefix + "MIN_EV_PCT", 3.0):
        reasons.append(f"tier_{tier.lower()}_canonical_ev_below_min")
    if tier == "B" and fam == "dnb":
        if metrics["canonical_edge_pp"] < env_float("CONTROLLED_FALLBACK_TIER_B_DNB_MIN_EDGE_PP", 2.5):
            reasons.append("tier_b_dnb_edge_below_min")
        if metrics["canonical_ev_pct"] < env_float("CONTROLLED_FALLBACK_TIER_B_DNB_MIN_EV_PCT", 5.5):
            reasons.append("tier_b_dnb_ev_below_min")
    if tier == "B" and fam == "btts":
        if metrics["canonical_edge_pp"] < env_float("CONTROLLED_FALLBACK_TIER_B_BTTS_MIN_EDGE_PP", 2.5):
            reasons.append("tier_b_btts_edge_below_min")
        if metrics["canonical_ev_pct"] < env_float("CONTROLLED_FALLBACK_TIER_B_BTTS_MIN_EV_PCT", 5.5):
            reasons.append("tier_b_btts_ev_below_min")
    if metrics["publication_score"] < env_float(prefix + "MIN_PUBLICATION_SCORE", 20.0):
        reasons.append(f"tier_{tier.lower()}_publication_score_below_min")
    if metrics["odds"] > env_float(prefix + "MAX_ODDS", env_float("CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS", 3.05)):
        reasons.append(f"tier_{tier.lower()}_odds_above_max")

    # Proxy quality is useful for reserve ranking, but it must not create a false Tier A signal.
    if tier == "A" and env_bool("CONTROLLED_FALLBACK_TIER_A_REQUIRE_RAW_QUALITY", True):
        if str(metrics.get("quality_score_source") or "") == "proxy":
            reasons.append("tier_a_proxy_quality_not_allowed")

    xg = metrics.get("xg_sanity") or {}
    if bool(xg.get("enabled")):
        gap = float(xg.get("xg_model_optimism_gap_pp") or max(0.0, float(xg.get("xg_model_gap_pp") or 0.0)))
        max_gap = env_float(prefix + "MAX_XG_GAP_PP", 999.0)
        if gap > max_gap:
            reasons.append(f"tier_{tier.lower()}_xg_gap_above_max")
        if tier in {"A", "B"} and not bool(xg.get("xg_direction_ok", True)):
            reasons.append(f"tier_{tier.lower()}_xg_direction_conflict")
        if tier == "A" and env_bool("CONTROLLED_FALLBACK_TIER_A_REQUIRE_XG_SANITY", True):
            if gap > env_float("CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP", 6.5):
                reasons.append("tier_a_xg_confirmation_missing")

    btts = metrics.get("btts_sanity") or {}
    if bool(btts.get("enabled")):
        gap = float(btts.get("btts_abs_gap_pp") or 0.0)
        max_gap = env_float(prefix + "MAX_BTTS_GAP_PP", 999.0)
        if gap > max_gap:
            reasons.append(f"tier_{tier.lower()}_btts_gap_above_max")
        if tier in {"A", "B"} and not bool(btts.get("btts_direction_ok", True)):
            reasons.append(f"tier_{tier.lower()}_btts_direction_conflict")
        if tier == "A" and env_bool("CONTROLLED_FALLBACK_TIER_A_REQUIRE_BTTS_SANITY", True):
            if gap > env_float("CONTROLLED_FALLBACK_TIER_A_MAX_BTTS_GAP_PP", 7.0):
                reasons.append("tier_a_btts_confirmation_missing")

    dnb = metrics.get("dnb_sanity") or {}
    if bool(dnb.get("enabled")):
        optimism_gap = float(dnb.get("dnb_model_optimism_gap_pp") or 0.0)
        max_gap = env_float(prefix + "MAX_DNB_OPTIMISM_GAP_PP", 999.0)
        if optimism_gap > max_gap:
            reasons.append(f"tier_{tier.lower()}_dnb_gap_above_max")
        if tier in {"A", "B"} and not bool(dnb.get("dnb_direction_ok", True)):
            reasons.append(f"tier_{tier.lower()}_dnb_direction_conflict")
        if tier == "A" and env_bool("CONTROLLED_FALLBACK_TIER_A_REQUIRE_DNB_SANITY", True):
            if optimism_gap > env_float("CONTROLLED_FALLBACK_TIER_A_MAX_DNB_OPTIMISM_GAP_PP", 6.0):
                reasons.append("tier_a_dnb_confirmation_missing")

    q_reasons = [r.lower() for r in metrics.get("quality_reasons") or []]
    allowed_stops = env_set(prefix + "ALLOWED_QUALITY_STOPS", os.getenv("CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS", "bad_historical_segment_guard,no_bet_quality_score_guard,post_calibration_probability_guard,post_calibration_edge_guard"))
    if q_reasons and q_reasons[0] not in allowed_stops:
        reasons.append(f"tier_{tier.lower()}_quality_stop_not_allowed:{q_reasons[0]}")
    return reasons



def final_publish_guard_reasons(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> list[str]:
    """Final Telegram publication guard.

    Earlier tier checks decide whether a candidate is mathematically interesting.
    This guard decides whether it is good enough to be sent as a public Telegram forecast.
    """
    reasons: list[str] = []
    fam = family_norm(candidate)
    tier_name = tier.replace("уровень ", "").strip().upper()

    if env_bool("CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM", True):
        if int(metrics.get("books_count") or 0) < 2:
            reasons.append("telegram_publish_books_guard")

    odds_sources_count = int(metrics.get("odds_sources_count") or 0)
    min_public_odds_sources = env_int("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES_FOR_TELEGRAM", 2)
    hybrid_single_line_allowed = (
        env_bool("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED", True)
        and tier_name == "B"
        and odds_sources_count >= 1
        and int(metrics.get("books_count") or 0) >= env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", 2)
        and int(metrics.get("confirmation_sources_count") or metrics.get("sources_count") or 0) >= env_int("CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES", 3)
        and float(metrics.get("canonical_edge_pp") or 0.0) >= env_float("CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP", 4.0)
        and float(metrics.get("canonical_ev_pct") or 0.0) >= env_float("CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT", 7.0)
        and float(metrics.get("confidence") or 0.0) >= env_float("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE", 76.0)
    )
    if env_bool("CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM", True):
        if odds_sources_count < min_public_odds_sources and not hybrid_single_line_allowed:
            reasons.append(f"telegram_publish_odds_sources_guard:{odds_sources_count}/{min_public_odds_sources}")

    if env_bool("CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK", True):
        if int(metrics.get("books_count") or 0) < 2 and str(metrics.get("quality_score_source") or "") == "proxy":
            reasons.append("proxy_single_book_guard")

    if tier_name == "C" and not env_bool("CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED", False):
        reasons.append("tier_c_watch_only")

    if env_bool("CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY", True):
        if str(metrics.get("quality_score_source") or "") == "proxy" and int(metrics.get("books_count") or 0) < 2:
            reasons.append("proxy_without_market_confirmation")

    if env_bool("CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES", True):
        min_sources = env_int("CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES", 2)
        confirmation_count = int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0)
        if confirmation_count < min_sources:
            reasons.append(f"controlled_fallback_confirmation_sources_below_min:{confirmation_count}/{min_sources}")

    # If all prices come from one provider, proxy signals need stronger numeric confirmation.
    # This avoids "looks good but only one data pipeline" publications while still allowing
    # strong 2-book signals when there is clear EV, edge and confidence.
    if env_bool("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT", True):
        if str(metrics.get("quality_score_source") or "") == "proxy" and int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0) < 2:
            if float(metrics.get("canonical_edge_pp") or 0.0) < env_float("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP", 3.0):
                reasons.append("proxy_single_source_edge_below_min")
            if float(metrics.get("canonical_ev_pct") or 0.0) < env_float("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT", 7.0):
                reasons.append("proxy_single_source_ev_below_min")
            if float(metrics.get("confidence") or 0.0) < env_float("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE", 68.0):
                reasons.append("proxy_single_source_confidence_below_min")

    if fam in {"totals", "teamtotals"} and env_bool("CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM", True):
        xg = metrics.get("xg_sanity") or {}
        if not bool(xg.get("enabled")):
            reasons.append("missing_total_xg_sanity")
        elif not bool(xg.get("xg_direction_ok", True)):
            reasons.append("xg_direction_conflict")

    if fam == "btts" and env_bool("CONTROLLED_FALLBACK_REQUIRE_BTTS_SANITY_FOR_TELEGRAM", True):
        btts = metrics.get("btts_sanity") or {}
        if not bool(btts.get("enabled")):
            reasons.append("missing_btts_sanity")
        elif not bool(btts.get("btts_direction_ok", True)):
            reasons.append("btts_direction_conflict")

    if fam == "dnb" and env_bool("CONTROLLED_FALLBACK_REQUIRE_DNB_SANITY_FOR_TELEGRAM", True):
        dnb = metrics.get("dnb_sanity") or {}
        if not bool(dnb.get("enabled")):
            reasons.append("missing_dnb_sanity")
        elif not bool(dnb.get("dnb_direction_ok", True)):
            reasons.append("dnb_direction_conflict")
        else:
            min_xg_edge = env_float("CONTROLLED_FALLBACK_DNB_MIN_XG_EDGE_PP", 3.0)
            min_xg_ev = env_float("CONTROLLED_FALLBACK_DNB_MIN_XG_EV_UNCONDITIONAL_PCT", 4.0)
            if float(dnb.get("dnb_xg_no_push_edge_pp") or 0.0) < min_xg_edge:
                reasons.append("dnb_xg_edge_below_min")
            if float(dnb.get("dnb_xg_ev_unconditional_pct") or 0.0) < min_xg_ev:
                reasons.append("dnb_xg_ev_below_min")

            # Extreme xG-vs-market/model divergence is usually not a "free money" signal.
            # With proxy quality and one source it is more likely an xG/match mapping/outlier problem.
            if env_bool("CONTROLLED_FALLBACK_DNB_OUTLIER_GUARD_ENABLED", True):
                quality_source = str(metrics.get("quality_score_source") or "")
                single_source = int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0) < 2
                if quality_source == "proxy" and single_source:
                    abs_gap = abs(float(dnb.get("dnb_model_gap_pp") or 0.0))
                    xg_ev = float(dnb.get("dnb_xg_ev_unconditional_pct") or 0.0)
                    xg_edge = float(dnb.get("dnb_xg_no_push_edge_pp") or 0.0)
                    no_push = float(dnb.get("dnb_no_push_probability_pct") or 0.0)
                    if abs_gap > env_float("CONTROLLED_FALLBACK_DNB_MAX_ABS_MODEL_XG_GAP_PP", 30.0):
                        reasons.append("dnb_xg_model_gap_outlier")
                    if xg_ev > env_float("CONTROLLED_FALLBACK_DNB_MAX_XG_EV_UNCONDITIONAL_PCT", 75.0):
                        reasons.append("dnb_xg_ev_outlier")
                    if xg_edge > env_float("CONTROLLED_FALLBACK_DNB_MAX_XG_EDGE_PP", 35.0):
                        reasons.append("dnb_xg_edge_outlier")
                    if no_push > env_float("CONTROLLED_FALLBACK_DNB_MAX_NO_PUSH_PROBABILITY_PCT", 82.0):
                        reasons.append("dnb_xg_probability_outlier")

    min_edge = env_float("CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP", 1.8)
    min_ev = env_float("CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT", 4.0)
    if float(metrics.get("canonical_edge_pp") or 0.0) < min_edge:
        if not final_edge_tolerance_allowed(candidate, metrics, min_edge, min_ev):
            reasons.append("final_edge_below_min")
    if float(metrics.get("canonical_ev_pct") or 0.0) < min_ev:
        reasons.append("final_ev_below_min")

    return reasons


def final_edge_tolerance_allowed(candidate: dict[str, Any], metrics: dict[str, Any], min_edge: float, min_ev: float) -> bool:
    """Allow a tiny final-edge miss only when every safety signal is already strong.

    The fallback recalculates value against the selected price, so a candidate can miss
    the final edge threshold by a few basis points while still having strong EV,
    multi-book confirmation and clean xG/context checks. This helper keeps that
    tolerance explicit and auditable instead of lowering the global publication bar.
    """
    tolerance = max(0.0, env_float("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_PP", 0.15))
    if tolerance <= 0:
        return False
    edge = float(metrics.get("canonical_edge_pp") or 0.0)
    ev = float(metrics.get("canonical_ev_pct") or 0.0)
    if edge <= 0 or edge < (min_edge - tolerance):
        return False
    if ev < (min_ev + env_float("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_MIN_EV_BUFFER_PCT", 1.0)):
        return False
    if int(metrics.get("books_count") or 0) < env_int("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_MIN_BOOKS", 2):
        return False
    confirmation_count = int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0)
    if confirmation_count < env_int("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_MIN_CONFIRMATION_SOURCES", 2):
        return False
    if float(metrics.get("quality_score") or 0.0) < env_float("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_MIN_QUALITY", 75.0):
        return False
    if float(metrics.get("confidence") or 0.0) < env_float("CONTROLLED_FALLBACK_FINAL_EDGE_TOLERANCE_MIN_CONFIDENCE", 68.0):
        return False

    fam = family_norm(candidate)
    if fam in {"totals", "teamtotals"}:
        xg = metrics.get("xg_sanity") or {}
        if not bool(xg.get("enabled")) or not bool(xg.get("xg_direction_ok", True)):
            return False
    if fam == "btts":
        btts = metrics.get("btts_sanity") or {}
        if not bool(btts.get("enabled")) or not bool(btts.get("btts_direction_ok", True)):
            return False
    if fam == "dnb":
        dnb = metrics.get("dnb_sanity") or {}
        if not bool(dnb.get("enabled")) or not bool(dnb.get("dnb_direction_ok", True)):
            return False

    metrics["final_edge_tolerance_used"] = {
        "edge_pp": round(edge, 3),
        "min_edge_pp": round(min_edge, 3),
        "tolerance_pp": round(tolerance, 3),
    }
    return True


def watch_candidate_rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
    metrics = row.get("metrics") or {}
    return (
        float(metrics.get("canonical_ev_pct") or 0.0),
        float(metrics.get("canonical_edge_pp") or 0.0),
        float(metrics.get("quality_score") or 0.0),
        float(metrics.get("confidence") or 0.0),
    )


def build_watchlist(evaluated: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Positive-value candidates that were intentionally not published.

    This gives visibility without sending weak single-book/proxy picks as real forecasts.
    """
    max_items = limit if limit is not None else env_int("CONTROLLED_FALLBACK_WATCHLIST_MAX_ITEMS", 5)
    interesting_reasons = {
        "telegram_publish_books_guard",
        "proxy_single_book_guard",
        "proxy_without_market_confirmation",
        "tier_c_watch_only",
        "final_edge_below_min",
        "final_ev_below_min",
    }
    rows: list[dict[str, Any]] = []
    for row in evaluated:
        metrics = row.get("metrics") or {}
        if float(metrics.get("canonical_ev_pct") or 0.0) <= 0:
            continue
        if float(metrics.get("canonical_edge_pp") or 0.0) <= 0:
            continue
        reject_reasons = set(str(item) for item in row.get("reject_reasons") or [])
        if not (reject_reasons & interesting_reasons):
            continue
        rows.append(row)

    rows.sort(key=watch_candidate_rank, reverse=True)
    watchlist: list[dict[str, Any]] = []
    for row in rows[:max_items]:
        metrics = row.get("metrics") or {}
        watchlist.append({
            "match_key": row.get("match_key"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "home_team_ru": row.get("home_team_ru"),
            "away_team_ru": row.get("away_team_ru"),
            "league_name": row.get("league_name"),
            "league_name_ru": row.get("league_name_ru"),
            "family": row.get("family"),
            "selection": row.get("selection"),
            "point": row.get("point"),
            "commence_time": row.get("commence_time"),
            "commence_time_display": row.get("commence_time_display"),
            "reject_reasons": row.get("reject_reasons") or [],
            "reject_reasons_ru": row.get("reject_reasons_ru") or [],
            "metrics": {
                "odds": metrics.get("odds"),
                "canonical_edge_pp": metrics.get("canonical_edge_pp"),
                "canonical_ev_pct": metrics.get("canonical_ev_pct"),
                "confidence": metrics.get("confidence"),
                "quality_score": metrics.get("quality_score"),
                "quality_score_source": metrics.get("quality_score_source"),
                "books_count": metrics.get("books_count"),
                "sources_count": metrics.get("sources_count"),
                "odds_sources_count": metrics.get("odds_sources_count"),
                "confirmation_sources_count": metrics.get("confirmation_sources_count"),
                "confirmation_sources": metrics.get("confirmation_sources"),
                "line_sources": metrics.get("line_sources"),
                "publish_coverage_reasons": metrics.get("publish_coverage_reasons"),
            },
        })
    return watchlist


def evaluate_candidate(candidate: dict[str, Any], sent_index: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any], str | None]:
    metrics = candidate_metrics(candidate)
    hard = hard_reject_reasons(candidate, metrics, sent_index)
    if hard:
        return False, hard, metrics, None
    all_tier_reasons: list[str] = []
    for tier in ("A", "B", "C"):
        reasons = tier_reasons(tier, candidate, metrics)
        if not reasons:
            tier_name = f"уровень {tier}"
            final_reasons = final_publish_guard_reasons(candidate, metrics, tier_name)
            if final_reasons:
                return False, final_reasons, metrics, tier_name
            return True, [], metrics, tier_name
        all_tier_reasons.extend(reasons)
    return False, all_tier_reasons, metrics, None


def candidate_rank(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> tuple[float, float, float, float, float]:
    tier_bonus = {"уровень A": 30.0, "уровень B": 15.0, "уровень C": 0.0}.get(tier, 0.0)
    proxy_penalty = 5.0 if str(metrics.get("quality_score_source") or "") == "proxy" else 0.0
    xg = metrics.get("xg_sanity") or {}
    xg_gap_penalty = min(
        10.0,
        float(xg.get("xg_model_optimism_gap_pp") or max(0.0, float(xg.get("xg_model_gap_pp") or 0.0))) * 0.45,
    ) if bool(xg.get("enabled")) else 0.0
    btts = metrics.get("btts_sanity") or {}
    btts_gap_penalty = min(10.0, float(btts.get("btts_abs_gap_pp") or 0.0) * 0.45) if bool(btts.get("enabled")) else 0.0
    dnb = metrics.get("dnb_sanity") or {}
    dnb_gap_penalty = min(8.0, float(dnb.get("dnb_model_optimism_gap_pp") or 0.0) * 0.50) if bool(dnb.get("enabled")) else 0.0
    return (
        tier_bonus + float(metrics["canonical_ev_pct"]) - proxy_penalty - xg_gap_penalty - btts_gap_penalty - dnb_gap_penalty,
        float(metrics["canonical_edge_pp"]),
        float(metrics["quality_score"]),
        float(metrics["publication_score"]),
        float(metrics["confidence"]),
    )

def load_candidate_pool() -> tuple[list[dict[str, Any]], dict[str, int]]:
    pool: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    filter_by_time = env_bool("CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME", True)
    now = datetime.now(UTC)
    earliest = now + timedelta(minutes=effective_min_kickoff_lead_minutes())
    latest = now + timedelta(hours=max(1, env_int("PUBLISH_WINDOW_HOURS", 24)))
    run_summary = load_json(".data/exports/latest-run-summary.json", {})
    debug = load_json(".logs/debug-last-run.json", {})
    rescue_payload = load_json(".data/exports/latest-rescue-candidates.json", [])
    artifact_rescue_payload = load_json("artifacts/run-bot/latest-rescue-candidates.json", [])
    reference = newest_timestamp(run_summary, debug, rescue_payload, artifact_rescue_payload) or now

    def row_in_current_window(row: dict[str, Any]) -> bool:
        if not filter_by_time or not env_bool("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", True):
            return True
        kickoff = parse_dt(row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
        if kickoff is None:
            return env_bool("CONTROLLED_FALLBACK_ALLOW_UNKNOWN_TIME", False)
        return earliest <= kickoff <= latest

    def row_post_quality_canonical_metrics(row: dict[str, Any]) -> dict[str, float]:
        odds = as_float(row.get("selected_odds"), 0.0) or as_float(row.get("odds"), 0.0)
        diag = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        quality = diag.get("quality") if isinstance(diag.get("quality"), dict) else {}
        adjusted = (
            as_float(quality.get("final_adjusted_probability"), 0.0)
            or as_float(row.get("adjusted_probability"), 0.0)
            or as_float(row.get("final_probability"), 0.0)
            or as_float(row.get("canonical_adjusted_probability"), 0.0)
            or as_float(row.get("probability_used_for_ev"), 0.0)
            or as_float(row.get("model_probability"), 0.0)
        )
        implied = (1.0 / odds) if odds > 1.0 else as_float(row.get("selected_implied_probability"), as_float(row.get("implied_probability"), 0.0))
        ev_pct = ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 and adjusted > 0 else -999.0
        edge_pp = (adjusted - implied) * 100.0 if adjusted > 0 and implied > 0 else -999.0
        return {"odds": odds, "adjusted_probability": adjusted, "implied": implied, "canonical_ev_pct": ev_pct, "canonical_edge_pp": edge_pp}

    def row_allowed_for_fallback_pool(source: str, row: dict[str, Any]) -> bool:
        if not env_bool("CONTROLLED_FALLBACK_POOL_CANONICAL_VALUE_PREFILTER_ENABLED", True):
            return True
        metrics = row_post_quality_canonical_metrics(row)
        min_ev = env_float("CONTROLLED_FALLBACK_POOL_MIN_CANONICAL_EV_PCT", 0.0)
        min_edge = env_float("CONTROLLED_FALLBACK_POOL_MIN_CANONICAL_EDGE_PP", 0.0)
        if metrics["canonical_ev_pct"] < min_ev or metrics["canonical_edge_pp"] < min_edge:
            counts[f"{source}_canonical_negative_value_prefilter"] += 1
            try:
                source_summary = row.setdefault("source_summary", {})
                if isinstance(source_summary, dict):
                    source_summary["fallback_pool_prefilter_reject"] = {k: round(v, 4) for k, v in metrics.items()}
            except Exception:
                pass
            return False
        return True

    def payload_is_usable(source: str, payload: Any) -> bool:
        if not env_bool("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", True):
            return True
        if isinstance(payload, dict):
            if is_payload_fresh(payload, reference):
                return True
            counts[f"{source}_stale_payload"] += 1
            return False
        # Legacy list payloads have no run timestamp. Keep them only when the
        # current-window check can prove that rows are about upcoming matches.
        return True

    def add_rows(source: str, rows: Any) -> None:
        if not payload_is_usable(source, rows):
            return
        if isinstance(rows, dict):
            rows_iter = rows.get("candidates") or rows.get("rows") or rows.get("items") or []
        else:
            rows_iter = rows
        if not isinstance(rows_iter, list):
            return
        for row in rows_iter:
            if not isinstance(row, dict):
                continue
            if not row_in_current_window(row):
                counts[f"{source}_stale_or_outside_window"] += 1
                continue
            if not row_allowed_for_fallback_pool(source, row):
                continue
            key = dedupe_key(row)
            if key in seen:
                counts[f"{source}_duplicate_in_pool"] += 1
                continue
            seen.add(key)
            row.setdefault("_candidate_source", source)
            pool.append(row)
            counts[source] += 1

    add_rows("latest_rescue_candidates", rescue_payload)
    add_rows("artifact_rescue_candidates", artifact_rescue_payload)
    if isinstance(debug, dict) and (not env_bool("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", True) or is_payload_fresh(debug, reference)):
        add_rows("debug_candidates_before_quality", debug.get("candidates_before_quality") or [])
        add_rows("debug_candidates_after_quality", debug.get("candidates_after_quality") or [])
    elif isinstance(debug, dict) and debug:
        counts["debug_stale_payload"] += 1
    # Shadow rows are historical learning material. They are NOT a fresh publication source by default:
    # they often contain already-started matches and can drown the current candidate pool.
    if env_bool("CONTROLLED_FALLBACK_INCLUDE_STATE_SHADOW", False):
        state = load_json(".data/state.json", {})
        if isinstance(state, dict):
            add_rows("state_shadow_bets", state.get("shadow_bets") or [])
    add_rows("latest_picks", load_json(".data/exports/latest-picks.json", []))
    return pool, dict(counts)


def already_has_picks() -> bool:
    # latest-picks.json may contain generated candidates that never reached Telegram.
    # Only an actually sent/published pick may suppress fallback publication.
    latest_picks = load_json(".data/exports/latest-picks.json", [])
    if isinstance(latest_picks, list) and len(latest_picks) > 0 and env_bool("CONTROLLED_FALLBACK_SKIP_IF_LATEST_PICKS", True):
        if not env_bool("CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME", True):
            return any(is_sent_pick_row(row) for row in latest_picks if isinstance(row, dict))
        now = datetime.now(UTC)
        latest = now + timedelta(hours=max(1, env_int("PUBLISH_WINDOW_HOURS", 24)))
        for row in latest_picks:
            if not isinstance(row, dict) or not is_sent_pick_row(row):
                continue
            kickoff = parse_dt(row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
            if kickoff is not None and now <= kickoff <= latest:
                return True
    debug = load_json(".logs/debug-last-run.json", {})
    run_summary = load_json(".data/exports/latest-run-summary.json", {})
    reference = newest_timestamp(debug, run_summary) or datetime.now(UTC)
    if env_bool("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", True) and not is_payload_fresh(debug, reference):
        return False
    summary = debug.get("summary") if isinstance(debug, dict) else {}
    published_to_telegram = max(
        as_int(summary.get("published_to_telegram"), 0),
        as_int(summary.get("telegram_picks_sent"), 0),
    )
    return published_to_telegram > 0 and env_bool("CONTROLLED_FALLBACK_SKIP_IF_INTERNAL_PUBLISHED", True)


def send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "missing_telegram_credentials"
    text = normalize_telegram_text(text)
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with request.urlopen(request.Request(url, data=data, method="POST"), timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        result = payload.get("result") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else {}
        ok = isinstance(payload, dict) and payload.get("ok") is True and result.get("message_id")
        blocked = isinstance(payload, dict) and bool(payload.get("blocked_by_market_family_publication_guard"))
        return bool(ok and not blocked), body[:1000]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"



def clean_point_text(point: Any) -> str:
    if point in (None, "", "null"):
        return ""
    try:
        value = float(point)
        if abs(value) < 1e-9:
            return "0"
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    except Exception:
        return str(point)


def candidate_side_code(candidate: dict[str, Any]) -> str | None:
    raw_side = str(candidate.get("team_side") or candidate.get("side") or "").strip().lower()
    if raw_side in {"home", "1", "team1", "home_team"}:
        return "1"
    if raw_side in {"away", "2", "team2", "away_team"}:
        return "2"

    selection = str(candidate.get("selection") or candidate.get("selection_key") or "").strip().lower()
    home = str(candidate.get("home_team") or "").strip().lower()
    away = str(candidate.get("away_team") or "").strip().lower()
    if home and home in selection:
        return "1"
    if away and away in selection:
        return "2"

    # Some feeds encode DNB side in selection_key.
    selection_key = str(candidate.get("selection_key") or "").strip().lower()
    if any(token in selection_key for token in {"home", "team1", ":1", "_1"}):
        return "1"
    if any(token in selection_key for token in {"away", "team2", ":2", "_2"}):
        return "2"
    return None


def handicap_market_title(candidate: dict[str, Any]) -> str | None:
    fam = family_norm(candidate)
    if fam not in {"dnb", "spreads"}:
        return None
    side = candidate_side_code(candidate)
    point = clean_point_text(candidate.get("point"))
    if fam == "dnb" and not point:
        point = "0"
    if side and point != "":
        return f"Фора {side}({point})"
    if side:
        return f"Фора {side}"
    if fam == "dnb":
        return "Фора 0"
    return "Фора"


def clean_handicap_selection_text(candidate: dict[str, Any], selection: str) -> str:
    value = str(selection or "").strip()
    if not value:
        return value

    point = clean_point_text(candidate.get("point"))
    if point:
        escaped = re.escape(point)
        patterns = [
            rf"\s*\(\s*{escaped}\s*\)\s*$",
            rf"\s+{escaped}\s*$",
        ]
        for pattern in patterns:
            value = re.sub(pattern, "", value).strip()

    value = re.sub(r"\s*\(\s*[+-]?\d+(?:\.\d+)?\s*\)\s*$", "", value).strip()
    value = re.sub(r"\s+[+-]?\d+(?:\.\d+)?\s*$", "", value).strip()

    try:
        home_raw = str(candidate.get("home_team") or "").strip()
        away_raw = str(candidate.get("away_team") or "").strip()
        if home_raw and home_raw.lower() == value.lower():
            return translate_team_name(home_raw)
        if away_raw and away_raw.lower() == value.lower():
            return translate_team_name(away_raw)
    except Exception:
        pass

    return normalize_telegram_text(value)



def bet_line_text(candidate: dict[str, Any], selection: str) -> str:
    fam = family_norm(candidate)
    handicap_title = handicap_market_title(candidate)
    if handicap_title:
        # Keep the selected team after dash for readability, but do not duplicate the point:
        # "Фора 2(0) — Команда", not "Фора 2(0) — Команда (0)".
        clean_selection = clean_handicap_selection_text(candidate, selection)
        return f"{handicap_title} — {clean_selection}" if clean_selection else handicap_title

    point = candidate.get("point")
    point_text = "" if point in (None, "", "null") else f" ({clean_point_text(point)})"
    return f"{market_title(fam)} — {selection}{point_text}"



def market_title(family: str) -> str:
    return {
        "totals": "Тотал",
        "dnb": "Фора 0 / DNB",
        "btts": "Обе забьют",
        "h2h": "Исход",
        "spreads": "Фора",
        "teamtotals": "Индивидуальный тотал",
        "teamTotals": "Индивидуальный тотал",
    }.get(family, family)


def kickoff_text(candidate: dict[str, Any]) -> str:
    return format_match_time_for_telegram(
        candidate.get("commence_time") or candidate.get("start_time") or candidate.get("kickoff")
    )


def publish_window_label() -> str:
    hours = max(1, env_int("PUBLISH_WINDOW_HOURS", 24))
    if 11 <= hours % 100 <= 14:
        word = "часов"
    elif hours % 10 == 1:
        word = "час"
    elif hours % 10 in {2, 3, 4}:
        word = "часа"
    else:
        word = "часов"
    return f"{hours} {word}"


def stake_amount_for_tier(tier: str, bankroll: dict[str, Any], used_stake: float = 0.0) -> float:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure - max(0.0, used_stake))
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65)
    max_stake = env_float("CONTROLLED_FALLBACK_MAX_STAKE_" + tier.replace("уровень ", "TIER_").replace(" ", "_").upper(), 5.0)
    min_stake = env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0)
    total_cap_pct = env_float("CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT", 1.8)
    total_cap = bank * total_cap_pct / 100.0 if bank > 0 else max_stake
    remaining_cap = max(0.0, total_cap - max(0.0, used_stake))
    raw = min(max_stake, max(min_stake, bank * stake_pct / 100.0)) if bank > 0 else max_stake
    stake = min(raw, available, remaining_cap)
    if stake < min_stake and env_bool("CONTROLLED_FALLBACK_SKIP_IF_STAKE_BELOW_MIN", True):
        return 0.0
    return max(0.0, round(stake, 2))


def sanity_lines(metrics: dict[str, Any]) -> str:
    lines = ""
    xg = metrics.get("xg_sanity") or {}
    if bool(xg.get("enabled")):
        lines += (
            f"\n🔎 xG-проверка: ориентир {float(xg.get('xg_probability_pct') or 0.0):.1f}% "
            f"| разрыв {float(xg.get('xg_model_gap_pp') or 0.0):+.1f} п.п."
        )
    btts = metrics.get("btts_sanity") or {}
    if bool(btts.get("enabled")):
        lines += (
            f"\n🔎 ОЗ-проверка: ориентир {float(btts.get('btts_probability_pct') or 0.0):.1f}% "
            f"| разрыв {float(btts.get('btts_model_gap_pp') or 0.0):+.1f} п.п."
        )
    dnb = metrics.get("dnb_sanity") or {}
    if bool(dnb.get("enabled")):
        lines += (
            f"\n🔎 DNB-проверка: без ничьей {float(dnb.get('dnb_no_push_probability_pct') or 0.0):.1f}% "
            f"| xG EV {float(dnb.get('dnb_xg_ev_unconditional_pct') or 0.0):+.1f}% "
            f"| разрыв {float(dnb.get('dnb_model_gap_pp') or 0.0):+.1f} п.п."
        )
    return lines


def pick_block(index: int, candidate: dict[str, Any], metrics: dict[str, Any], tier: str, stake: float) -> str:
    home = translate_team_name(candidate.get("home_team") or "")
    away = translate_team_name(candidate.get("away_team") or "")
    league = translate_league_name(candidate.get("league_name") or "")
    selection = translate_selection_text(candidate.get("selection") or "", candidate.get("home_team") or "", candidate.get("away_team") or "")

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    xg_line = ""
    if expected_home not in (None, "") and expected_away not in (None, ""):
        try:
            xg_line = f"\n📈 Ожидаемые голы: {float(expected_home):.2f} : {float(expected_away):.2f}"
        except Exception:
            xg_line = ""
    xg_line += sanity_lines(metrics)

    source_note = ""
    if int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0) < 2:
        source_note = " | один источник, сниженный риск"
    confirmations = ", ".join(metrics.get("confirmation_sources") or []) or "нет"

    return (
        f"{index}. {home} — {away}\n"
        f"🎯 Ставка: {bet_line_text(candidate, selection)}\n"
        f"💸 Коэффициент: {metrics['odds']:.2f}\n"
        f"📊 Скорректированная оценка: {metrics['adjusted_probability'] * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {metrics['market_probability'] * 100:.1f}%\n"
        f"✅ Уверенность: {metrics['confidence']:.1f}% | качество {metrics['quality_score']:.1f} "
        f"({'оценка резерва' if metrics.get('quality_score_source') == 'proxy' else 'модель'}) | {tier}{source_note}\n"
        f"📚 Линии: {metrics['books_count']} | odds sources: {metrics.get('odds_sources_count', 0)} | context sources: {metrics.get('confirmation_sources_count', 0)}\n"
        f"🔎 Линии: {', '.join(metrics.get('line_sources') or []) or selected_source(candidate) or 'н/д'} | контекст: {confirmations} | букмекер: {selected_bookmaker(candidate) or 'н/д'}\n"
        f"🧮 Контрольная ценность: запас {metrics['canonical_edge_pp']:+.1f} п.п. | EV {metrics['canonical_ev_pct']:+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {kickoff_text(candidate)}\n"
        f"💰 Сумма ставки: {stake:.2f} (ограничение риска)"
        f"{xg_line}"
    )


def select_top_picks(
    viable: list[tuple[tuple[float, float, float, float, float], dict[str, Any], dict[str, Any], str]],
    bankroll: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], str, float]]:
    max_picks = max(1, env_int("CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN", env_int("MAX_PICKS_PER_RUN", 1)))
    max_picks = min(max_picks, env_int("CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN", 3))
    per_match = max(1, env_int("CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH", 1))

    selected: list[tuple[dict[str, Any], dict[str, Any], str, float]] = []
    match_counts: dict[str, int] = {}
    used_stake = 0.0

    for _, candidate, metrics, tier in viable:
        if len(selected) >= max_picks:
            break

        tier_letter = str(tier).replace("уровень ", "").strip().upper()
        if tier_letter == "C" and not env_bool("CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED", False):
            continue

        # Extra top-bundle guard: additional picks must be materially good, not just barely pass.
        if len(selected) > 0 and env_bool("CONTROLLED_FALLBACK_EXTRA_PICK_STRICT", True):
            if float(metrics.get("canonical_ev_pct") or 0.0) < env_float("CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT", 7.0):
                continue
            if float(metrics.get("canonical_edge_pp") or 0.0) < env_float("CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP", 3.0):
                continue
            if float(metrics.get("confidence") or 0.0) < env_float("CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE", 67.0):
                continue

        match_key = str(candidate.get("match_key") or f"{candidate.get('home_team')}|{candidate.get('away_team')}|{candidate.get('commence_time')}")
        if match_counts.get(match_key, 0) >= per_match:
            continue

        stake = stake_amount_for_tier(tier, bankroll, used_stake)
        if stake <= 0:
            continue

        selected.append((candidate, metrics, tier, stake))
        match_counts[match_key] = match_counts.get(match_key, 0) + 1
        used_stake += stake

    return selected


def build_top_bundle_message(
    selected: list[tuple[dict[str, Any], dict[str, Any], str, float]],
    bankroll: dict[str, Any],
) -> str:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    total_stake = sum(item[3] for item in selected)
    n = len(selected)

    title_word = "прогноз" if n == 1 else "прогноза" if n in {2, 3, 4} else "прогнозов"
    lines = [
        f"🔥 {n} топовых контролируемых {title_word} на ближайшие {publish_window_label()}",
        "",
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}",
        f"💰 Новый риск: {total_stake:.2f} | Режим: top-bundle, только прошедшие финальные guard’ы",
        "",
        f"Окно поиска: от {effective_min_kickoff_lead_minutes()} мин до {publish_window_label()}. "
        "Публикую 1 лучший прогноз или multi-прогноз, если несколько разных матчей отдельно проходят EV, время, дубли и market/xG sanity.",
        "",
    ]
    for idx, (candidate, metrics, tier, stake) in enumerate(selected, start=1):
        lines.append(pick_block(idx, candidate, metrics, tier, stake))
        if idx != n:
            lines.append("")
    lines.append("")
    lines.append("📝 Комментарий: это не гарантия прибыли. Сумма снижена, потому что часть сигналов остаётся controlled reserve, а не clean quality-pass.")
    return normalize_telegram_text("\n".join(lines))



def build_message(candidate: dict[str, Any], metrics: dict[str, Any], tier: str, bankroll: dict[str, Any]) -> str:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake = stake_amount_for_tier(tier, bankroll, 0.0)
    point = candidate.get("point")
    point_text = "" if point in (None, "", "null") else f" ({point})"
    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    xg_line = ""
    if expected_home not in (None, "") and expected_away not in (None, ""):
        try:
            xg_line = f"\n📈 Ожидаемые голы: {float(expected_home):.2f} : {float(expected_away):.2f}"
            xg = metrics.get("xg_sanity") or {}
            if bool(xg.get("enabled")):
                xg_line += (
                    f"\n🔎 xG-проверка: ориентир {float(xg.get('xg_probability_pct') or 0.0):.1f}% "
                    f"| разрыв {float(xg.get('xg_model_gap_pp') or 0.0):+.1f} п.п."
                )
        except Exception:
            pass
    btts = metrics.get("btts_sanity") or {}
    if bool(btts.get("enabled")):
        xg_line += (
            f"\n🔎 ОЗ-проверка: ориентир {float(btts.get('btts_probability_pct') or 0.0):.1f}% "
            f"| разрыв {float(btts.get('btts_model_gap_pp') or 0.0):+.1f} п.п."
        )

    dnb = metrics.get("dnb_sanity") or {}
    if bool(dnb.get("enabled")):
        xg_line += (
            f"\n🔎 DNB-проверка: без ничьей {float(dnb.get('dnb_no_push_probability_pct') or 0.0):.1f}% "
            f"| xG EV {float(dnb.get('dnb_xg_ev_unconditional_pct') or 0.0):+.1f}% "
            f"| разрыв {float(dnb.get('dnb_model_gap_pp') or 0.0):+.1f} п.п."
        )

    risk_note = {
        "уровень A": "контролируемый резерв, уровень A. 2+ независимых источника линии и нормальный запас ценности.",
        "уровень B": "контролируемый резерв, уровень B. 1 источник линии + 2+ букмекера + усиленный контекст; сумма снижена.",
        "уровень C": "контролируемый резерв, уровень C. Пограничный резерв, минимальная тестовая сумма.",
    }.get(tier, "контролируемый резерв")

    home = translate_team_name(candidate.get("home_team") or "")
    away = translate_team_name(candidate.get("away_team") or "")
    league = translate_league_name(candidate.get("league_name") or "")
    selection = translate_selection_text(candidate.get("selection") or "", candidate.get("home_team") or "", candidate.get("away_team") or "")

    return normalize_telegram_text(
        f"🔥 1 контролируемый прогноз на ближайшие {publish_window_label()}\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"⚠️ Режим: {risk_note}\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {bet_line_text(candidate, selection)}\n"
        f"💸 Коэффициент: {metrics['odds']:.2f}\n"
        f"📊 Скорректированная оценка: {metrics['adjusted_probability'] * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {metrics['market_probability'] * 100:.1f}%\n"
        f"✅ Уверенность: {metrics['confidence']:.1f}% | качество {metrics['quality_score']:.1f} ({'оценка резерва' if metrics.get('quality_score_source') == 'proxy' else 'модель'}) | {tier}\n"
        f"📚 Линии: {metrics['books_count']} | odds sources: {metrics.get('odds_sources_count', 0)} | context sources: {metrics.get('confirmation_sources_count', 0)}\n"
        f"🔎 Линии: {', '.join(metrics.get('line_sources') or []) or selected_source(candidate) or 'н/д'} | контекст: {', '.join(metrics.get('confirmation_sources') or []) or 'нет'} | букмекер: {selected_bookmaker(candidate) or 'н/д'}\n"
        f"🧮 Контрольная ценность: запас {metrics['canonical_edge_pp']:+.1f} п.п. | EV {metrics['canonical_ev_pct']:+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {kickoff_text(candidate)}\n"
        f"💰 Сумма ставки: {stake:.2f} (ограничение риска)"
        f"{xg_line}\n"
        "📝 Комментарий: ставка разрешена только после повторного пересчёта от выбранного коэффициента и проверки времени матча. "
        "Если матч уже начался или вышел за окно публикации, резервный публикователь такую ставку не отправляет."
    )


def build_no_pick_message(report: dict[str, Any]) -> str:
    counter: Counter[str] = Counter()
    for item in report.get("evaluated") or []:
        for reason in item.get("reject_reasons") or []:
            counter[str(reason)] += 1
    pool_counts = report.get("pool_counts") or {}
    lines = [
        "🧾 Отчёт по запуску бота",
        "❌ Прогнозов не было.",
        "",
        "Основной слой качества не нашёл чистую ставку, а контролируемый резерв не нашёл безопасный вариант.",
        "",
        f"Проверено резервных кандидатов: {report.get('candidates_seen', 0)}",
    ]
    if pool_counts:
        lines.append("Пул кандидатов:")
        for key, count in sorted(pool_counts.items()):
            lines.append(f"• {key}: {count}")
    watchlist = report.get("watchlist") or []
    if watchlist:
        lines.append("")
        lines.append("Наблюдения без публикации:")
        for item in watchlist[:3]:
            metrics = item.get("metrics") or {}
            home = item.get("home_team_ru") or translate_team_name(item.get("home_team") or "")
            away = item.get("away_team_ru") or translate_team_name(item.get("away_team") or "")
            family = str(item.get("family") or "")
            selection = translate_selection_text(item.get("selection") or "", item.get("home_team") or "", item.get("away_team") or "")
            point = item.get("point")
            point_text = "" if point in (None, "", "null") else f" ({clean_point_text(point)})"
            reasons = ", ".join(translate_reject_reason(reason) for reason in (item.get("reject_reasons") or [])[:3])
            lines.append(
                f"• {home} — {away}: {market_title(family)} {selection}{point_text}, "
                f"EV {float(metrics.get('canonical_ev_pct') or 0.0):+.1f}%, "
                f"линий {int(metrics.get('books_count') or 0)} — не публикую: {reasons}"
            )
    if counter:
        lines.append("Причины отказа:")
        for reason, count in counter.most_common(10):
            lines.append(f"• {translate_reject_reason(reason)} — {count}")
    return normalize_telegram_text("\n".join(lines))


def main() -> int:
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": env_bool("CONTROLLED_FALLBACK_ENABLED", True),
        "published": False,
        "status": "not_started",
        "candidates_seen": 0,
        "evaluated": [],
        "scan_policy": {
            "min_kickoff_lead_minutes": effective_min_kickoff_lead_minutes(),
            "publish_window_hours": env_int("PUBLISH_WINDOW_HOURS", 12),
            "mode": "best_or_multi",
            "max_picks_per_run": env_int("CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN", env_int("MAX_PICKS_PER_RUN", 3)),
            "use_context_source_index": env_bool("CONTROLLED_FALLBACK_USE_CONTEXT_SOURCE_INDEX", True),
            "min_confirmation_sources": env_int("CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES", 2),
        },
        "time_guard": {
            "enabled": env_bool("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", True),
            "min_kickoff_lead_minutes": effective_min_kickoff_lead_minutes(),
            "base_min_kickoff_lead_minutes": env_int("MIN_KICKOFF_LEAD_MINUTES", 20),
            "manual_late_mode_enabled": env_bool("MANUAL_LATE_MODE_ENABLED", False),
            "manual_late_adaptive_min_kickoff_lead_minutes": env_int("MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES", 0),
            "publish_window_hours": env_int("PUBLISH_WINDOW_HOURS", 24),
        },
    }
    if not report["enabled"]:
        report["status"] = "disabled"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    if already_has_picks():
        report["status"] = "skipped_existing_pick"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    sent_index = prune_sent_index(load_sent_index(), env_int("CONTROLLED_FALLBACK_DEDUPE_HOURS", 72))
    candidates, pool_counts = load_candidate_pool()
    report["candidates_seen"] = len(candidates)
    report["pool_counts"] = pool_counts

    debug = load_json(".logs/debug-last-run.json", {})
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}

    viable: list[tuple[tuple[float, float, float, float, float], dict[str, Any], dict[str, Any], str]] = []
    for candidate in candidates:
        try:
            ok, reasons, metrics, tier = evaluate_candidate(candidate, sent_index)
        except Exception as exc:
            ok = False
            reasons = [f"candidate_evaluation_error:{type(exc).__name__}"]
            metrics = {"error": str(exc)[:300]}
            tier = None
        row = {
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "home_team_ru": translate_team_name(candidate.get("home_team") or ""),
            "away_team_ru": translate_team_name(candidate.get("away_team") or ""),
            "league_name": candidate.get("league_name"),
            "league_name_ru": translate_league_name(candidate.get("league_name") or ""),
            "commence_time": candidate.get("commence_time"),
            "commence_time_display": kickoff_text(candidate),
            "candidate_source": candidate.get("_candidate_source"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "ok": ok,
            "tier": tier,
            "reject_reasons": reasons,
            "reject_reasons_ru": [translate_reject_reason(item) for item in reasons],
            "metrics": metrics,
        }
        report["evaluated"].append(row)
        if ok and tier:
            viable.append((candidate_rank(candidate, metrics, tier), candidate, metrics, tier))

    if not viable:
        report["status"] = "no_viable_controlled_fallback"
        report["watchlist"] = build_watchlist(report.get("evaluated") or [])
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        if env_bool("CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT", True) and not env_bool("PUBLISH_DRY_RUN", False):
            sent, send_result = send_telegram(build_no_pick_message(report))
            report["no_pick_report_sent"] = sent
            report["telegram_result"] = send_result
            write_json("artifacts/controlled-fallback-report.json", report)
            write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    viable.sort(key=lambda item: item[0], reverse=True)
    selected_items = select_top_picks(viable, bankroll)
    if not selected_items:
        report["status"] = "no_viable_after_top_bundle_stake_guard"
        report["watchlist"] = build_watchlist(report.get("evaluated") or [])
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        if env_bool("CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT", True) and not env_bool("PUBLISH_DRY_RUN", False):
            sent, send_result = send_telegram(build_no_pick_message(report))
            report["no_pick_report_sent"] = sent
            report["telegram_result"] = send_result
            write_json("artifacts/controlled-fallback-report.json", report)
            write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    if len(selected_items) == 1:
        chosen, metrics, tier, stake = selected_items[0]
        message = build_message(chosen, metrics, tier, bankroll)
    else:
        message = build_top_bundle_message(selected_items, bankroll)

    dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)
    sent = False
    send_result = "dry_run"
    if not dry_run:
        sent, send_result = send_telegram(message)

    selected_rows: list[dict[str, Any]] = []
    if sent or dry_run:
        for chosen, metrics, tier, stake in selected_items:
            key = dedupe_key(chosen)
            if sent:
                sent_index[key] = {
                    "sent_at": datetime.now(UTC).isoformat(),
                    "match_key": chosen.get("match_key"),
                    "home_team": chosen.get("home_team"),
                    "away_team": chosen.get("away_team"),
                    "match_name": chosen.get("match_name") or f"{chosen.get('home_team') or ''} — {chosen.get('away_team') or ''}".strip(),
                    "league_name": chosen.get("league_name"),
                    "family": chosen.get("family"),
                    "selection": chosen.get("selection"),
                    "point": chosen.get("point"),
                    "odds": chosen.get("odds"),
                    "stake": stake,
                    "stake_amount": stake,
                    "tier": tier,
                    "commence_time": chosen.get("commence_time"),
                    "telegram_sent": True,
                    "publication_lifecycle_status": "telegram_sent",
                }
            selected_rows.append({
                "dedupe_key": key,
                "telegram_sent": bool(sent),
                "publication_lifecycle_status": "telegram_sent" if sent else "dry_run_selected",
                "status": "pending" if sent else "generated",
                "match_key": chosen.get("match_key"),
                "home_team": chosen.get("home_team"),
                "away_team": chosen.get("away_team"),
                "match_name": chosen.get("match_name") or f"{chosen.get('home_team') or ''} — {chosen.get('away_team') or ''}".strip(),
                "home_team_ru": translate_team_name(chosen.get("home_team") or ""),
                "away_team_ru": translate_team_name(chosen.get("away_team") or ""),
                "league_name": chosen.get("league_name"),
                "league_name_ru": translate_league_name(chosen.get("league_name") or ""),
                "family": chosen.get("family"),
                "market_family": chosen.get("family"),
                "selection": chosen.get("selection"),
                "point": chosen.get("point"),
                "odds": chosen.get("odds"),
                "selected_odds": chosen.get("odds"),
                "price": chosen.get("odds"),
                "tier": tier,
                "stake": stake,
                "stake_amount": stake,
                "commence_time": chosen.get("commence_time"),
                "kickoff": chosen.get("commence_time"),
                "commence_time_display": kickoff_text(chosen),
                "metrics": metrics,
                "bet_payload": {
                    "home_team": chosen.get("home_team"),
                    "away_team": chosen.get("away_team"),
                    "match_name": chosen.get("match_name") or f"{chosen.get('home_team') or ''} — {chosen.get('away_team') or ''}".strip(),
                    "league_name": chosen.get("league_name"),
                    "kickoff": chosen.get("commence_time"),
                    "commence_time": chosen.get("commence_time"),
                    "odds": chosen.get("odds"),
                    "selection": chosen.get("selection"),
                    "family": chosen.get("family"),
                    "stake": stake,
                    "stake_amount": stake,
                },
            })
        save_sent_index(sent_index)
    else:
        for chosen, metrics, tier, stake in selected_items:
            selected_rows.append({
                "dedupe_key": dedupe_key(chosen),
                "match_key": chosen.get("match_key"),
                "home_team": chosen.get("home_team"),
                "away_team": chosen.get("away_team"),
                "match_name": chosen.get("match_name") or f"{chosen.get('home_team') or ''} — {chosen.get('away_team') or ''}".strip(),
                "home_team_ru": translate_team_name(chosen.get("home_team") or ""),
                "away_team_ru": translate_team_name(chosen.get("away_team") or ""),
                "league_name": chosen.get("league_name"),
                "league_name_ru": translate_league_name(chosen.get("league_name") or ""),
                "family": chosen.get("family"),
                "market_family": chosen.get("family"),
                "selection": chosen.get("selection"),
                "point": chosen.get("point"),
                "odds": chosen.get("odds"),
                "selected_odds": chosen.get("odds"),
                "price": chosen.get("odds"),
                "tier": tier,
                "stake": stake,
                "stake_amount": stake,
                "commence_time": chosen.get("commence_time"),
                "kickoff": chosen.get("commence_time"),
                "commence_time_display": kickoff_text(chosen),
                "metrics": metrics,
                "bet_payload": {
                    "home_team": chosen.get("home_team"),
                    "away_team": chosen.get("away_team"),
                    "match_name": chosen.get("match_name") or f"{chosen.get('home_team') or ''} — {chosen.get('away_team') or ''}".strip(),
                    "league_name": chosen.get("league_name"),
                    "kickoff": chosen.get("commence_time"),
                    "commence_time": chosen.get("commence_time"),
                    "odds": chosen.get("odds"),
                    "selection": chosen.get("selection"),
                    "family": chosen.get("family"),
                    "stake": stake,
                    "stake_amount": stake,
                },
            })

    report.update({
        "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
        "published": bool(sent),
        "dry_run": bool(dry_run),
        "selected_count": len(selected_rows),
        "selected": selected_rows[0] if selected_rows else None,
        "selected_all": selected_rows,
        "telegram_result": send_result,
        "message": message,
    })
    write_json("artifacts/controlled-fallback-report.json", report)
    write_json(".data/exports/latest-controlled-fallback-report.json", report)
    return 0 if (sent or dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
