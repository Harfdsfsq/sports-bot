from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

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
    base_lead = max(0, env_int("MIN_KICKOFF_LEAD_MINUTES", 20))
    if not env_bool("CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD", True):
        return base_lead
    if not env_bool("MANUAL_LATE_MODE_ENABLED", False):
        return base_lead
    adaptive = env_int("MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES", 0)
    late = env_int("MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES", 0)
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
        "xg_total": round(total_xg, 3) if total_xg is not None else None,
        "xg_direction_ok": direction_ok,
    }



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
            if isinstance(row, dict) and dedupe_key(row) == key:
                return f"duplicate_state:{collection}"
    return None


def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    books = as_int(candidate.get("books_count"), 0)
    sources = as_int(candidate.get("sources_count"), 0)
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
        "sources_count": sources,
        "canonical_edge_pp": canonical_edge_pp,
        "canonical_ev_pct": canonical_ev_pct,
    }
    quality_score, quality_score_source = quality_proxy_score(candidate, seed_metrics, raw_quality_score)
    xg_sanity = xg_sanity_metrics(candidate, adjusted)

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
        "sources_count": sources,
        "quality_reasons": quality_reasons(candidate),
        "xg_sanity": xg_sanity,
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
        if float(xg.get("xg_abs_gap_pp") or 0.0) > hard_gap:
            reasons.append("xg_probability_gap_hard_reject")
    return reasons


def tier_reasons(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fam = family_norm(candidate)
    prefix = f"CONTROLLED_FALLBACK_TIER_{tier}_"
    allowed_families = env_set(prefix + "ALLOWED_FAMILIES", "")
    if allowed_families and fam not in allowed_families:
        reasons.append(f"tier_{tier.lower()}_family_not_allowed:{fam}")
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
        gap = float(xg.get("xg_abs_gap_pp") or 0.0)
        max_gap = env_float(prefix + "MAX_XG_GAP_PP", 999.0)
        if gap > max_gap:
            reasons.append(f"tier_{tier.lower()}_xg_gap_above_max")
        if tier in {"A", "B"} and not bool(xg.get("xg_direction_ok", True)):
            reasons.append(f"tier_{tier.lower()}_xg_direction_conflict")
        if tier == "A" and env_bool("CONTROLLED_FALLBACK_TIER_A_REQUIRE_XG_SANITY", True):
            if gap > env_float("CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP", 6.5):
                reasons.append("tier_a_xg_confirmation_missing")

    q_reasons = [r.lower() for r in metrics.get("quality_reasons") or []]
    allowed_stops = env_set(prefix + "ALLOWED_QUALITY_STOPS", os.getenv("CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS", "bad_historical_segment_guard,no_bet_quality_score_guard,post_calibration_probability_guard,post_calibration_edge_guard"))
    if q_reasons and q_reasons[0] not in allowed_stops:
        reasons.append(f"tier_{tier.lower()}_quality_stop_not_allowed:{q_reasons[0]}")
    return reasons


def evaluate_candidate(candidate: dict[str, Any], sent_index: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any], str | None]:
    metrics = candidate_metrics(candidate)
    hard = hard_reject_reasons(candidate, metrics, sent_index)
    if hard:
        return False, hard, metrics, None
    all_tier_reasons: list[str] = []
    for tier in ("A", "B", "C"):
        reasons = tier_reasons(tier, candidate, metrics)
        if not reasons:
            return True, [], metrics, f"уровень {tier}"
        all_tier_reasons.extend(reasons)
    return False, all_tier_reasons, metrics, None


def candidate_rank(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> tuple[float, float, float, float, float]:
    tier_bonus = {"уровень A": 30.0, "уровень B": 15.0, "уровень C": 0.0}.get(tier, 0.0)
    proxy_penalty = 5.0 if str(metrics.get("quality_score_source") or "") == "proxy" else 0.0
    xg = metrics.get("xg_sanity") or {}
    xg_gap_penalty = min(10.0, float(xg.get("xg_abs_gap_pp") or 0.0) * 0.45) if bool(xg.get("enabled")) else 0.0
    return (
        tier_bonus + float(metrics["canonical_ev_pct"]) - proxy_penalty - xg_gap_penalty,
        float(metrics["canonical_edge_pp"]),
        float(metrics["quality_score"]),
        float(metrics["publication_score"]),
        float(metrics["confidence"]),
    )

def load_candidate_pool() -> tuple[list[dict[str, Any]], dict[str, int]]:
    pool: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()

    def add_rows(source: str, rows: Any) -> None:
        if isinstance(rows, dict):
            rows_iter = rows.get("candidates") or rows.get("rows") or rows.get("items") or []
        else:
            rows_iter = rows
        if not isinstance(rows_iter, list):
            return
        for row in rows_iter:
            if not isinstance(row, dict):
                continue
            key = dedupe_key(row)
            if key in seen:
                counts[f"{source}_duplicate_in_pool"] += 1
                continue
            seen.add(key)
            row.setdefault("_candidate_source", source)
            pool.append(row)
            counts[source] += 1

    add_rows("latest_rescue_candidates", load_json(".data/exports/latest-rescue-candidates.json", []))
    add_rows("artifact_rescue_candidates", load_json("artifacts/run-bot/latest-rescue-candidates.json", []))
    debug = load_json(".logs/debug-last-run.json", {})
    if isinstance(debug, dict):
        add_rows("debug_candidates_before_quality", debug.get("candidates_before_quality") or [])
        add_rows("debug_candidates_after_quality", debug.get("candidates_after_quality") or [])
    # Shadow rows are historical learning material. They are NOT a fresh publication source by default:
    # they often contain already-started matches and can drown the current candidate pool.
    if env_bool("CONTROLLED_FALLBACK_INCLUDE_STATE_SHADOW", False):
        state = load_json(".data/state.json", {})
        if isinstance(state, dict):
            add_rows("state_shadow_bets", state.get("shadow_bets") or [])
    add_rows("latest_picks", load_json(".data/exports/latest-picks.json", []))
    return pool, dict(counts)


def already_has_picks() -> bool:
    # In external publisher mode latest-picks should normally stay empty.
    # Keep this guard for safety if internal publication is accidentally re-enabled.
    latest_picks = load_json(".data/exports/latest-picks.json", [])
    if isinstance(latest_picks, list) and len(latest_picks) > 0 and env_bool("CONTROLLED_FALLBACK_SKIP_IF_LATEST_PICKS", True):
        return True
    debug = load_json(".logs/debug-last-run.json", {})
    summary = debug.get("summary") if isinstance(debug, dict) else {}
    return isinstance(summary, dict) and as_int(summary.get("published"), 0) > 0 and env_bool("CONTROLLED_FALLBACK_SKIP_IF_INTERNAL_PUBLISHED", True)


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
        return True, body[:1000]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


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
    kickoff = parse_dt(candidate.get("commence_time") or candidate.get("start_time") or candidate.get("kickoff"))
    if kickoff is None:
        return str(candidate.get("commence_time") or "")
    return kickoff.astimezone(UTC).isoformat()


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], tier: str, bankroll: dict[str, Any]) -> str:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65)
    max_stake = env_float("CONTROLLED_FALLBACK_MAX_STAKE_" + tier.replace("уровень ", "TIER_").replace(" ", "_").upper(), 5.0)
    min_stake = env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0)
    stake = min(max_stake, max(min_stake, bank * stake_pct / 100.0)) if bank > 0 else max_stake
    stake = min(stake, available) if available > 0 else 0.0
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
    risk_note = {
        "уровень A": "контролируемый резерв, уровень A. 2+ линии и нормальный запас ценности.",
        "уровень B": "контролируемый резерв, уровень B. Основной слой качества не дал чистую ставку, поэтому сумма снижена.",
        "уровень C": "контролируемый резерв, уровень C. Пограничный резерв, минимальная тестовая сумма.",
    }.get(tier, "контролируемый резерв")

    home = translate_team_name(candidate.get("home_team") or "")
    away = translate_team_name(candidate.get("away_team") or "")
    league = translate_league_name(candidate.get("league_name") or "")
    selection = translate_selection_text(candidate.get("selection") or "", candidate.get("home_team") or "", candidate.get("away_team") or "")

    return normalize_telegram_text(
        "🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"⚠️ Режим: {risk_note}\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {market_title(family_norm(candidate))} — {selection}{point_text}\n"
        f"💸 Коэффициент: {metrics['odds']:.2f}\n"
        f"📊 Скорректированная оценка: {metrics['adjusted_probability'] * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {metrics['market_probability'] * 100:.1f}%\n"
        f"✅ Уверенность: {metrics['confidence']:.1f}% | качество {metrics['quality_score']:.1f} ({'оценка резерва' if metrics.get('quality_score_source') == 'proxy' else 'модель'}) | {tier}\n"
        f"📚 Линии: {metrics['books_count']} | Источники: {metrics['sources_count']} | {selected_bookmaker(candidate) or 'н/д'} / {selected_source(candidate) or 'н/д'}\n"
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
        ok, reasons, metrics, tier = evaluate_candidate(candidate, sent_index)
        row = {
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "home_team_ru": translate_team_name(candidate.get("home_team") or ""),
            "away_team_ru": translate_team_name(candidate.get("away_team") or ""),
            "league_name": candidate.get("league_name"),
            "league_name_ru": translate_league_name(candidate.get("league_name") or ""),
            "commence_time": candidate.get("commence_time"),
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
    _, chosen, metrics, tier = viable[0]
    message = build_message(chosen, metrics, tier, bankroll)
    dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)
    sent = False
    send_result = "dry_run"
    if not dry_run:
        sent, send_result = send_telegram(message)

    key = dedupe_key(chosen)
    if sent or dry_run:
        sent_index[key] = {
            "sent_at": datetime.now(UTC).isoformat(),
            "match_key": chosen.get("match_key"),
            "family": chosen.get("family"),
            "selection": chosen.get("selection"),
            "point": chosen.get("point"),
            "tier": tier,
            "commence_time": chosen.get("commence_time"),
        }
        save_sent_index(sent_index)

    report.update({
        "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
        "published": bool(sent),
        "dry_run": bool(dry_run),
        "selected": {
            "dedupe_key": key,
            "match_key": chosen.get("match_key"),
            "home_team": chosen.get("home_team"),
            "away_team": chosen.get("away_team"),
            "home_team_ru": translate_team_name(chosen.get("home_team") or ""),
            "away_team_ru": translate_team_name(chosen.get("away_team") or ""),
            "league_name": chosen.get("league_name"),
            "league_name_ru": translate_league_name(chosen.get("league_name") or ""),
            "family": chosen.get("family"),
            "selection": chosen.get("selection"),
            "point": chosen.get("point"),
            "odds": chosen.get("odds"),
            "tier": tier,
            "commence_time": chosen.get("commence_time"),
            "metrics": metrics,
        },
        "telegram_result": send_result,
        "message": message,
    })
    write_json("artifacts/controlled-fallback-report.json", report)
    write_json(".data/exports/latest-controlled-fallback-report.json", report)
    return 0 if (sent or dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
