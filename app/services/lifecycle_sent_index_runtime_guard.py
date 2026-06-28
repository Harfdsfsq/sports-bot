from __future__ import annotations

"""Atexit guard for candidate_lifecycle_gate.py.

It runs after the lifecycle report is written but before the next GitHub Actions
step reads GITHUB_ENV. If lifecycle selected a pick that is already present in
fallback-sent-index, the guard rewrites the decision and appends a later env
assignment that blocks controlled publication.
"""

import atexit
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_lifecycle_sent_index_guard_v1"
REPORT_PATH = Path(".data/exports/latest-candidate-lifecycle-report.json")
REPORT_MD_PATH = Path(".data/exports/latest-candidate-lifecycle-report.md")
SENT_INDEX_PATH = Path(".data/fallback-sent-index.json")
OUT_PATH = Path(".data/exports/latest-candidate-lifecycle-sent-filter.json")


def _is_lifecycle_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else "").replace("\\", "/")
    return argv0.endswith("scripts/candidate_lifecycle_gate.py") or argv0.endswith("candidate_lifecycle_gate.py")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _append_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        try:
            with open(github_env, "a", encoding="utf-8") as fh:
                for key, value in values.items():
                    fh.write(f"{key}={value}\n")
        except Exception:
            pass


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def _point_norm(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        parsed = float(str(value))
        if parsed.is_integer():
            return str(int(parsed))
        return f"{parsed:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return _norm(value)


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("last_metrics")
    return value if isinstance(value, dict) else {}


def _row_sig(row: dict[str, Any]) -> tuple[str, str, str, str]:
    m = _metrics(row)
    return (
        _norm(row.get("match_key")),
        _norm(row.get("family") or m.get("family")),
        _norm(row.get("selection") or m.get("selection")),
        _point_norm(row.get("point") if row.get("point") not in (None, "") else m.get("point")),
    )


def _sent_signatures(sent_index: dict[str, Any]) -> tuple[set[tuple[str, str, str, str]], set[str]]:
    exact: set[tuple[str, str, str, str]] = set()
    matches: set[str] = set()
    for item in sent_index.values():
        if not isinstance(item, dict):
            continue
        mk = _norm(item.get("match_key"))
        if mk:
            matches.add(mk)
        sig = (_norm(item.get("match_key")), _norm(item.get("family")), _norm(item.get("selection")), _point_norm(item.get("point")))
        if sig[0] and sig[1] and sig[2]:
            exact.add(sig)
    return exact, matches


def _is_duplicate(row: dict[str, Any], exact: set[tuple[str, str, str, str]], match_keys: set[str]) -> tuple[bool, str]:
    sig = _row_sig(row)
    if sig in exact:
        return True, "duplicate_fallback_sent_index"
    if sig[0] and sig[0] in match_keys:
        return True, "duplicate_same_match:fallback_sent_index"
    return False, ""


def _rewrite_markdown(report: dict[str, Any], summary: dict[str, Any]) -> None:
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
    lines = [
        "# Candidate lifecycle gate",
        "",
        f"- Created UTC: `{report.get('created_at_utc')}`",
        f"- Sent-index filter: **{summary.get('status')}**",
        f"- Duplicates blocked: **{summary.get('duplicates_blocked')}**",
        f"- Allow publish: **{decision.get('allow_publish')}**",
        f"- Eligible after sent-filter: **{decision.get('eligible_count')}**",
        f"- Blocked: **{decision.get('blocked_count')}**",
        "",
    ]
    if selected:
        lines.extend([
            "## Selected for final publication window",
            "",
            f"- Match: `{selected.get('home_team')} — {selected.get('away_team')}`",
            f"- League: `{selected.get('league_name')}`",
            f"- Market: `{selected.get('family')} / {selected.get('selection')} / {selected.get('point')}`",
            f"- Kickoff UTC: `{selected.get('kickoff_utc')}`",
            f"- Seen count: `{selected.get('seen_count')}`",
            f"- Value streak: `{selected.get('value_streak')}`",
            "",
        ])
    else:
        lines.extend(["## No selected pick", "", "No non-duplicate eligible pick remained after sent-index filtering.", ""])
    lines.extend(["## Top blocked", "", "| Match | Market | Kickoff min | Seen | Streak | Reasons |", "|---|---|---:|---:|---:|---|"])
    for row in (decision.get("blocked_top") or [])[:12]:
        if not isinstance(row, dict):
            continue
        ks = row.get("kickoff_state") if isinstance(row.get("kickoff_state"), dict) else {}
        match = f"{row.get('home_team')} — {row.get('away_team')}"
        market = f"{row.get('family')} {row.get('selection')} {row.get('point') or ''}".strip()
        reasons = "; ".join(str(x) for x in (row.get("block_reasons") or []))[:300]
        lines.append(f"| `{match}` | `{market}` | {ks.get('minutes_to_kickoff')} | {row.get('seen_count')} | {row.get('value_streak')} | {reasons} |")
    try:
        REPORT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _run() -> None:
    report = _load_json(REPORT_PATH, {})
    sent_index = _load_json(SENT_INDEX_PATH, {})
    if not isinstance(report, dict) or not report:
        return
    if not isinstance(sent_index, dict):
        sent_index = {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    eligible = [row for row in (decision.get("eligible") or []) if isinstance(row, dict)]
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else None
    if selected and not any(row.get("key") == selected.get("key") for row in eligible):
        eligible.insert(0, selected)
    exact, sent_matches = _sent_signatures(sent_index)
    kept: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in eligible:
        is_dup, reason = _is_duplicate(row, exact, sent_matches)
        if is_dup:
            block = dict(row)
            reasons = list(block.get("block_reasons") or [])
            if reason not in reasons:
                reasons.append(reason)
            block["block_reasons"] = reasons
            duplicates.append(block)
        else:
            kept.append(row)
    blocked = [row for row in (decision.get("blocked_top") or []) if isinstance(row, dict)]
    blocked = duplicates + blocked
    blocked.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
    kept.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
    new_selected = kept[0] if kept else None
    decision.update({
        "allow_publish": bool(new_selected),
        "selected": new_selected,
        "eligible": kept[:10],
        "eligible_count": len(kept),
        "blocked_top": blocked[:20],
        "blocked_count": int(decision.get("blocked_count") or 0) + len(duplicates),
        "sent_index_filter_applied": True,
        "sent_index_duplicates_blocked": len(duplicates),
    })
    report["decision"] = decision
    summary = {
        "status": "allow_publish" if new_selected else "blocked_or_no_eligible",
        "duplicates_blocked": len(duplicates),
        "eligible_before": len(eligible),
        "eligible_after": len(kept),
        "selected_match_key": new_selected.get("match_key") if new_selected else "",
        "selected_key": new_selected.get("key") if new_selected else "",
        "applied_at_utc": datetime.now(UTC).isoformat(),
    }
    report["sent_index_filter"] = summary
    _write_json(REPORT_PATH, report)
    _write_json(OUT_PATH, summary)
    _rewrite_markdown(report, summary)
    _append_env({
        "CANDIDATE_LIFECYCLE_ALLOW_PUBLISH": "true" if new_selected else "false",
        "CANDIDATE_LIFECYCLE_REASON": "selected_final_recheck_passed" if new_selected else "no_non_duplicate_candidate_passed_lifecycle_recheck",
        "CANDIDATE_LIFECYCLE_SELECTED_KEY": str(new_selected.get("key") if new_selected else ""),
        "CANDIDATE_LIFECYCLE_SELECTED_MATCH_KEY": str(new_selected.get("match_key") if new_selected else ""),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def _install_rules_lifecycle_gate() -> bool:
    try:
        from app.services.rules_lifecycle_runtime_gate import install as install_rules_gate
        result = install_rules_gate()
        if isinstance(result, dict):
            return bool(result.get("installed"))
        return bool(result)
    except Exception:
        return False


def install() -> bool:
    rules_gate_installed = _install_rules_lifecycle_gate()
    if getattr(sys, PATCH_MARKER, False):
        return rules_gate_installed
    if not _is_lifecycle_process():
        return rules_gate_installed
    setattr(sys, PATCH_MARKER, True)
    atexit.register(_run)
    return True
