from __future__ import annotations

"""Inject the HARIZON ideal-runtime audit scorecard into the clean detailed report.

This is a post-render patch: it does not affect publication logic and does not
relax any guard. It only makes the already-produced ideal audit visible in the
Telegram detailed report.
"""

import json
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
AUDIT_JSON = EXPORT / "latest-harizon-ideal-runtime-audit.json"
REPORT_TXT = EXPORT / "latest-detailed-run-report-cleaned.txt"
STATUS_JSON = EXPORT / "latest-ideal-audit-scorecard-patch.json"


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def pct(value: Any) -> str:
    return f"{as_float(value):.0f}%"


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def nested(payload: dict[str, Any], *keys: str) -> Any:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def build_scorecard(audit: dict[str, Any]) -> str:
    score = as_int(audit.get("ideal_score") or audit.get("score"))
    inv = first_dict(audit.get("inventory"), audit.get("day_inventory"), audit.get("coverage"))
    totals = first_dict(inv.get("totals"), inv.get("counts"), inv)
    blockers = audit.get("blockers") if isinstance(audit.get("blockers"), list) else []
    actions = audit.get("recommended_actions") if isinstance(audit.get("recommended_actions"), list) else []
    account2 = first_dict(audit.get("odds_api_io_account2"), nested(audit, "odds_api_io", "account2"))
    bzz = first_dict(audit.get("bzzoiro_chain"), audit.get("bzzoiro"))

    total = as_int(totals.get("matches_total") or totals.get("total") or totals.get("matches"))
    with_lines = as_int(totals.get("matches_with_odds") or totals.get("with_lines") or totals.get("with_odds"))
    with_context = as_int(totals.get("matches_with_context") or totals.get("with_context"))
    ready = as_int(totals.get("matches_ready_for_model") or totals.get("ready") or totals.get("ready_for_model"))
    next6 = as_int(totals.get("matches_next_6h") or totals.get("next_6h_total"))
    next6_ready = as_int(totals.get("matches_next_6h_ready") or totals.get("next_6h_ready"))

    lines_pct = audit.get("with_lines_pct") or totals.get("with_lines_pct") or (with_lines / total * 100.0 if total else 0)
    ctx_pct = audit.get("with_context_pct") or totals.get("with_context_pct") or (with_context / total * 100.0 if total else 0)
    ready_pct = audit.get("ready_pct") or totals.get("ready_pct") or (ready / total * 100.0 if total else 0)
    next6_pct = audit.get("next_6h_ready_pct") or totals.get("next_6h_ready_pct") or (next6_ready / next6 * 100.0 if next6 else 0)

    lines = ["🧭 Ideal runtime audit"]
    if score:
        lines.append(f"• Score: {score}/100")
    if total:
        lines.append(f"• Lines: {with_lines}/{total} ({pct(lines_pct)}) | Context: {with_context}/{total} ({pct(ctx_pct)})")
        lines.append(f"• Ready: {ready}/{total} ({pct(ready_pct)}) | Near 6h: {next6_ready}/{next6} ({pct(next6_pct)})")
    if account2:
        lines.append(
            "• account2: "
            f"offers {as_int(account2.get('offers') or account2.get('offers_parsed'))}, "
            f"req {as_int(account2.get('requests') or account2.get('odds_requests'))}, "
            f"plan_restriction {bool(account2.get('plan_restriction'))}"
        )
    if bzz:
        lines.append(
            "• Bzzoiro bridge: "
            f"offers {as_int(bzz.get('overlap_bridge_offers') or bzz.get('offers'))}, "
            f"overlap {as_int(bzz.get('overlap_matches') or bzz.get('unique_overlap_match_count'))}, "
            f"2-source {as_int(bzz.get('merge_after_2plus_sources') or bzz.get('after_2plus_sources'))}"
        )
    if blockers:
        lines.append("• Main blockers: " + ", ".join(str(item) for item in blockers[:5]))
    if actions:
        lines.append("• Next action: " + str(actions[0]))
    return "\n".join(lines)


def insert_scorecard(text: str, scorecard: str) -> str:
    if "🧭 Ideal runtime audit" in text:
        return text
    marker = "⚙️ Что сделал скрипт"
    idx = text.find(marker)
    if idx >= 0:
        return text[:idx].rstrip() + "\n\n" + scorecard + "\n\n" + text[idx:].lstrip()
    return text.rstrip() + "\n\n" + scorecard + "\n"


def main() -> int:
    audit = load_json(AUDIT_JSON, {})
    if not isinstance(audit, dict) or not audit:
        payload = {"status": "skipped", "reason": "audit_missing", "audit_path": str(AUDIT_JSON)}
        write_json(STATUS_JSON, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    try:
        text = REPORT_TXT.read_text(encoding="utf-8")
    except Exception:
        payload = {"status": "skipped", "reason": "report_missing", "report_path": str(REPORT_TXT)}
        write_json(STATUS_JSON, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    scorecard = build_scorecard(audit)
    patched = insert_scorecard(text, scorecard)
    REPORT_TXT.write_text(patched, encoding="utf-8")
    payload = {"status": "ok", "scorecard_added": patched != text, "audit_path": str(AUDIT_JSON), "report_path": str(REPORT_TXT)}
    write_json(STATUS_JSON, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
