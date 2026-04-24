from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

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


def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    books = as_int(candidate.get("books_count"), 0)
    sources = as_int(candidate.get("sources_count"), 0)
    quality_score = as_float(quality_payload(candidate).get("quality_score"), as_float(candidate.get("quality_score"), 0.0))
    publication_score = as_float(candidate.get("publication_score"), 0.0)
    selected_implied = 1.0 / odds if odds > 1.0 else 0.0
    canonical_edge_pp = (adjusted - selected_implied) * 100.0 if odds > 1.0 else -999.0
    canonical_ev_pct = ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0
    market_edge_pp = (adjusted - market_prob) * 100.0 if market_prob > 0 else 0.0
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
        "publication_score": round(publication_score, 3),
        "books_count": books,
        "sources_count": sources,
        "quality_reasons": quality_reasons(candidate),
    }


def evaluate_tier(candidate: dict[str, Any], tier: str, metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    prefix = f"CONTROLLED_FALLBACK_{tier}_"
    reasons: list[str] = []
    family = str(candidate.get("family") or "").strip().lower()
    allowed_families = env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,teamtotals")
    if family not in allowed_families:
        reasons.append(f"family_not_allowed:{family}")

    odds = metrics["odds"]
    books = int(metrics["books_count"])
    sources = int(metrics["sources_count"])
    confidence = float(metrics["confidence"])
    quality_score = float(metrics["quality_score"])
    publication_score = float(metrics["publication_score"])
    canonical_edge_pp = float(metrics["canonical_edge_pp"])
    canonical_ev_pct = float(metrics["canonical_ev_pct"])
    q_reasons = list(metrics.get("quality_reasons") or [])

    if odds < env_float("CONTROLLED_FALLBACK_MIN_ODDS", 1.60):
        reasons.append("odds_below_min")
    tier_max_odds = env_float(prefix + "MAX_ODDS", env_float("CONTROLLED_FALLBACK_MAX_ODDS", 2.90))
    if odds > tier_max_odds:
        reasons.append("odds_above_max")
    if books < env_int(prefix + "MIN_BOOKS", 2):
        reasons.append("books_below_min")
    if sources < env_int("CONTROLLED_FALLBACK_MIN_SOURCES", 1):
        reasons.append("sources_below_min")
    if confidence < env_float(prefix + "MIN_CONFIDENCE", 60.0):
        reasons.append("confidence_below_min")
    if quality_score < env_float(prefix + "MIN_QUALITY_SCORE", 60.0):
        reasons.append("quality_below_min")
    if publication_score < env_float(prefix + "MIN_PUBLICATION_SCORE", 20.0):
        reasons.append("publication_score_below_min")
    if canonical_edge_pp < env_float(prefix + "MIN_CANONICAL_EDGE_PP", 2.0):
        reasons.append("canonical_edge_below_min")
    if canonical_ev_pct < env_float(prefix + "MIN_CANONICAL_EV_PCT", 3.0):
        reasons.append("canonical_ev_below_min")

    allowed_stops = env_set(
        "CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS",
        "bad_historical_segment_guard,no_bet_quality_score_guard,post_calibration_probability_guard,historical_guard",
    )
    if not q_reasons:
        reasons.append("missing_quality_reason")
    elif q_reasons[0].strip().lower() not in allowed_stops:
        reasons.append(f"quality_stop_not_allowed:{q_reasons[0]}")

    if canonical_edge_pp <= 0 or canonical_ev_pct <= 0:
        reasons.append("canonical_negative_value")

    if tier == "TIER_C":
        if not env_bool("CONTROLLED_FALLBACK_TIER_C_ENABLED", True):
            reasons.append("tier_c_disabled")
        if books < 2 and not env_bool("CONTROLLED_FALLBACK_TIER_C_ALLOW_SINGLE_BOOK", True):
            reasons.append("single_book_rejected")
    elif books < 2:
        reasons.append("single_book_rejected")

    return len(reasons) == 0, reasons


def ranking_key(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> tuple[float, float, float, float, float]:
    tier_bonus = {"TIER_A": 3.0, "TIER_B": 2.0, "TIER_C": 1.0}.get(tier, 0.0)
    return (
        tier_bonus,
        float(metrics.get("canonical_ev_pct") or -999.0),
        float(metrics.get("canonical_edge_pp") or -999.0),
        float(metrics.get("quality_score") or 0.0),
        float(metrics.get("publication_score") or 0.0),
    )


def send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "missing_telegram_credentials"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with request.urlopen(request.Request(url, data=data, method="POST"), timeout=20) as response:
            return True, response.read().decode("utf-8", errors="replace")[:1000]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def already_has_picks() -> bool:
    latest_picks = load_json(".data/exports/latest-picks.json", [])
    if isinstance(latest_picks, list) and len(latest_picks) > 0:
        return True
    debug = load_json(".logs/debug-last-run.json", {})
    summary = debug.get("summary") if isinstance(debug, dict) else {}
    return isinstance(summary, dict) and as_int(summary.get("published"), 0) > 0


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], bankroll: dict[str, Any], tier: str) -> str:
    home = str(candidate.get("home_team") or "")
    away = str(candidate.get("away_team") or "")
    league = str(candidate.get("league_name") or "")
    family = str(candidate.get("family") or "")
    selection = str(candidate.get("selection") or "")
    point = candidate.get("point")
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake = min(env_float("CONTROLLED_FALLBACK_MAX_STAKE", 5.0), max(env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0), bank * env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65) / 100.0)) if bank > 0 else env_float("CONTROLLED_FALLBACK_MAX_STAKE", 5.0)
    stake = round(min(stake, available), 2) if available > 0 else 0.0

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    xg_line = ""
    if expected_home not in (None, "") and expected_away not in (None, ""):
        try:
            xg_line = f"\n📈 Ожидаемые голы: {float(expected_home):.2f} : {float(expected_away):.2f}"
        except Exception:
            pass

    market_title = {
        "totals": "Тотал",
        "dnb": "Фора 0 / DNB",
        "btts": "Обе забьют",
        "h2h": "Исход",
        "spreads": "Фора",
        "teamTotals": "Индивидуальный тотал",
        "teamtotals": "Индивидуальный тотал",
    }.get(family, family)
    point_text = "" if point in (None, "", "null") else f" ({point})"
    tier_text = tier.replace("TIER_", "Tier ")

    return (
        f"🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"⚠️ Режим: controlled fallback {tier_text}. Чистых quality-pass ставок не было; ставка снижена и помечена как тестовая.\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {market_title} — {selection}{point_text}\n"
        f"💸 Коэффициент: {float(metrics['odds']):.2f}\n"
        f"📊 Скорректированная оценка: {float(metrics['adjusted_probability']) * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {float(metrics['market_probability']) * 100:.1f}%\n"
        f"✅ Уверенность: {float(metrics['confidence']):.1f}% | quality {float(metrics['quality_score']):.1f} | {tier_text}\n"
        f"📚 Линии: {int(metrics['books_count'])} | Источники: {int(metrics['sources_count'])} | {selected_bookmaker(candidate) or 'n/a'} / {selected_source(candidate) or 'n/a'}\n"
        f"🧮 Canonical value: edge {float(metrics['canonical_edge_pp']):+.1f} п.п. | EV {float(metrics['canonical_ev_pct']):+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {candidate.get('commence_time') or ''}\n"
        f"💰 Сумма ставки: {stake:.2f} (controlled cap)"
        f"{xg_line}\n"
        f"📝 Комментарий: основной quality-layer не дал чистую ставку. Публикация разрешена только после повторного пересчёта EV от выбранного коэффициента."
    )


def build_no_pick_message(debug: dict[str, Any], report: dict[str, Any]) -> str:
    summary = debug.get("summary") if isinstance(debug, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    rejections = summary.get("rejections") or summary.get("rejection_reasons") or {}
    quality_rejections = summary.get("quality_rejections") or {}
    top_reasons = []
    if isinstance(rejections, dict):
        for k, v in sorted(rejections.items(), key=lambda item: int(item[1] or 0), reverse=True)[:4]:
            top_reasons.append(f"• {k} — {v}")
    q_reasons = []
    if isinstance(quality_rejections, dict):
        for k, v in sorted(quality_rejections.items(), key=lambda item: int(item[1] or 0), reverse=True)[:4]:
            q_reasons.append(f"• {k} — {v}")
    rejected = report.get("evaluated") or []
    return (
        "🧾 Отчёт по запуску бота\n"
        f"🕒 Время запуска: {summary.get('created_at') or debug.get('created_at') or ''}\n"
        f"📅 Окно публикации: {summary.get('publish_window_hours', 'n/a')} ч | Мин. запас до матча: {summary.get('min_kickoff_lead_minutes', 'n/a')} мин\n"
        f"⚽ Матчей в окне: {summary.get('matches_seen', 0)} | С офферами: {summary.get('matches_with_offers', 0)} | Контекстов: {summary.get('contexts_built', 0)}\n"
        f"🧠 Кандидаты: до quality {summary.get('candidates_before_quality', summary.get('candidates', 0))} | после quality {summary.get('candidates_after_quality', 0)} | к публикации 0\n"
        "❌ В этот запуск прогнозов не было.\n"
        f"Controlled fallback тоже не нашёл безопасный вариант. Проверено кандидатов: {len(rejected)}.\n"
        + ("\nПочему нет прогноза:\n" + "\n".join(top_reasons) if top_reasons else "")
        + ("\nQuality стопоры:\n" + "\n".join(q_reasons) if q_reasons else "")
    )


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
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    debug = load_json(".logs/debug-last-run.json", {})
    candidates = debug.get("candidates_before_quality") if isinstance(debug, dict) else []
    candidates = candidates if isinstance(candidates, list) else []
    report["candidates_seen"] = len(candidates)
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}

    viable: list[tuple[tuple[float, float, float, float, float], str, dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        metrics = candidate_metrics(candidate)
        tier_results: dict[str, Any] = {}
        selected_tier = ""
        selected_reasons: list[str] = []
        for tier in ("TIER_A", "TIER_B", "TIER_C"):
            ok, reasons = evaluate_tier(candidate, tier, metrics)
            tier_results[tier] = {"ok": ok, "reject_reasons": reasons}
            if ok and not selected_tier:
                selected_tier = tier
                selected_reasons = reasons
        row = {
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "league_name": candidate.get("league_name"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "ok": bool(selected_tier),
            "tier": selected_tier or None,
            "reject_reasons": selected_reasons if selected_tier else tier_results.get("TIER_C", {}).get("reject_reasons", []),
            "tier_results": tier_results,
            "metrics": metrics,
        }
        report["evaluated"].append(row)
        if selected_tier:
            viable.append((ranking_key(candidate, metrics, selected_tier), selected_tier, candidate, metrics))

    if viable:
        viable.sort(key=lambda item: item[0], reverse=True)
        _, tier, chosen, metrics = viable[0]
        message = build_message(chosen, metrics, bankroll, tier)
        dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)
        sent = False
        send_result = "dry_run"
        if not dry_run:
            sent, send_result = send_telegram(message)
        report.update({
            "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
            "published": bool(sent),
            "dry_run": bool(dry_run),
            "selected_tier": tier,
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
    else:
        report["status"] = "no_viable_controlled_fallback"
        if env_bool("CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT", True):
            message = build_no_pick_message(debug if isinstance(debug, dict) else {}, report)
            dry_run = env_bool("PUBLISH_DRY_RUN", False)
            sent, send_result = (False, "dry_run")
            if not dry_run:
                sent, send_result = send_telegram(message)
            report.update({
                "no_pick_report_sent": bool(sent),
                "dry_run": bool(dry_run),
                "telegram_result": send_result,
                "message": message,
            })

    write_json("artifacts/controlled-fallback-report.json", report)
    write_json(".data/exports/latest-controlled-fallback-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
