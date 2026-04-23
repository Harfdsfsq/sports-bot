from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(".")
ARTIFACTS_DIR = ROOT / "artifacts"

ALLOWED_FAMILIES = {"totals", "dnb", "doubleChance"}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _eligible_reasons(item: dict) -> list[str]:
    reasons: list[str] = []
    family = str(item.get("family") or "")
    if family not in ALLOWED_FAMILIES:
        reasons.append(f"family_not_allowed:{family}")
    if str(item.get("integrity_status") or "ok") != "ok":
        reasons.append(f"integrity_status:{item.get('integrity_status')}")
    if float(item.get("quality_score") or item.get("source_summary", {}).get("quality_score") or 0.0) < 70.0:
        reasons.append("quality_below_min")
    if float(item.get("confidence") or 0.0) < 64.0:
        reasons.append("confidence_below_min")
    if float(item.get("edge_pct") or 0.0) < 3.5:
        reasons.append("edge_below_min")
    if int(item.get("books_count") or 0) < 2:
        reasons.append("books_below_min")
    if int(item.get("sources_count") or 0) < 2:
        reasons.append("sources_below_min")
    if float(item.get("odds") or 0.0) < 1.75 or float(item.get("odds") or 0.0) > 2.35:
        reasons.append("odds_out_of_range")
    risk_label = str(item.get("risk_label") or "")
    if risk_label in {"forced", "shadow", "high"}:
        reasons.append(f"risk_label:{risk_label}")
    quality_status = str(item.get("source_summary", {}).get("quality_status") or "")
    if quality_status not in {"passed_quality", ""}:
        reasons.append(f"quality_status:{quality_status}")
    return reasons


def _build_message(item: dict) -> str:
    home = str(item.get("home_team") or "")
    away = str(item.get("away_team") or "")
    sel = str(item.get("selection") or "")
    point = item.get("point")
    odds = item.get("odds")
    adj = float(item.get("adjusted_probability") or 0.0) * 100.0
    market = float(item.get("market_probability") or 0.0) * 100.0
    edge = float(item.get("edge_pct") or 0.0)
    lines = [
        "🔥 Main-clean ставка",
        "",
        f"{home} — {away}",
        f"🎯 Ставка: {sel}" + (f" ({point})" if point not in (None, "") else ""),
        f"💸 Коэффициент: {float(odds):.2f}",
        f"📊 Скорректированная оценка: {adj:.1f}% | Рынок: {market:.1f}%",
        f"📈 Edge: {edge:.2f}% | Confidence: {float(item.get('confidence') or 0.0):.1f}",
    ]
    return "\n".join(lines)


def main() -> int:
    report = _load_json(ARTIFACTS_DIR / "latest-candidate-integrity.json", {})
    candidates = [dict(item) for item in report.get("candidates") or [] if isinstance(item, dict)]
    decisions = []
    publishable = []
    for item in candidates:
        reasons = _eligible_reasons(item)
        decisions.append({
            "match_key": item.get("match_key"),
            "selection_key": item.get("selection_key"),
            "reasons": reasons,
            "publishable": not reasons,
        })
        if not reasons:
            publishable.append(item)
    publishable.sort(key=lambda row: (
        float(row.get("quality_score") or row.get("source_summary", {}).get("quality_score") or 0.0),
        float(row.get("confidence") or 0.0),
        float(row.get("edge_pct") or 0.0),
    ), reverse=True)
    picked = publishable[:1]
    sent = False
    message = ""
    token = str(os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if picked and token and chat_id:
        message = _build_message(picked[0])
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": message},
                )
            sent = 200 <= response.status_code < 300
        except Exception:
            sent = False
    payload = {
        "published": bool(sent and picked),
        "picked_count": len(picked),
        "picked": picked,
        "decisions": decisions,
        "telegram_sent": sent,
        "message": message,
    }
    _save_json(ARTIFACTS_DIR / "canonical-publish-report.json", payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
