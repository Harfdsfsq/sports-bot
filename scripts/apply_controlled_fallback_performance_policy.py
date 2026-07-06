from __future__ import annotations

"""Runtime performance-aware policy for controlled fallback publication.

The policy keeps the public guards intact, but separates two cases:

* baseline production scoring for clean B-tier reserve candidates; and
* stricter cooldown thresholds when the controlled_fallback historical segment is
  mature and negative.

The latest artifacts showed clean B-tier reserve candidates with 2 books,
2+ confirmations, positive EV/edge and xG support being blocked by the generic
publication-score/final-value floors.  The baseline B-tier floor is therefore
kept explicit and modest, while a mature negative fallback segment can still
raise all thresholds through the cooldown branch.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
SUMMARY = ROOT / ".data" / "bets" / "performance-summary.json"
EXPORT = ROOT / ".data" / "exports" / "latest-controlled-fallback-performance-policy.json"
UTC = timezone.utc


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def apply_env(values: dict[str, Any]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if not github_env:
        return
    with open(github_env, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def run_rescue_candidate_sanitizer() -> dict[str, Any]:
    try:
        from scripts import sanitize_rescue_candidate_payloads

        code = int(sanitize_rescue_candidate_payloads.main() or 0)
        payload = load_json(ROOT / ".data" / "exports" / "latest-rescue-candidate-sanitizer.json", {})
        if isinstance(payload, dict):
            payload.setdefault("exit_code", code)
            return payload
        return {"status": "ok" if code == 0 else "non_zero", "exit_code": code}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def baseline_env_updates() -> dict[str, Any]:
    return {
        "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": os.getenv("CONTROLLED_FALLBACK_BASE_TIER_B_MIN_PUBLICATION_SCORE", "16.0"),
        "CONTROLLED_FALLBACK_TIER_C_MIN_PUBLICATION_SCORE": os.getenv("CONTROLLED_FALLBACK_BASE_TIER_C_MIN_PUBLICATION_SCORE", "18.0"),
        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_BASE_FINAL_MIN_EDGE_PP", "1.8"),
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_BASE_FINAL_MIN_EV_PCT", "4.0"),
        "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": os.getenv("CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES", "false"),
    }


def main() -> int:
    sanitizer = run_rescue_candidate_sanitizer()
    enabled = env_bool("CONTROLLED_FALLBACK_PERFORMANCE_POLICY_ENABLED", True)
    payload = load_json(SUMMARY, {})
    by_quality = payload.get("by_quality") if isinstance(payload, dict) else {}
    segment = by_quality.get("controlled_fallback") if isinstance(by_quality, dict) else {}
    closed = as_int(segment.get("closed")) if isinstance(segment, dict) else 0
    total = as_int(segment.get("total")) if isinstance(segment, dict) else 0
    pnl = as_float(segment.get("pnl")) if isinstance(segment, dict) else 0.0
    roi = as_float(segment.get("roi_pct")) if isinstance(segment, dict) else 0.0
    min_closed = as_int(os.getenv("CONTROLLED_FALLBACK_PERFORMANCE_POLICY_MIN_CLOSED"), 12)
    min_total = as_int(os.getenv("CONTROLLED_FALLBACK_PERFORMANCE_POLICY_MIN_TOTAL"), 18)
    negative = pnl < as_float(os.getenv("CONTROLLED_FALLBACK_PERFORMANCE_POLICY_MAX_PNL", 0.0)) or roi < as_float(os.getenv("CONTROLLED_FALLBACK_PERFORMANCE_POLICY_MAX_ROI_PCT", 0.0))
    applies = bool(enabled and closed >= min_closed and total >= min_total and negative)

    env_updates: dict[str, Any] = baseline_env_updates()
    if applies:
        env_updates.update({
            "CONTROLLED_FALLBACK_PERFORMANCE_COOLDOWN_ACTIVE": "true",
            "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_FINAL_MIN_EDGE_PP", "3.2"),
            "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_FINAL_MIN_EV_PCT", "7.0"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_EDGE_PP", "3.5"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_EV_PCT", "7.5"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_PUBLICATION_SCORE", "22.0"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_CONFIRMATION_SOURCES", "2"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_EDGE_PP", "5.0"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_EV_PCT", "10.0"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE", "72.0"),
            "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
            "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": "true",
            "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": os.getenv("CONTROLLED_FALLBACK_PERF_MIN_CONFIRMATION_SOURCES", "2"),
        })
    apply_env(env_updates)

    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": enabled,
        "applies": applies,
        "reason": "controlled_fallback_segment_negative" if applies else "baseline_b_tier_score_floor_applied",
        "rescue_candidate_sanitizer": sanitizer,
        "segment": {"total": total, "closed": closed, "pnl": round(pnl, 2), "roi_pct": round(roi, 3), "min_closed": min_closed, "min_total": min_total, "negative": negative},
        "env_updates": env_updates,
    }
    write_json(EXPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
