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
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
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


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


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
    ss = candidate.get("source_summary") or {}
    if isinstance(ss, dict):
        reasons = ss.get("quality_reasons") or []
        if isinstance(reasons, list):
            return [str(item) for item in reasons if str(item).strip()]
    return []


def selected_bookmaker(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(candidate.get("bookmaker") or ss.get("selected_bookmaker") or ss.get("bookmaker") or "").strip()


def selected_source(candidate: dict[str, Any]) -> str:
    ss = candidate.get("source_summary") or {}
    return str(ss.get("selected_source") or ss.get("source") or "").strip()


def family_norm(candidate: dict[str, Any]) -> str:
    return str(candidate.get("family") or "").strip().lower()


def normalize_candidate(raw: dict[str, Any], source: str) -> dict[str, Any]:
    row = dict(raw)
    row.setdefault("_candidate_source", source)
    # State rows can store prediction_id/fingerprint, but not always selection_key.
    if not row.get("selection_key"):
        family = str(row.get("family") or "").lower()
        selection = str(row.get("selection") or "").lower()
        point = "" if row.get("point") in (None, "") else str(row.get("point"))
        team_side = str(row.get("team_side") or "").lower()
        row["selection_key"] = "|".join([family, selection, point, team_side])
    return row


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
        if ts and ts >= cutoff:
            result[key] = row
    return result


def row_was_really_published(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    return bool(row.get("telegram_sent")) or status in {"pending", "won", "lost", "push", "void", "half_won", "half_lost"}


def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:
    key = dedupe_key(candidate)
    if key in sent_index:
        return "duplicate_fallback_sent_index"
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return None
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_BETS", True):
        for row in state.get("bets") or []:
            if isinstance(row, dict) and row_was_really_published(row) and dedupe_key(row) == key:
                return "duplicate_state:bets"
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED", True):
        for row in state.get("published_candidates") or []:
            if isinstance(row, dict) and row_was_really_published(row) and dedupe_key(row) == key:
                return "duplicate_state:published_candidates"
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW", False):
        for row in state.get("shadow_bets") or []:
            if isinstance(row, dict) and dedupe_key(row) == key:
                return "duplicate_state:shadow_bets"
    return None


def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(candidate.get("odds"), as_float(candidate.get("selected_price"), 0.0))
    adjusted = as_float(candidate.get("adjusted_probability"), as_float(candidate.get("final_probability"), 0.0))
    ss = candidate.get("source_summary") or {}
    if adjusted <= 0 and isinstance(ss, dict):
        adjusted = as_float(ss.get("adjusted_probability"), 0.0)
    model_prob = as_float(candidate.get("model_probability"), 0.0)
    market_prob = as_float(candidate.get("market_probability"), as_float(candidate.get("consensus_probability"), 0.0))
    confidence = as_float(candidate.get("confidence"), 0.0)
    books = as_int(candidate.get("books_count"), 0)
    sources = as_int(candidate.get("sources_count"), 0)
    q = quality_payload(candidate)
    quality_score = as_float(q.get("quality_score"), as_float(candidate.get("quality_score"), as_float((candidate.get("source_summary") or {}).get("quality_score"), 0.0)))
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


def hard_reject_reasons(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fam = family_norm(candidate)
    if fam not in env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,teamtotals,teamTotals,btts"):
        reasons.append(f"family_not_allowed:{fam}")
    dup = duplicate_reason(candidate, sent_index)
    if dup:
        reasons.append(dup)
    if metrics["odds"] < env_float("CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS", 1.55):
        reasons.append("odds_below_global_min")
    if metrics["odds"] > env_float("CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS", 3.10):
        reasons.append("odds_above_global_max")
    if metrics["canonical_edge_pp"] <= 0 or metrics["canonical_ev_pct"] <= 0:
        reasons.append("canonical_negative_value")
    if metrics["adjusted_probability"] <= 0 or metrics["adjusted_probability"] >= 0.98:
        reasons.append("bad_adjusted_probability")
    if metrics["books_count"] <= 0:
        reasons.append("missing_books")
    if metrics["sources_count"] <= 0:
        reasons.append("missing_sources")
    if fam == "h2h" and metrics["odds"] > env_float("CONTROLLED_FALLBACK_H2H_MAX_ODDS", 2.25):
        reasons.append("h2h_rescue_odds_too_high")
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
    if metrics["odds"] > env_float(prefix + "MAX_ODDS", env_float("CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS", 3.10)):
        reasons.append(f"tier_{tier.lower()}_odds_above_max")
    q_reasons = [r.lower() for r in metrics.get("quality_reasons") or []]
    allowed_stops = env_set(prefix + "ALLOWED_QUALITY_STOPS", os.getenv("CONTROLLED_FALLBACK_ALLOWED_QUALITY_STOPS", "bad_historical_segment_guard,historical_guard,no_bet_quality_score_guard,post_calibration_probability_guard,post_calibration_edge_guard"))
    if q_reasons and q_reasons[0] not in allowed_stops:
        reasons.append(f"tier_{tier.lower()}_quality_stop_not_allowed:{q_reasons[0]}")
    # Candidates sourced from latest-picks may already be clean. Do not require a quality reason for them.
    if not q_reasons and str(candidate.get("_candidate_source") or "") not in {"latest_picks"}:
        reasons.append(f"tier_{tier.lower()}_missing_quality_reason")
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
            return True, [], metrics, f"Tier {tier}"
        all_tier_reasons.extend(reasons)
    return False, all_tier_reasons, metrics, None


def candidate_rank(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> tuple[float, float, float, float, float]:
    tier_bonus = {"Tier A": 30.0, "Tier B": 15.0, "Tier C": 0.0}.get(tier, 0.0)
    return (
        tier_bonus + float(metrics["canonical_ev_pct"]),
        float(metrics["canonical_edge_pp"]),
        float(metrics["quality_score"]),
        float(metrics["publication_score"]),
        float(metrics["confidence"]),
    )


def candidate_in_window(candidate: dict[str, Any]) -> bool:
    dt = parse_dt(candidate.get("commence_time"))
    if dt is None:
        return True
    now = datetime.now(UTC)
    lead_minutes = env_int("MIN_KICKOFF_LEAD_MINUTES", 20)
    window_hours = env_int("PUBLISH_WINDOW_HOURS", 24)
    return now + timedelta(minutes=lead_minutes) <= dt <= now + timedelta(hours=window_hours)


def candidate_from_state_is_eligible(row: dict[str, Any]) -> bool:
    if row_was_really_published(row):
        return False
    status = str(row.get("status") or "").lower()
    if status not in {"shadow_pending", "generated", "", "shadow"}:
        return False
    return candidate_in_window(row)


def collect_candidates() -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(items: Any, source: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            row = normalize_candidate(item, source)
            if not candidate_in_window(row):
                counts[f"{source}_outside_window"] += 1
                continue
            key = dedupe_key(row)
            if key in seen:
                counts[f"{source}_duplicate_in_pool"] += 1
                continue
            seen.add(key)
            rows.append(row)
            counts[source] += 1

    if env_bool("CONTROLLED_FALLBACK_INCLUDE_LATEST_PICKS", True):
        add_many(load_json(".data/exports/latest-picks.json", []), "latest_picks")

    if env_bool("CONTROLLED_FALLBACK_INCLUDE_DEBUG_CANDIDATES", True):
        for path in [".logs/debug-last-run.json", "artifacts/run-bot/debug-last-run.json"]:
            debug = load_json(path, {})
            if isinstance(debug, dict):
                add_many(debug.get("candidates_before_quality") or [], "debug_candidates_before_quality")
                add_many(debug.get("candidates_after_quality") or [], "debug_candidates_after_quality")
                add_many(debug.get("published_candidates") or [], "debug_published_candidates")

    if env_bool("CONTROLLED_FALLBACK_INCLUDE_STATE_SHADOW", True):
        state = load_json(".data/state.json", {})
        if isinstance(state, dict):
            shadow = []
            for item in state.get("shadow_bets") or []:
                if isinstance(item, dict) and candidate_from_state_is_eligible(item):
                    shadow.append(item)
            add_many(shadow[-env_int("CONTROLLED_FALLBACK_MAX_STATE_SHADOW_CANDIDATES", 40):], "state_shadow_bets")

    return rows, dict(counts)


def send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "missing_telegram_credentials"
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


def build_message(candidate: dict[str, Any], metrics: dict[str, Any], tier: str, bankroll: dict[str, Any]) -> str:
    bank = as_float(bankroll.get("current_balance"), as_float(bankroll.get("starting_balance"), 0.0))
    open_exposure = as_float(bankroll.get("open_exposure"), 0.0)
    available = max(0.0, bank - open_exposure)
    stake_pct = env_float("CONTROLLED_FALLBACK_STAKE_PCT", 0.65)
    max_stake = env_float(f"CONTROLLED_FALLBACK_MAX_STAKE_{tier.replace(' ', '_').upper()}", 5.0)
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
        except Exception:
            pass
    risk_note = {
        "Tier A": "контролируемый fallback Tier A: 2+ букмекера и нормальный запас value.",
        "Tier B": "контролируемый fallback Tier B: ставка снижена, потому что основной quality-layer не дал чистую ставку.",
        "Tier C": "controlled fallback Tier C: single-book/пограничный резерв, минимальная тестовая сумма.",
    }.get(tier, "controlled fallback")
    return (
        "🔥 1 контролируемый прогноз на ближайшие 24 часа\n\n"
        f"💼 Банк: {bank:.2f} | Открытый риск: {open_exposure:.2f} | Доступно: {available:.2f}\n\n"
        f"⚠️ Режим: {risk_note}\n\n"
        f"1. {candidate.get('home_team') or ''} — {candidate.get('away_team') or ''}\n"
        f"🎯 Ставка: {market_title(family_norm(candidate))} — {candidate.get('selection') or ''}{point_text}\n"
        f"💸 Коэффициент: {metrics['odds']:.2f}\n"
        f"📊 Скорректированная оценка: {metrics['adjusted_probability'] * 100:.1f}%\n"
        f"📉 Рынок/консенсус: {metrics['market_probability'] * 100:.1f}%\n"
        f"✅ Уверенность: {metrics['confidence']:.1f}% | quality {metrics['quality_score']:.1f} | {tier}\n"
        f"📚 Линии: {metrics['books_count']} | Источники: {metrics['sources_count']} | {selected_bookmaker(candidate) or 'n/a'} / {selected_source(candidate) or 'n/a'}\n"
        f"🧮 Canonical value: edge {metrics['canonical_edge_pp']:+.1f} п.п. | EV {metrics['canonical_ev_pct']:+.1f}%\n"
        f"🏆 Турнир: {candidate.get('league_name') or ''}\n"
        f"🕒 Начало: {candidate.get('commence_time') or ''}\n"
        f"💰 Сумма ставки: {stake:.2f} (controlled cap)"
        f"{xg_line}\n"
        "📝 Комментарий: основной quality-layer не дал чистую ставку. Публикация разрешена только после повторного пересчёта EV от выбранного коэффициента."
    )


def build_no_pick_message(report: dict[str, Any]) -> str:
    counter: Counter[str] = Counter()
    for item in report.get("evaluated") or []:
        for reason in item.get("reject_reasons") or []:
            counter[str(reason)] += 1
    lines = [
        "🧾 Отчёт по запуску бота",
        "❌ Прогнозов не было.",
        "",
        "Основной quality-layer не нашёл чистую ставку, а controlled fallback не нашёл безопасный резервный вариант.",
        "",
        f"Кандидатов fallback проверено: {report.get('candidates_seen', 0)}",
    ]
    pool_counts = report.get("candidate_pool_counts") or {}
    if pool_counts:
        lines.append("Пул кандидатов:")
        for key, count in sorted(pool_counts.items()):
            lines.append(f"• {key}: {count}")
    if counter:
        lines.append("Причины отказа:")
        for reason, count in counter.most_common(8):
            lines.append(f"• {reason} — {count}")
    return "\n".join(lines)


def main() -> int:
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": env_bool("CONTROLLED_FALLBACK_ENABLED", True),
        "published": False,
        "status": "not_started",
        "candidates_seen": 0,
        "evaluated": [],
        "dedupe_policy": {
            "sent_index": True,
            "state_bets": env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_BETS", True),
            "state_published": env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED", True),
            "state_shadow": env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW", False),
            "generated_rows_block": False,
        },
    }
    if not report["enabled"]:
        report["status"] = "disabled"
        write_json("artifacts/controlled-fallback-report.json", report)
        write_json(".data/exports/latest-controlled-fallback-report.json", report)
        return 0

    sent_index = prune_sent_index(load_sent_index(), env_int("CONTROLLED_FALLBACK_DEDUPE_HOURS", 72))
    debug = load_json(".logs/debug-last-run.json", {})
    bankroll = (debug.get("bankroll") or {}) if isinstance(debug, dict) else {}
    candidates, pool_counts = collect_candidates()
    report["candidates_seen"] = len(candidates)
    report["candidate_pool_counts"] = pool_counts

    viable: list[tuple[tuple[float, float, float, float, float], dict[str, Any], dict[str, Any], str]] = []
    max_evaluated_rows = env_int("CONTROLLED_FALLBACK_REPORT_MAX_EVALUATED", 80)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        ok, reasons, metrics, tier = evaluate_candidate(candidate, sent_index)
        row = {
            "source": candidate.get("_candidate_source"),
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "league_name": candidate.get("league_name"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "ok": ok,
            "tier": tier,
            "reject_reasons": reasons,
            "metrics": metrics,
        }
        if len(report["evaluated"]) < max_evaluated_rows:
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
        }
        save_sent_index(sent_index)

    report.update({
        "status": "published" if sent else ("dry_run_selected" if dry_run else "send_failed"),
        "published": bool(sent),
        "dry_run": bool(dry_run),
        "selected": {
            "dedupe_key": key,
            "source": chosen.get("_candidate_source"),
            "match_key": chosen.get("match_key"),
            "home_team": chosen.get("home_team"),
            "away_team": chosen.get("away_team"),
            "league_name": chosen.get("league_name"),
            "family": chosen.get("family"),
            "selection": chosen.get("selection"),
            "point": chosen.get("point"),
            "odds": chosen.get("odds"),
            "tier": tier,
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
