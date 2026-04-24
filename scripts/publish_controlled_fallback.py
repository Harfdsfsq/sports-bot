from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request, parse

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


def quality_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    diag = candidate.get("diagnostics") or {}
    q = diag.get("quality") if isinstance(diag, dict) else None
    return q if isinstance(q, dict) else {}


def quality_reasons(candidate: dict[str, Any]) -> list[str]:
    q = quality_payload(candidate)
    reasons = q.get("reasons") or candidate.get("quality_reasons") or []
    if isinstance(reasons, list):
        return [str(item) for item in reasons]
    if isinstance(reasons, str) and reasons.strip():
        return [reasons.strip()]
    return []


def selected_bookmaker(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(candidate.get("bookmaker") or ss.get("selected_bookmaker") or ss.get("bookmaker") or "").strip()


def selected_source(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(ss.get("selected_source") or ss.get("source") or "").strip()


def selection_kind(candidate: dict[str, Any]) -> str:
    text = f"{candidate.get('selection') or ''} {candidate.get('selection_key') or ''}".lower()
    if "меньше" in text or "under" in text or "_under" in text:
        return "under"
    if "больше" in text or "over" in text or "_over" in text:
        return "over"
    return ""


def candidate_score(candidate: dict[str, Any]) -> tuple[float, float, float, float]:
    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    if odds <= 1.0 or adjusted <= 0.0:
        return (-999.0, -999.0, -999.0, -999.0)
    selected_implied = 1.0 / odds
    canonical_edge_pp = (adjusted - selected_implied) * 100.0
    canonical_ev_pct = ((adjusted * odds) - 1.0) * 100.0
    quality_score = as_float(quality_payload(candidate).get("quality_score"), as_float(candidate.get("quality_score"), 0.0))
    publication_score = as_float(candidate.get("publication_score"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    return (canonical_ev_pct, canonical_edge_pp, quality_score, publication_score + confidence / 10.0)


def evaluate_candidate(candidate: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    family = str(candidate.get("family") or "").strip().lower()
    allowed_families = env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb")
    if family not in allowed_families:
        reasons.append(f"family_not_allowed:{family}")

    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    books = as_int(candidate.get("books_count"), 0)
    sources = as_int(candidate.get("sources_count"), 0)
    quality_score = as_float(quality_payload(candidate).get("quality_score"), as_float(candidate.get("quality_score"), 0.0))
    publication_score = as_float(candidate.get("publication_score"), 0.0)
    q_reasons = quality_reasons(candidate)

    selected_implied = 1.0 / odds if odds > 1.0 else 0.0
    canonical_edge_pp = (adjusted - selected_implied) * 100.0 if odds > 1.0 else -999.0
    canonical_ev_pct = ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0
    market_edge_pp = (adjusted - market_prob) * 100.0 if market_prob > 0 else 0.0

    metrics.update({
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
        "publication_score": round(publication_score, 3),
        "books_count": books,
        "sources_count": sources,
        "quality_reasons": q_reasons,
    })

    if odds < env_float("CONTROLLED_FALLBACK_MIN_ODDS", 1.65):
        reasons.append("odds_below_min")
    if odds > env_float("CONTROLLED_FALLBACK_MAX_ODDS", 2.85):
        reasons.append("odds_above_max")
    if books < env_int("CONTROLLED_FALLBACK_MIN_BOOKS", 2):
        reasons.append("books_below_min")
    if sources < env_int("CONTROLLED_FALLBACK_MIN_SOURCES", 1):
        reasons.append("sources_below_min")
    if confidence < env_float("CONTROLLED_FALLBACK_MIN_CONFIDENCE", 60.0):
        reasons.append("confidence_below_min")
    if quality_score < env_float("CONTROLLED_FALLBACK_MIN_QUALITY_SCORE", 68.0):
        reasons.append("quality_below_min")
    if publication_score < env_float("CONTROLLED_FALLBACK_MIN_PUBLICATION_SCORE", 30.0):
        reasons.append("publication_score_below_min")
    if canonical_edge_pp < env_float("CONTROLLED_FALLBACK_MIN_CANONICAL_EDGE_PP", 3.0):
        reasons.append("canonical_edge_below_min")
    if canonical_ev_pct < env_float("CONTROLLED_FALLBACK_MIN_CANONICAL_EV_PCT", 5.0):
        reasons.append("canonical_ev_below_min")

    allowed_stops = env_set(
        "CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS",
        "bad_historical_segment_guard,no_bet_quality_score_guard,post_calibration_probability_guard",
    )
    if not q_reasons:
        reasons.append("missing_quality_reason")
    elif q_reasons[0].strip().lower() not in allowed_stops:
        reasons.append(f"quality_stop_not_allowed:{q_reasons[0]}")

    # Hard safety: never rescue internally negative edge/EV after canonical recomputation.
    if canonical_edge_pp <= 0 or canonical_ev_pct <= 0:
        reasons.append("canonical_negative_value")

    # Avoid obviously unstable one-book totals unless explicitly allowed.
    if books < 2 and not env_bool("CONTROLLED_FALLBACK_ALLOW_SINGLE_BOOK", False):
        reasons.append("single_book_rejected")

    # Avoid pure h2h outsiders in rescue mode.
    if family == "h2h" and odds > env_float("CONTROLLED_FALLBACK_H2H_MAX_ODDS", 2.25):
        reasons.append("h2h_rescue_odds_too_high")

    return (len(reasons) == 0, reasons, metrics)


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], bankroll: dict[str, Any]) -> str:
    home = str(candidate.get("home_team") or "")
    away = str(candidate.get("away_team") or "")
    league = str(candidate.get("league_name") or "")
    family = str(candidate.get("family") or "")
    selection = str(candidate.get("selection") or "")
    point = candidate.get("point")
    odds = metrics["odds"]
    adjusted = metrics["adjusted_probability"] * 100.0
    market = metrics["market_probability"] * 100.0
    edge = metrics["canonical_edge_pp"]
    ev = metrics["canonical_ev_pct"]
    confidence = metrics["confidence"]
    q_score = metrics["quality_score"]
    books = metrics["books_count"]
    sources = metrics["sources_count"]
    bookmaker = selected_bookmaker(candidate)
    source = selected_source(candidate)
    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    commence = str(candidate.get("commence_time") or "")
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 1.0)
    max_stake = env_float("CONTROLLED_FALLBACK_MAX_STAKE", 10.0)
    min_stake = env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0)
    stake = min(max_stake, max(min_stake, bank * stake_pct / 100.0)) if bank > 0 else max_stake
    stake = min(stake, available) if available > 0 else 0.0
    stake = round(stake, 2)

    xg_line = ""
    if expected_home not in (None, "") and expected_away not in (None, ""):
        try:
            xg_line = f"\n📈 Ожидаемые голы: {float(expected_home):.2f} : {float(expected_away):.2f}"
        except Exception:
            pass

    point_text = "" if point in (None, "", "null") else f" ({point})"
    market_title = {
        "totals": "Тотал",
        "dnb": "Фора 0 / DNB",
        "btts": "Обе забьют",
        "h2h": "Исход",
        "spreads": "Фора",
        "teamTotals": "Индивидуальный тотал",
    }.get(family, family)

    return (
        "🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        "⚠️ Режим: controlled fallback. Чистых quality-pass ставок не было, поэтому опубликован только лучший кандидат, "
        "который прошёл повторную проверку value, odds и риска. Ставка снижена.\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {market_title} — {selection}{point_text}\n"
        f"💸 Коэффициент: {odds:.2f}\n"
        f"📊 Оценка модели после quality-калибровки: {adjusted:.1f}%\n"
        f"📉 Консенсус рынка: {market:.1f}%\n"
        f"✅ Уверенность: {confidence:.1f}% | quality {q_score:.1f} | Букмекеров: {books} | Источников: {sources}\n"
        f"🧮 Canonical value: edge {edge:+.1f} п.п. | EV {ev:+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {commence}\n"
        f"💰 Сумма ставки: {stake:.2f} ({stake_pct:.2f}% от банка, capped)"
        f"{xg_line}\n"
        f"📚 Линия: {bookmaker or 'n/a'} / {source or 'n/a'}\n"
        "📝 Логика: ставка не прошла основной historical/quality guard, но после пересчёта от выбранного коэффициента "
        "остаётся положительный EV, достаточная уверенность и минимум 2 букмекера. Это не high-risk all-in сигнал, а малая тестовая ставка."
    )


def send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "missing_telegram_credentials"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with request.urlopen(request.Request(url, data=data, method="POST"), timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        return True, body[:1000]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def already_has_picks() -> bool:
    latest_picks = load_json(".data/exports/latest-picks.json", [])
    if isinstance(latest_picks, list) and len(latest_picks) > 0:
        return True
    debug = load_json(".logs/debug-last-run.json", {})
    summary = debug.get("summary") if isinstance(debug, dict) else {}
    if isinstance(summary, dict) and as_int(summary.get("published"), 0) > 0:
        return True
    return False


def main() -> int:
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": env_bool("CONTROLLED_FALLBACK_ENABLED", True),
        "published": False,
        "status": "not_started",
        "candidates_seen": 0,
        "evaluated": [],
    }
    if not report["enabled"]:
        report["status"] = "disabled"
        write_json("artifacts/controlled-fallback-report.json", report)
        return 0
    if already_has_picks():
        report["status"] = "skipped_existing_pick"
        write_json("artifacts/controlled-fallback-report.json", report)
        return 0

    debug = load_json(".logs/debug-last-run.json", {})
    candidates = []
    if isinstance(debug, dict):
        candidates = debug.get("candidates_before_quality") or []
    if not isinstance(candidates, list):
        candidates = []
    report["candidates_seen"] = len(candidates)
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}

    viable: list[tuple[tuple[float, float, float, float], dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        ok, reasons, metrics = evaluate_candidate(candidate)
        row = {
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "league_name": candidate.get("league_name"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "ok": ok,
            "reject_reasons": reasons,
            "metrics": metrics,
        }
        report["evaluated"].append(row)
        if ok:
            viable.append((candidate_score(candidate), candidate, metrics))

    if not viable:
        report["status"] = "no_viable_controlled_fallback"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    viable.sort(key=lambda item: item[0], reverse=True)
    _, chosen, metrics = viable[0]
    message = build_message(chosen, metrics, bankroll)
    dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)
    sent = False
    send_result = "dry_run"
    if not dry_run:
        sent, send_result = send_telegram(message)

    report.update({
        "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
        "published": bool(sent),
        "dry_run": bool(dry_run),
        "selected": {
            "match_key": chosen.get("match_key"),
            "home_team": chosen.get("home_team"),
            "away_team": chosen.get("away_team"),
            "league_name": chosen.get("league_name"),
            "family": chosen.get("family"),
            "selection": chosen.get("selection"),
            "point": chosen.get("point"),
            "odds": chosen.get("odds"),
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
