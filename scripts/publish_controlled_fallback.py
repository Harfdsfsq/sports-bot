from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
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


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


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
        return [str(item) for item in reasons if str(item).strip()]
    if isinstance(reasons, str) and reasons.strip():
        return [reasons.strip()]
    return []


def selected_bookmaker(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(candidate.get("bookmaker") or ss.get("selected_bookmaker") or ss.get("bookmaker") or "").strip()


def selected_source(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(ss.get("selected_source") or ss.get("source") or candidate.get("source") or "").strip()


def normalize_family(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "")


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    existing = str(candidate.get("fingerprint") or "").strip()
    if existing:
        return existing
    payload = "|".join(
        str(candidate.get(key) or "")
        for key in (
            "match_key",
            "family",
            "selection",
            "selection_key",
            "point",
            "team_side",
            "odds",
            "commence_time",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def candidate_score(candidate: dict[str, Any], metrics: dict[str, Any]) -> tuple[float, float, float, float, float]:
    tier_rank = {"A": 3.0, "B": 2.0, "C": 1.0}.get(str(metrics.get("fallback_tier") or ""), 0.0)
    return (
        tier_rank,
        as_float(metrics.get("canonical_ev_pct"), -999.0),
        as_float(metrics.get("canonical_edge_pp"), -999.0),
        as_float(metrics.get("quality_score"), 0.0),
        as_float(candidate.get("publication_score"), 0.0) + as_float(candidate.get("confidence"), 0.0) / 10.0,
    )


def base_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(candidate.get("odds"), 0.0)
    adjusted = as_float(candidate.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), 0.0)
    implied_probability_stored = as_float(candidate.get("implied_probability"), 0.0)
    fair_odds = as_float(candidate.get("fair_odds"), 0.0)
    confidence = as_float(candidate.get("confidence"), 0.0)
    books = as_int(candidate.get("books_count"), 0)
    sources = as_int(candidate.get("sources_count"), 0)
    q_payload = quality_payload(candidate)
    quality_score = as_float(q_payload.get("quality_score"), as_float(candidate.get("quality_score"), 0.0))
    publication_score = as_float(candidate.get("publication_score"), 0.0)
    q_reasons = quality_reasons(candidate)
    selected_implied = 1.0 / odds if odds > 1.0 else 0.0
    canonical_edge_pp = (adjusted - selected_implied) * 100.0 if odds > 1.0 else -999.0
    canonical_ev_pct = ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0
    market_edge_pp = (adjusted - market_prob) * 100.0 if market_prob > 0 else 0.0
    implied_mismatch = abs(selected_implied - implied_probability_stored) if implied_probability_stored > 0 else 0.0
    fair_from_market = 1.0 / market_prob if market_prob > 0 else 0.0
    return {
        "odds": round(odds, 4),
        "selected_implied_probability": round(selected_implied, 6),
        "stored_implied_probability": round(implied_probability_stored, 6),
        "implied_mismatch": round(implied_mismatch, 6),
        "adjusted_probability": round(adjusted, 6),
        "model_probability": round(model_prob, 6),
        "market_probability": round(market_prob, 6),
        "fair_odds": round(fair_odds, 6),
        "fair_odds_from_market": round(fair_from_market, 6),
        "canonical_edge_pp": round(canonical_edge_pp, 3),
        "market_edge_pp": round(market_edge_pp, 3),
        "canonical_ev_pct": round(canonical_ev_pct, 3),
        "confidence": round(confidence, 3),
        "quality_score": round(quality_score, 3),
        "publication_score": round(publication_score, 3),
        "books_count": books,
        "sources_count": sources,
        "quality_reasons": q_reasons,
        "selected_bookmaker": selected_bookmaker(candidate),
        "selected_source": selected_source(candidate),
    }


def common_rejections(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    family = normalize_family(candidate.get("family"))
    allowed_families = env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,teamTotals")
    if family not in {normalize_family(item) for item in allowed_families}:
        reasons.append(f"family_not_allowed:{family}")

    odds = as_float(metrics.get("odds"), 0.0)
    if odds < env_float("CONTROLLED_FALLBACK_MIN_ODDS", 1.55):
        reasons.append("odds_below_min")
    if odds > env_float("CONTROLLED_FALLBACK_MAX_ODDS", 2.85):
        reasons.append("odds_above_max")
    if as_int(metrics.get("sources_count"), 0) < env_int("CONTROLLED_FALLBACK_MIN_SOURCES", 1):
        reasons.append("sources_below_min")

    q_reasons = [str(item).strip().lower() for item in metrics.get("quality_reasons") or [] if str(item).strip()]
    allowed_stops = env_set(
        "CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS",
        "bad_historical_segment_guard,historical_guard,no_bet_quality_score_guard,post_calibration_probability_guard,post_calibration_edge_guard",
    )
    if not q_reasons:
        reasons.append("missing_quality_reason")
    elif q_reasons[0] not in allowed_stops:
        reasons.append(f"quality_stop_not_allowed:{q_reasons[0]}")

    if as_float(metrics.get("canonical_edge_pp"), -999.0) <= 0 or as_float(metrics.get("canonical_ev_pct"), -999.0) <= 0:
        reasons.append("canonical_negative_value")

    # Keep h2h rescue disabled by default; it caused the highest historical variance.
    if family == "h2h":
        reasons.append("h2h_not_allowed_in_controlled_fallback")

    return reasons


def passes_tier_a(candidate: dict[str, Any], metrics: dict[str, Any], common: list[str]) -> tuple[bool, list[str]]:
    reasons = list(common)
    if as_int(metrics.get("books_count"), 0) < env_int("CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS", 2):
        reasons.append("tier_a_books_below_min")
    if as_float(metrics.get("confidence"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE", 60.0):
        reasons.append("tier_a_confidence_below_min")
    if as_float(metrics.get("quality_score"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY_SCORE", 60.0):
        reasons.append("tier_a_quality_below_min")
    if as_float(metrics.get("publication_score"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_A_MIN_PUBLICATION_SCORE", 26.0):
        reasons.append("tier_a_publication_score_below_min")
    if as_float(metrics.get("canonical_edge_pp"), -999.0) < env_float("CONTROLLED_FALLBACK_TIER_A_MIN_CANONICAL_EDGE_PP", 1.5):
        reasons.append("tier_a_edge_below_min")
    if as_float(metrics.get("canonical_ev_pct"), -999.0) < env_float("CONTROLLED_FALLBACK_TIER_A_MIN_CANONICAL_EV_PCT", 3.0):
        reasons.append("tier_a_ev_below_min")
    return len(reasons) == 0, reasons


def passes_tier_b(candidate: dict[str, Any], metrics: dict[str, Any], common: list[str]) -> tuple[bool, list[str]]:
    reasons = list(common)
    if as_int(metrics.get("books_count"), 0) < env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", 2):
        reasons.append("tier_b_books_below_min")
    if as_float(metrics.get("odds"), 0.0) > env_float("CONTROLLED_FALLBACK_TIER_B_MAX_ODDS", 2.35):
        reasons.append("tier_b_odds_above_max")
    if as_float(metrics.get("confidence"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE", 61.0):
        reasons.append("tier_b_confidence_below_min")
    if as_float(metrics.get("quality_score"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY_SCORE", 52.0):
        reasons.append("tier_b_quality_below_min")
    if as_float(metrics.get("publication_score"), 0.0) < env_float("CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE", 22.0):
        reasons.append("tier_b_publication_score_below_min")
    if as_float(metrics.get("canonical_edge_pp"), -999.0) < env_float("CONTROLLED_FALLBACK_TIER_B_MIN_CANONICAL_EDGE_PP", 1.0):
        reasons.append("tier_b_edge_below_min")
    if as_float(metrics.get("canonical_ev_pct"), -999.0) < env_float("CONTROLLED_FALLBACK_TIER_B_MIN_CANONICAL_EV_PCT", 2.0):
        reasons.append("tier_b_ev_below_min")
    return len(reasons) == 0, reasons


def passes_tier_c_single_book(candidate: dict[str, Any], metrics: dict[str, Any], common: list[str]) -> tuple[bool, list[str]]:
    reasons = list(common)
    if not env_bool("CONTROLLED_FALLBACK_ALLOW_SINGLE_BOOK", False):
        reasons.append("single_book_tier_disabled")
    family = normalize_family(candidate.get("family"))
    allowed_single_families = {normalize_family(item) for item in env_set("CONTROLLED_FALLBACK_SINGLE_BOOK_ALLOWED_FAMILIES", "totals,teamTotals,dnb")}
    if family not in allowed_single_families:
        reasons.append(f"single_book_family_not_allowed:{family}")
    if as_int(metrics.get("books_count"), 0) != 1:
        reasons.append("single_book_tier_requires_exactly_one_book")
    allowed_books = {item.lower() for item in env_set("CONTROLLED_FALLBACK_SINGLE_BOOK_ALLOWED_BOOKMAKERS", "Bet365,Unibet,Pinnacle,Betfair,Bwin")}
    bookmaker = str(metrics.get("selected_bookmaker") or "").strip().lower()
    if bookmaker and bookmaker not in allowed_books:
        reasons.append(f"single_book_bookmaker_not_allowed:{bookmaker}")
    if as_float(metrics.get("odds"), 0.0) > env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MAX_ODDS", 2.85):
        reasons.append("single_book_odds_above_max")
    if as_float(metrics.get("confidence"), 0.0) < env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MIN_CONFIDENCE", 61.0):
        reasons.append("single_book_confidence_below_min")
    if as_float(metrics.get("quality_score"), 0.0) < env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MIN_QUALITY_SCORE", 52.0):
        reasons.append("single_book_quality_below_min")
    if as_float(metrics.get("publication_score"), 0.0) < env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MIN_PUBLICATION_SCORE", 30.0):
        reasons.append("single_book_publication_score_below_min")
    if as_float(metrics.get("canonical_edge_pp"), -999.0) < env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MIN_CANONICAL_EDGE_PP", 1.8):
        reasons.append("single_book_edge_below_min")
    if as_float(metrics.get("canonical_ev_pct"), -999.0) < env_float("CONTROLLED_FALLBACK_SINGLE_BOOK_MIN_CANONICAL_EV_PCT", 5.0):
        reasons.append("single_book_ev_below_min")
    return len(reasons) == 0, reasons


def evaluate_candidate(candidate: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    metrics = base_metrics(candidate)
    common = common_rejections(candidate, metrics)
    tier_results: dict[str, Any] = {}

    ok_a, reasons_a = passes_tier_a(candidate, metrics, common)
    tier_results["A"] = {"ok": ok_a, "reject_reasons": reasons_a}
    if ok_a:
        metrics["fallback_tier"] = "A"
        return True, [], metrics

    ok_b, reasons_b = passes_tier_b(candidate, metrics, common)
    tier_results["B"] = {"ok": ok_b, "reject_reasons": reasons_b}
    if ok_b:
        metrics["fallback_tier"] = "B"
        return True, [], metrics

    ok_c, reasons_c = passes_tier_c_single_book(candidate, metrics, common)
    tier_results["C"] = {"ok": ok_c, "reject_reasons": reasons_c}
    metrics["tier_results"] = tier_results
    if ok_c:
        metrics["fallback_tier"] = "C"
        return True, [], metrics

    # Return the shortest rejection path for readability, but keep all tier traces in metrics.
    best_reasons = min((reasons_a, reasons_b, reasons_c), key=len)
    return False, best_reasons, metrics


def estimate_stake(bankroll: dict[str, Any]) -> tuple[float, float, float, float]:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65)
    max_stake = env_float("CONTROLLED_FALLBACK_MAX_STAKE", 5.0)
    min_stake = env_float("CONTROLLED_FALLBACK_MIN_STAKE", 5.0)
    stake = min(max_stake, max(min_stake, bank * stake_pct / 100.0)) if bank > 0 else max_stake
    stake = min(stake, available) if available > 0 else 0.0
    return round(bank, 2), round(open_exposure, 2), round(available, 2), round(stake, 2)


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], bankroll: dict[str, Any]) -> str:
    home = str(candidate.get("home_team") or "")
    away = str(candidate.get("away_team") or "")
    league = str(candidate.get("league_name") or "")
    family = str(candidate.get("family") or "")
    selection = str(candidate.get("selection") or "")
    point = candidate.get("point")
    odds = as_float(metrics.get("odds"), 0.0)
    adjusted = as_float(metrics.get("adjusted_probability"), 0.0) * 100.0
    market = as_float(metrics.get("market_probability"), 0.0) * 100.0
    edge = as_float(metrics.get("canonical_edge_pp"), 0.0)
    ev = as_float(metrics.get("canonical_ev_pct"), 0.0)
    confidence = as_float(metrics.get("confidence"), 0.0)
    q_score = as_float(metrics.get("quality_score"), 0.0)
    books = as_int(metrics.get("books_count"), 0)
    sources = as_int(metrics.get("sources_count"), 0)
    tier = str(metrics.get("fallback_tier") or "?")
    bookmaker = selected_bookmaker(candidate) or "n/a"
    source = selected_source(candidate) or "n/a"
    expected_home = candidate.get("expected_home")
    expected_away = candidate.get("expected_away")
    commence = str(candidate.get("commence_time") or "")
    bank, open_exposure, available, stake = estimate_stake(bankroll)

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

    risk_note = (
        "⚠️ Режим: controlled fallback Tier C. Чистых quality-pass ставок не было; опубликован single-book кандидат "
        "с положительным canonical EV и минимальной суммой."
        if tier == "C"
        else "⚠️ Режим: controlled fallback. Чистых quality-pass ставок не было; опубликован кандидат после повторной проверки value и риска."
    )

    return (
        "🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"{risk_note}\n\n"
        f"1. {home} — {away}\n"
        f"🎯 Ставка: {market_title} — {selection}{point_text}\n"
        f"💸 Коэффициент: {odds:.2f}\n"
        f"📊 Скорректированная оценка: {adjusted:.1f}%\n"
        f"📉 Рынок/консенсус: {market:.1f}%\n"
        f"✅ Уверенность: {confidence:.1f}% | quality {q_score:.1f} | Tier {tier}\n"
        f"📚 Линии: {books} | Источники: {sources} | {bookmaker} / {source}\n"
        f"🧮 Canonical value: edge {edge:+.1f} п.п. | EV {ev:+.1f}%\n"
        f"🏆 Турнир: {league}\n"
        f"🕒 Начало: {commence}\n"
        f"💰 Сумма ставки: {stake:.2f} (controlled cap)"
        f"{xg_line}\n"
        "📝 Комментарий: ставка не прошла основной quality-layer, поэтому размер снижен. "
        "Публикация разрешена только потому, что после пересчёта от выбранного коэффициента остаётся положительный EV."
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


def update_candidate_for_fallback(candidate: dict[str, Any], metrics: dict[str, Any], message: str, sent: bool, bankroll: dict[str, Any]) -> dict[str, Any]:
    bank, _open_exposure, _available, stake = estimate_stake(bankroll)
    row = dict(candidate)
    row["fingerprint"] = candidate_fingerprint(candidate)
    row["controlled_fallback"] = True
    row["fallback_tier"] = metrics.get("fallback_tier")
    row["fallback_published_at"] = datetime.now(UTC).isoformat()
    row["status"] = "pending" if sent else "generated_controlled_fallback"
    row["telegram_sent"] = bool(sent)
    row["stake_amount"] = stake if sent else 0.0
    row["stake_pct"] = round((stake / bank * 100.0), 3) if sent and bank > 0 else 0.0
    row["bankroll_snapshot"] = bank
    row["risk_label"] = "controlled_fallback_single_book" if metrics.get("fallback_tier") == "C" else "controlled_fallback"
    row["fallback_metrics"] = metrics
    row["telegram_message"] = message
    source_summary = dict(row.get("source_summary") or {})
    source_summary["controlled_fallback"] = True
    source_summary["fallback_tier"] = metrics.get("fallback_tier")
    source_summary["selected_implied_probability"] = metrics.get("selected_implied_probability")
    source_summary["canonical_edge_pp"] = metrics.get("canonical_edge_pp")
    source_summary["canonical_ev_pct"] = metrics.get("canonical_ev_pct")
    row["source_summary"] = source_summary
    return row


def persist_fallback_pick(candidate: dict[str, Any], metrics: dict[str, Any], message: str, sent: bool, bankroll: dict[str, Any]) -> dict[str, Any]:
    row = update_candidate_for_fallback(candidate, metrics, message, sent, bankroll)
    write_json(".data/exports/latest-picks.json", [row])
    write_json(".data/exports/latest-controlled-fallback-pick.json", row)
    write_csv(".data/exports/latest-picks.csv", [row])
    write_json("artifacts/run-bot/latest-picks.json", [row])
    write_json("artifacts/run-bot/latest-controlled-fallback-pick.json", row)

    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return {"state_updated": False, "reason": "state_not_dict"}
    bets = state.setdefault("bets", [])
    if not isinstance(bets, list):
        state["bets"] = bets = []
    fp = row["fingerprint"]
    exists = any(isinstance(item, dict) and str(item.get("fingerprint") or "") == fp for item in bets)
    if exists:
        return {"state_updated": False, "reason": "duplicate_fingerprint", "fingerprint": fp}

    if sent:
        bets.append(row)
        bank_state = state.setdefault("bankroll", {})
        if isinstance(bank_state, dict):
            stake = as_float(row.get("stake_amount"), 0.0)
            bank_state["open_exposure"] = round(as_float(bank_state.get("open_exposure"), 0.0) + stake, 2)
            bank_state["total_staked"] = round(as_float(bank_state.get("total_staked"), 0.0) + stake, 2)
            bank_state["bets_published"] = as_int(bank_state.get("bets_published"), 0) + 1
        state["updated_at"] = datetime.now(UTC).isoformat()
        write_json(".data/state.json", state)
        return {"state_updated": True, "fingerprint": fp}
    return {"state_updated": False, "reason": "not_sent", "fingerprint": fp}


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
    candidates: list[dict[str, Any]] = []
    if isinstance(debug, dict):
        raw_candidates = debug.get("candidates_before_quality") or []
        if isinstance(raw_candidates, list):
            candidates = [item for item in raw_candidates if isinstance(item, dict)]
    report["candidates_seen"] = len(candidates)
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}

    viable: list[tuple[tuple[float, float, float, float, float], dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
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
            viable.append((candidate_score(candidate, metrics), candidate, metrics))

    if not viable:
        report["status"] = "no_viable_controlled_fallback"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    viable.sort(key=lambda item: item[0], reverse=True)
    _, chosen, metrics = viable[0]
    message = build_message(chosen, metrics, bankroll)
    dry_run = env_bool("PUBLISH_DRY_RUN", False) or not env_bool("CONTROLLED_FALLBACK_SEND_TELEGRAM", True)
    sent = False
    send_result = "dry_run"
    if not dry_run:
        sent, send_result = send_telegram(message)

    persistence = persist_fallback_pick(chosen, metrics, message, sent, bankroll)

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
        "persistence": persistence,
        "message": message,
    })
    write_json("artifacts/controlled-fallback-report.json", report)
    write_json(".data/exports/latest-controlled-fallback-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if (sent or dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
