from __future__ import annotations

"""Keep near-zero candidates alive until final consensus validation.

The value runtime layer runs before the final API consensus guard. On live runs it
can drop candidates using a pre-consensus selected price even though the final
guard later rebases the price to exact-line consensus and validates EV/edge.

This module does not allow publishing negative value. It only widens the
pre-quality holding pen. Final API coverage, quality, line sanity and Telegram
safety still require non-negative consensus EV/edge.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-prequality-final-consensus-bridge.json"
_INSTALLED = False


def _set(key: str, value: Any) -> None:
    os.environ[str(key)] = str(value)


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True

    # CandidateValue reads these at build time, so env defaults are enough.
    # The final consensus guard below still enforces >=0 EV/edge before publish.
    env = {
        "PREQUALITY_CANONICAL_VALUE_FILTER_ENABLED": "true",
        "PREQUALITY_CANONICAL_MIN_EV_PCT": os.getenv("PREQUALITY_CANONICAL_MIN_EV_PCT_RELIEF", "-10.0"),
        "PREQUALITY_CANONICAL_MIN_EDGE_PP": os.getenv("PREQUALITY_CANONICAL_MIN_EDGE_PP_RELIEF", "-6.0"),
        "API_COVERAGE_MIN_CANONICAL_EV_PCT": "0.0",
        "API_COVERAGE_MIN_CANONICAL_EDGE_PP": "0.0",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EV_PCT": "0.0",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EDGE_PP": "0.0",
    }
    for key, value in env.items():
        _set(key, value)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "installed",
        "env": env,
        "safety": "prequality only; final consensus guard remains non-negative",
    }
    _write(report)
    return report
