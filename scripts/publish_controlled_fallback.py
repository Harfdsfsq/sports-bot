from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
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


def norm_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def point_text(value: Any) -> str:
    if value in (None, "", "null"):
        return ""
    try:
        return f"{float(value):g}"
    except Exception:
        return str(value)


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


def candidate_dedupe_key(candidate: dict[str, Any]) -> str:
    """Stable key for one market/selection in one match.

    This intentionally ignores odds, because a repeated publication of the same
    match/market/selection at a slightly changed price is still a repeat for the
    Telegram channel.
    """
    selection_key = candidate.get("selection_key") or candidate.get("selection") or ""
    raw = "|".join(
        [
            norm_text(candidate.get("match_key")),
            norm_text(candidate.get("family")),
            norm_text(selection_key),
            norm_text(candidate.get("selection")),
            point_text(candidate.get("point")),
            norm_text(candidate.get("team_side")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def load_sent_index(path: str | Path) -> dict[str, Any]:
    payload = load_json(path, {"items": []})
    if not isinstance(payload, dict):
        return {"items": []}
    items = payload.get("items")
    if not isinstance(items, list):
        payload["items"] = []
    return payload


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def sent_recently(candidate: dict[str, Any], sent_index: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    if not env_bool("CONTROLLED_FALLBACK_DEDUPE_ENABLED", True):
        return False, ""
    key = candidate_dedupe_key(candidate)
    now = datetime.now(UTC)
    ttl_hours = env_float("CONTROLLED_FALLBACK_DEDUPE_TTL_HOURS", 48.0)
    cutoff = now - timedelta(hours=max(1.0, ttl_hours))

    for item in sent_index.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("dedupe_key") or "") != key:
            continue
        sent_at = parse_dt(item.get("sent_at"))
        if sent_at is None or sent_at >= cutoff:
            return True, "duplicate_fallback_sent_index"

    # Also guard against picks stored by the main app/state.
    collections = [
        "bets",
        "shadow_bets",
        "published_candidates",
        "fallback_published_candidates",
    ]
    for name in collections:
        rows = state.get(name) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("dedupe_key") or "") == key:
                return True, f"duplicate_state:{name}"
            if (
                norm_text(row.get("match_key")) == norm_text(candidate.get("match_key"))
                and norm_text(row.get("family")) == norm_text(candidate.get("family"))
                and norm_text(row.get("selection") or row.get("selection_key")) in {
                    norm_text(candidate.get("selection")),
                    norm_text(candidate.get("selection_key")),
                }
                and point_text(row.get("point")) == point_text(candidate.get("point"))
            ):
                return True, f"duplicate_state:{name}"
    return False, ""


def mark_sent(candidate: dict[str, Any], report: dict[str, Any], message: str, stake: float) -> None:
    if not env_bool("CONTROLLED_FALLBACK_DEDUPE_ENABLED", True):
        return
    index_path = Path(os.getenv("CONTROLLED_FALLBACK_SENT_INDEX_PATH", ".data/fallback-sent-index.json"))
    state_path = Path(os.getenv("STATE_PATH", ".data/state.json"))
    sent_index = load_sent_index(index_path)
    state = load_json(state_path, {})

    now = datetime.now(UTC).isoformat()
    key = candidate_dedupe_key(candidate)
    item = {
        "dedupe_key": key,
        "sent_at": now,
        "match_key": candidate.get("match_key"),
        "home_team": candidate.get("home_team"),
        "away_team": candidate.get("away_team"),
        "league_name": candidate.get("league_name"),
        "family": candidate.get("family"),
        "selection": candidate.get("selection"),
        "selection_key": candidate.get("selection_key"),
        "point": candidate.get("point"),
        "odds": candidate.get("odds"),
        "stake_amount": stake,
        "tier": report.get("selected", {}).get("tier"),
        "source": "controlled_fallback",
    }

    items = [x for x in (sent_index.get("items") or []) if isinstance(x, dict)]
    items = [x for x in items if str(x.get("dedupe_key") or "") != key]
    items.append(item)
    sent_index["updated_at"] = now
    sent_index["items"] = items[-500:]
    write_json(index_path, sent_index)

    if isinstance(state, dict):
        rows = state.setdefault("fallback_published_candidates", [])
        if isinstance(rows, list):
            rows.append({**item, "message": message})
            state["fallback_published_candidates"] = rows[-250:]
        write_json(state_path, state)


def selected_stake(bankroll: dict[str, Any]) -> tuple[float, float, float, float]:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65)
    max_stake = env_float("CONTROLLED_FALLBACK_MAX_STAKE", 5.0)
    min_stake = env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0)
    stake = min(max_stake, max(min_stake, bank * stake_pct / 100.0)) if bank > 0 else max_stake
    stake = min(stake, available) if available > 0 else 0.0
    return round(stake, 2), bank, open_exposure, available


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


def tier_names() -> list[str]:
    names = ["TIER_A", "TIER_B"]
    if env_bool("CONTROLLED_FALLBACK_TIER_C_ENABLED", True):
        names.append("TIER_C")
    return names


def tier_threshold(name: str, key: str, default: float | int | bool) -> float | int | bool:
    env_name = f"CONTROLLED_FALLBACK_{name}_{key}"
    if isinstance(default, bool):
        return env_bool(env_name, default)
    if isinstance(default, int):
        return env_int(env_name, default)
    return env_float(env_name, float(default))


def evaluate_tier(candidate: dict[str, Any], tier: str, metrics: dict[str, Any], duplicate_reason: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    family = norm_text(candidate.get("family"))
    allowed_families = env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,teamtotals")
    if family not in allowed_families:
        reasons.append(f"family_not_allowed:{family}")

    odds = metrics["odds"]
    if odds < env_float("CONTROLLED_FALLBACK_MIN_ODDS", 1.60):
        reasons.append("odds_below_min")
    tier_max_odds = env_float(f"CONTROLLED_FALLBACK_{tier}_MAX_ODDS", env_float("CONTROLLED_FALLBACK_MAX_ODDS", 2.90))
    if odds > tier_max_odds:
        reasons.append("odds_above_max")

    if metrics["books_count"] < int(tier_threshold(tier, "MIN_BOOKS", 2 if tier != "TIER_C" else 1)):
        reasons.append("books_below_min")
    if metrics["sources_count"] < env_int("CONTROLLED_FALLBACK_MIN_SOURCES", 1):
        reasons.append("sources_below_min")
    if metrics["confidence"] < float(tier_threshold(tier, "MIN_CONFIDENCE", 60.0 if tier != "TIER_B" else 58.0)):
        reasons.append("confidence_below_min")
    if metrics["quality_score"] < float(tier_threshold(tier, "MIN_QUALITY_SCORE", 62.0 if tier == "TIER_A" else 55.0 if tier == "TIER_B" else 50.0)):
        reasons.append("quality_below_min")
    if metrics["publication_score"] < float(tier_threshold(tier, "MIN_PUBLICATION_SCORE", 24.0 if tier == "TIER_A" else 20.0)):
        reasons.append("publication_score_below_min")
    if metrics["canonical_edge_pp"] < float(tier_threshold(tier, "MIN_CANONICAL_EDGE_PP", 2.0 if tier == "TIER_A" else 1.5)):
        reasons.append("canonical_edge_below_min")
    if metrics["canonical_ev_pct"] < float(tier_threshold(tier, "MIN_CANONICAL_EV_PCT", 3.5 if tier == "TIER_A" else 2.5 if tier == "TIER_B" else 4.0)):
        reasons.append("canonical_ev_below_min")

    allowed_stops = env_set(
        "CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS",
        "bad_historical_segment_guard,historical_guard,no_bet_quality_score_guard,post_calibration_probability_guard",
    )
    q_reasons = [norm_text(item) for item in metrics.get("quality_reasons") or []]
    if not q_reasons:
        reasons.append("missing_quality_reason")
    elif q_reasons[0] not in allowed_stops:
        reasons.append(f"quality_stop_not_allowed:{q_reasons[0]}")

    if metrics["canonical_edge_pp"] <= 0 or metrics["canonical_ev_pct"] <= 0:
        reasons.append("canonical_negative_value")

    allow_single = bool(tier_threshold(tier, "ALLOW_SINGLE_BOOK", tier == "TIER_C"))
    if metrics["books_count"] < 2 and not allow_single:
        reasons.append("single_book_rejected")
    if duplicate_reason:
        reasons.append(duplicate_reason)

    return (len(reasons) == 0, reasons)


def evaluate_candidate(candidate: dict[str, Any], sent_index: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str | None, list[str], dict[str, Any], dict[str, Any]]:
    metrics = candidate_metrics(candidate)
    duplicate, duplicate_reason = sent_recently(candidate, sent_index, state)
    tier_results: dict[str, Any] = {}
    for tier in tier_names():
        ok, reasons = evaluate_tier(candidate, tier, metrics, duplicate_reason if duplicate else "")
        tier_results[tier] = {"ok": ok, "reject_reasons": reasons}
        if ok:
            return True, tier, [], metrics, tier_results

    # Compact top-level reasons from the most permissive checked tier.
    last = tier_names()[-1]
    return False, None, list(tier_results.get(last, {}).get("reject_reasons") or []), metrics, tier_results


def candidate_score(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> tuple[float, float, float, float, float]:
    tier_rank = {"TIER_A": 3.0, "TIER_B": 2.0, "TIER_C": 1.0}.get(tier, 0.0)
    return (
        tier_rank,
        float(metrics.get("canonical_ev_pct") or -999.0),
        float(metrics.get("canonical_edge_pp") or -999.0),
        float(metrics.get("quality_score") or 0.0),
        float(metrics.get("publication_score") or 0.0),
    )


def market_title(family: str) -> str:
    return {
        "totals": "Тотал",
        "teamtotals": "Индивидуальный тотал",
        "teamTotals": "Индивидуальный тотал",
        "dnb": "Фора 0 / DNB",
        "btts": "Обе забьют",
        "h2h": "Исход",
        "spreads": "Фора",
    }.get(family, family)


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], bankroll: dict[str, Any], tier: str) -> tuple[str, float]:
    home = str(candidate.get("home_team") or "")
    away = str(candidate.get("away_team") or "")
    league = str(candidate.get("league_name") or "")
    family = str(candidate.get("family") or "")
    selection = str(candidate.get("selection") or "")
    point = candidate.get("point")
    stake, bank, open_exposure, available = selected_stake(bankroll)

    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    xg_line = ""
    if expected_home not in (None, "") and expected_away not in (None, ""):
        try:
            xg_line = f"\n📈 Ожидаемые голы: {float(expected_home):.2f} : {float(expected_away):.2f}"
        except Exception:
            pass

    point_suffix = "" if point in (None, "", "null") else f" ({point})"
    tier_text = {
        "TIER_A": "controlled fallback Tier A",
        "TIER_B": "controlled fallback Tier B",
        "TIER_C": "controlled fallback Tier C",
    }.get(tier, "controlled fallback")
    risk_text = (
        "Чистых quality-pass ставок не было; опубликован single-book кандидат с положительным canonical EV и минимальной суммой."
        if tier == "TIER_C" and int(metrics.get("books_count") or 0) < 2
        else "Чистых quality-pass ставок не было; опубликован лучший кандидат после повторной проверки value, odds и риска."
    )

    message = (
        "🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"⚠️ Режим: {tier_text}. {risk_text}\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {market_title(norm_text(family))} — {selection}{point_suffix}\n"
        f"💸 Коэффициент: {float(metrics['odds']):.2f}\n"
        f"📊 Скорректированная оценка: {float(metrics['adjusted_probability']) * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {float(metrics['market_probability']) * 100:.1f}%\n"
        f"✅ Уверенность: {float(metrics['confidence']):.1f}% | quality {float(metrics['quality_score']):.1f} | {tier}\n"
        f"📚 Линии: {int(metrics['books_count'])} | Источники: {int(metrics['sources_count'])} | {selected_bookmaker(candidate) or 'n/a'} / {selected_source(candidate) or 'n/a'}\n"
        f"🧮 Canonical value: edge {float(metrics['canonical_edge_pp']):+.1f} п.п. | EV {float(metrics['canonical_ev_pct']):+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {candidate.get('commence_time') or ''}\n"
        f"💰 Сумма ставки: {stake:.2f} (controlled cap)"
        f"{xg_line}\n"
        "📝 Комментарий: основной quality-layer не дал чистую ставку. "
        "Публикация разрешена только после повторного пересчёта EV от выбранного коэффициента."
    )
    return message, stake


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
    return isinstance(summary, dict) and as_int(summary.get("published"), 0) > 0


def build_no_pick_message(report: dict[str, Any]) -> str:
    reasons = Counter()
    for row in report.get("evaluated") or []:
        for reason in row.get("reject_reasons") or []:
            reasons[str(reason)] += 1
    top = "\n".join(f"• {reason} — {count}" for reason, count in reasons.most_common(6)) or "• нет кандидатов для fallback"
    return (
        "🧾 Отчёт по запуску бота\n"
        "❌ Прогнозов не было.\n\n"
        "Основной quality-layer не нашёл чистую ставку, а controlled fallback не нашёл безопасный резервный вариант.\n\n"
        f"Кандидатов fallback проверено: {int(report.get('candidates_seen') or 0)}\n"
        "Причины отказа:\n"
        f"{top}"
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
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    if already_has_picks():
        report["status"] = "skipped_existing_pick"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    debug = load_json(".logs/debug-last-run.json", {})
    candidates = debug.get("candidates_before_quality") if isinstance(debug, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    report["candidates_seen"] = len(candidates)
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}
    sent_index_path = os.getenv("CONTROLLED_FALLBACK_SENT_INDEX_PATH", ".data/fallback-sent-index.json")
    sent_index = load_sent_index(sent_index_path)
    state = load_json(os.getenv("STATE_PATH", ".data/state.json"), {})

    viable: list[tuple[tuple[float, float, float, float, float], dict[str, Any], dict[str, Any], str]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        ok, tier, reasons, metrics, tier_results = evaluate_candidate(candidate, sent_index, state if isinstance(state, dict) else {})
        row = {
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "league_name": candidate.get("league_name"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "dedupe_key": candidate_dedupe_key(candidate),
            "ok": ok,
            "tier": tier,
            "reject_reasons": reasons,
            "tier_results": tier_results,
            "metrics": metrics,
        }
        report["evaluated"].append(row)
        if ok and tier:
            viable.append((candidate_score(candidate, metrics, tier), candidate, metrics, tier))

    if not viable:
        report["status"] = "no_viable_controlled_fallback"
        if env_bool("CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT", True) and not env_bool("PUBLISH_DRY_RUN", False):
            sent, send_result = send_telegram(build_no_pick_message(report))
            report["no_pick_report_sent"] = bool(sent)
            report["telegram_result"] = send_result
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    viable.sort(key=lambda item: item[0], reverse=True)
    _, chosen, metrics, tier = viable[0]
    message, stake = build_message(chosen, metrics, bankroll, tier)
    dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)

    sent = False
    send_result = "dry_run"
    if not dry_run:
        sent, send_result = send_telegram(message)
    if sent:
        mark_sent(chosen, {"selected": {"tier": tier}}, message, stake)

    report.update(
        {
            "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
            "published": bool(sent),
            "dry_run": bool(dry_run),
            "selected": {
                "dedupe_key": candidate_dedupe_key(chosen),
                "match_key": chosen.get("match_key"),
                "home_team": chosen.get("home_team"),
                "away_team": chosen.get("away_team"),
                "league_name": chosen.get("league_name"),
                "family": chosen.get("family"),
                "selection": chosen.get("selection"),
                "point": chosen.get("point"),
                "odds": chosen.get("odds"),
                "tier": tier,
                "stake_amount": stake,
                "metrics": metrics,
            },
            "telegram_result": send_result,
            "message": message,
        }
    )
    write_json("artifacts/controlled-fallback-report.json", report)
    write_json(".data/exports/latest-controlled-fallback-report.json", report)
    return 0 if (sent or dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
