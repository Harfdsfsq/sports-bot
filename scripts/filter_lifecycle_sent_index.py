from __future__ import annotations

"""Post-process candidate lifecycle gate with the sent-index.

The lifecycle gate can confirm a value pick 2/2, but publication still must not
open the publisher for a pick that has already been sent. This filter rewrites
the lifecycle report and GITHUB_ENV decision so duplicate picks are blocked
before `publish_controlled_fallback.py` runs.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT_PATH = Path(".data/exports/latest-candidate-lifecycle-report.json")
REPORT_MD_PATH = Path(".data/exports/latest-candidate-lifecycle-report.md")
SENT_INDEX_PATH = Path(".data/fallback-sent-index.json")
OUT_PATH = Path(".data/exports/latest-candidate-lifecycle-sent-filter.json")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text


def point_norm(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        parsed = float(str(value))
        if parsed.is_integer():
            return str(int(parsed))
        return f"{parsed:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return norm(value)


def row_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    metrics = row.get("last_metrics") if isinstance(row.get("last_metrics"), dict) else {}
    return (
        norm(row.get("match_key")),
        norm(row.get("family") or metrics.get("family")),
        norm(row.get("selection") or metrics.get("selection")),
        point_norm(row.get("point") if row.get("point") not in (None, "") else metrics.get("point")),
    )


def sent_signatures(sent_index: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    out: set[tuple[str, str, str, str]] = set()
    for item in sent_index.values():
        if not isinstance(item, dict):
            continue
        sig = (
            norm(item.get("match_key")),
            norm(item.get("family")),
            norm(item.get("selection")),
            point_norm(item.get("point")),
        )
        if sig[0] and sig[1] and sig[2]:
            out.add(sig)
    return out


def same_match_sent_keys(sent_index: dict[str, Any]) -> set[str]:
    return {norm(item.get("match_key")) for item in sent_index.values() if isinstance(item, dict) and item.get("match_key")}


def is_duplicate(row: dict[str, Any], exact_sigs: set[tuple[str, str, str, str]], match_sigs: set[str]) -> tuple[bool, str]:
    sig = row_signature(row)
    if sig in exact_sigs:
        return True, "duplicate_fallback_sent_index"
    # Bot policy is one recommendation per match. A different market on an already-published match should also not reopen publication.
    if sig[0] and sig[0] in match_sigs:
        return True, "duplicate_same_match:fallback_sent_index"
    return False, ""


def rewrite_markdown(report: dict[str, Any], filter_summary: dict[str, Any]) -> None:
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
    lines = [
        "# Candidate lifecycle gate",
        "",
        f"- Created UTC: `{report.get('created_at_utc')}`",
        f"- Sent-index filter: **{filter_summary.get('status')}**",
        f"- Duplicates blocked: **{filter_summary.get('duplicates_blocked')}**",
        f"- Allow publish: **{decision.get('allow_publish')}**",
        f"- Eligible after sent-filter: **{decision.get('eligible_count')}**",
        f"- Blocked: **{decision.get('blocked_count')}**",
        "",
    ]
    if selected:
        lines.extend([
            "## Selected for final publication window", "",
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
    REPORT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report = load_json(REPORT_PATH, {})
    sent_index = load_json(SENT_INDEX_PATH, {})
    if not isinstance(report, dict) or not report:
        append_env({"CANDIDATE_LIFECYCLE_ALLOW_PUBLISH": "false", "CANDIDATE_LIFECYCLE_REASON": "lifecycle_report_missing"})
        return 0
    if not isinstance(sent_index, dict):
        sent_index = {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    eligible = [row for row in (decision.get("eligible") or []) if isinstance(row, dict)]
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else None
    if selected and not any(row.get("key") == selected.get("key") for row in eligible):
        eligible.insert(0, selected)
    exact = sent_signatures(sent_index)
    match_keys = same_match_sent_keys(sent_index)
    kept: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in eligible:
        duplicate, reason = is_duplicate(row, exact, match_keys)
        if duplicate:
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
    report["sent_index_filter"] = {
        "applied_at_utc": datetime.now(UTC).isoformat(),
        "sent_index_path": str(SENT_INDEX_PATH),
        "sent_index_size": len(sent_index),
        "duplicates_blocked": len(duplicates),
        "eligible_before": len(eligible),
        "eligible_after": len(kept),
        "selected_match_key": new_selected.get("match_key") if new_selected else "",
    }
    write_json(REPORT_PATH, report)
    summary = {
        "status": "allow_publish" if new_selected else "blocked_or_no_eligible",
        "duplicates_blocked": len(duplicates),
        "eligible_before": len(eligible),
        "eligible_after": len(kept),
        "selected_match_key": new_selected.get("match_key") if new_selected else "",
        "selected_key": new_selected.get("key") if new_selected else "",
    }
    write_json(OUT_PATH, summary)
    rewrite_markdown(report, summary)
    append_env({
        "CANDIDATE_LIFECYCLE_ALLOW_PUBLISH": "true" if new_selected else "false",
        "CANDIDATE_LIFECYCLE_REASON": "selected_final_recheck_passed" if new_selected else "no_non_duplicate_candidate_passed_lifecycle_recheck",
        "CANDIDATE_LIFECYCLE_SELECTED_KEY": str(new_selected.get("key") if new_selected else ""),
        "CANDIDATE_LIFECYCLE_SELECTED_MATCH_KEY": str(new_selected.get("match_key") if new_selected else ""),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
