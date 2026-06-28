from __future__ import annotations

"""Runtime performance-aware policy for controlled fallback publication.

The manual all-time report showed that recovered controlled-fallback picks can
produce volume while adding little or negative P&L.  This script reads the latest
performance summary and, when the controlled_fallback segment is mature enough
and negative, exports stricter fallback thresholds through GITHUB_ENV before the
publication step runs.
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


def set_env(values: dict[str, Any]) -> None:
    github_env = os.getenv("GITHUB_ENV")
    if not github_env:
        return
    with open(github_env, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def main() -> int:
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

    env_updates: dict[str, Any] = {}
    if applies:
        # Keep fallback available, but only when the value is materially stronger
        # than the weak historical segment.  These are minimums already consumed
        # by publish_controlled_fallback.py.
        env_updates = {
            "CONTROLLED_FALLBACK_PERFORMANCE_COOLDOWN_ACTIVE": "true",
            "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_FINAL_MIN_EDGE_PP", "3.2"),
            "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_FINAL_MIN_EV_PCT", "7.0"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_EDGE_PP", "3.5"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_EV_PCT", "7.5"),
            "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": os.getenv("CONTROLLED_FALLBACK_PERF_TIER_B_MIN_CONFIRMATION_SOURCES", "2"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_EDGE_PP", "5.0"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_EV_PCT", "10.0"),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": os.getenv("CONTROLLED_FALLBACK_PERF_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE", "72.0"),
            "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
            "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": "true",
            "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": os.getenv("CONTROLLED_FALLBACK_PERF_MIN_CONFIRMATION_SOURCES", "2"),
        }
        set_env(env_updates)

    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": enabled,
        "applies": applies,
        "reason": "controlled_fallback_segment_negative" if applies else "not_triggered",
        "segment": {
            "total": total,
            "closed": closed,
            "pnl": round(pnl, 2),
            "roi_pct": round(roi, 3),
            "min_closed": min_closed,
            "min_total": min_total,
            "negative": negative,
        },
        "env_updates": env_updates,
    }
    write_json(EXPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
