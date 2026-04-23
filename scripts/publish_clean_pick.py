#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any
import sys

try:
    import httpx
except Exception:
    httpx = None


EXPORT_ROOT = Path(".data/exports")
REPORT_PATH = EXPORT_ROOT / "main-clean-publish-report.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except Exception:
        return default


def _to_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _allowed_families() -> set[str]:
    raw = os.getenv("MAIN_CLEAN_ALLOWED_FAMILIES", "totals,dnb")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _extract_candidates() -> list[dict[str, Any]]:
    candidates = _load_json(EXPORT_ROOT / "latest-picks.json", [])
    if isinstance(candidates, list):
        return [item for item in candidates if isinstance(item, dict)]
    return []


def _candidate_quality(candidate: dict[str, Any]) -> tuple[str, float, list[str]]:
    source_summary = dict(candidate.get("source_summary") or {})
    diagnostics = dict(candidate.get("diagnostics") or {})
    quality = dict(diagnostics.get("quality") or {})
    status = str(
        quality.get("status")
        or source_summary.get("quality_status")
        or ""
    ).strip()
    score = _to_float(
        quality.get("quality_score")
        if quality
        else source_summary.get("quality_score"),
        0.0,
    )
    reasons = list(quality.get("reasons") or source_summary.get("quality_reasons") or [])
    return status, score, [str(item) for item in reasons]


def _signal_score(candidate: dict[str, Any]) -> float:
    analysis = dict(candidate.get("analysis") or {})
    profile = dict(analysis.get("signal_profile") or {})
    if profile:
        return _to_float(profile.get("score"), 0.0)
    source_summary = dict(candidate.get("source_summary") or {})
    return _to_float(source_summary.get("signal_score"), 0.0)


def _risk_flags(candidate: dict[str, Any]) -> list[str]:
    source_summary = dict(candidate.get("source_summary") or {})
    flags = []
    if int(candidate.get("sources_count") or 0) <= 1:
        flags.append("single-source")
    risk_label = str(candidate.get("risk_label") or "").strip().lower()
    if risk_label:
        flags.append(risk_label)
    for key in ("risk_flags", "risk_reasons"):
        for item in (source_summary.get(key) or []):
            text = str(item).strip()
            if text:
                flags.append(text)
    # add heuristics from reasons text
    for item in candidate.get("reasons") or []:
        low = str(item).lower()
        if "fallback" in low or "emergency" in low or "historical" in low or "last_resort" in low:
            flags.append(low)
    return flags


def _implied_from_odds(odds: float) -> float | None:
    if odds <= 1.0001:
        return None
    return 1.0 / odds


def _candidate_issues(candidate: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    family = str(candidate.get("family") or "").strip()
    if family not in _allowed_families():
        issues.append(f"family_not_allowed:{family}")

    odds = _to_float(candidate.get("odds"), 0.0)
    confidence = _to_float(candidate.get("confidence"), 0.0)
    edge_pct = _to_float(candidate.get("edge_pct"), 0.0)
    books_count = int(candidate.get("books_count") or 0)
    adjusted_probability = _to_float(candidate.get("adjusted_probability"), 0.0)
    final_probability = _to_float(candidate.get("final_probability"), adjusted_probability)
    implied_probability = _to_float(candidate.get("implied_probability"), 0.0)
    source_summary = dict(candidate.get("source_summary") or {})
    ss_adjusted_probability = _to_float(source_summary.get("adjusted_probability"), adjusted_probability)

    status, quality_score, reasons = _candidate_quality(candidate)
    signal_score = _signal_score(candidate)

    min_quality = _to_float(os.getenv("MAIN_CLEAN_MIN_QUALITY"), 70.0)
    min_signal_score = _to_float(os.getenv("MAIN_CLEAN_MIN_SIGNAL_SCORE"), 65.0)
    min_conf = _to_float(os.getenv("MAIN_CLEAN_MIN_CONFIDENCE"), 64.0)
    min_edge = _to_float(os.getenv("MAIN_CLEAN_MIN_EDGE_PCT"), 3.5)
    max_odds = _to_float(os.getenv("MAIN_CLEAN_MAX_ODDS"), 2.35)
    req_books = int(_to_float(os.getenv("MAIN_CLEAN_REQUIRE_BOOKS"), 2))
    max_implied_mismatch = _to_float(os.getenv("MAIN_CLEAN_MAX_IMPLIED_MISMATCH"), 0.02)
    max_adjusted_mismatch = _to_float(os.getenv("MAIN_CLEAN_MAX_ADJUSTED_MISMATCH"), 0.02)
    max_final_mismatch = _to_float(os.getenv("MAIN_CLEAN_MAX_FINAL_MISMATCH"), 0.02)

    if quality_score < min_quality:
        issues.append(f"quality_below_min:{quality_score:.2f}")
    if signal_score < min_signal_score:
        issues.append(f"signal_score_below_min:{signal_score:.2f}")
    if confidence < min_conf:
        issues.append(f"confidence_below_min:{confidence:.2f}")
    if edge_pct < min_edge:
        issues.append(f"edge_below_min:{edge_pct:.2f}")
    if odds <= 1.01 or odds > max_odds:
        issues.append(f"odds_out_of_range:{odds:.2f}")
    if books_count < req_books:
        issues.append(f"books_below_min:{books_count}")

    if _to_bool_env("MAIN_CLEAN_REJECT_FALLBACK", True):
        if status and status != "passed_quality":
            issues.append(f"fallback_status:{status}")
        for reason in reasons:
            low = str(reason).lower()
            if "historical" in low or "emergency" in low or "last_resort" in low or "fallback" in low:
                issues.append(f"fallback_reason:{reason}")

    if _to_bool_env("MAIN_CLEAN_REJECT_SINGLE_SOURCE", True):
        if int(candidate.get("sources_count") or 0) <= 1:
            issues.append("single_source_rejected")

    if _to_bool_env("MAIN_CLEAN_REJECT_HIGH_RISK_LABELS", True):
        for flag in _risk_flags(candidate):
            low = flag.lower()
            if any(token in low for token in ("heavy-shrink", "non-core", "fallback", "historical", "emergency", "last_resort")):
                issues.append(f"risk_flag:{flag}")

    implied_from_odds = _implied_from_odds(odds)
    if implied_from_odds is None:
        issues.append("invalid_odds")
    else:
        if abs(implied_from_odds - implied_probability) > max_implied_mismatch:
            issues.append(
                f"implied_mismatch:{abs(implied_from_odds - implied_probability):.4f}"
            )

    if abs(adjusted_probability - ss_adjusted_probability) > max_adjusted_mismatch:
        issues.append(
            f"adjusted_mismatch:{abs(adjusted_probability - ss_adjusted_probability):.4f}"
        )
    if abs(adjusted_probability - final_probability) > max_final_mismatch:
        issues.append(
            f"final_probability_mismatch:{abs(adjusted_probability - final_probability):.4f}"
        )

    ev_pct = _to_float(candidate.get("ev_pct"), 0.0)
    if edge_pct < 0 and ev_pct > 0:
        issues.append("edge_ev_conflict")

    return issues


def _build_message(candidate: dict[str, Any]) -> str:
    home = str(candidate.get("home_team") or "").strip()
    away = str(candidate.get("away_team") or "").strip()
    league = str(candidate.get("league_name") or "").strip()
    family = str(candidate.get("family") or "").strip()
    selection = str(candidate.get("selection") or "").strip()
    point = candidate.get("point")
    odds = _to_float(candidate.get("odds"), 0.0)
    adjusted_probability = _to_float(candidate.get("adjusted_probability"), 0.0) * 100.0
    market_probability = _to_float(candidate.get("market_probability"), 0.0) * 100.0
    confidence = _to_float(candidate.get("confidence"), 0.0)
    quality_status, quality_score, _ = _candidate_quality(candidate)

    line_text = ""
    if point not in ("", None):
        line_text = f" ({point})"

    return (
        "🔥 Чистый сигнал single-run\n\n"
        f"{home} — {away}\n"
        f"🏆 {league}\n"
        f"🎯 {family}: {selection}{line_text}\n"
        f"💸 Коэффициент: {odds:.2f}\n"
        f"📊 Модель: {adjusted_probability:.1f}% | Рынок: {market_probability:.1f}%\n"
        f"✅ Уверенность: {confidence:.1f}% | Quality: {quality_score:.1f} ({quality_status or 'n/a'})\n"
        "\nСообщение прошло через strict main-clean gate."
    )


def _send_telegram(message: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"ok": False, "error": "missing telegram credentials"}
    if httpx is None:
        return {"ok": False, "error": "httpx not installed"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(url, json=payload)
        return {"ok": response.is_success, "status_code": response.status_code, "body": response.text[:500]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    candidates = _extract_candidates()
    report: dict[str, Any] = {
        "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "candidates_seen": len(candidates),
        "selected_candidate": None,
        "issues": [],
        "published": False,
        "telegram_result": None,
    }

    if not candidates:
        report["issues"] = ["no_candidates_in_latest_picks"]
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    candidate = candidates[0]
    issues = _candidate_issues(candidate)
    report["selected_candidate"] = {
        "match_key": candidate.get("match_key"),
        "home_team": candidate.get("home_team"),
        "away_team": candidate.get("away_team"),
        "family": candidate.get("family"),
        "selection": candidate.get("selection"),
        "point": candidate.get("point"),
        "odds": candidate.get("odds"),
    }
    report["issues"] = issues

    if issues:
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    message = _build_message(candidate)
    telegram_result = _send_telegram(message)
    report["telegram_result"] = telegram_result
    report["published"] = bool(telegram_result.get("ok"))
    report["message"] = message
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
