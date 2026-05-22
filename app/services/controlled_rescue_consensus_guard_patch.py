from __future__ import annotations

"""Guard controlled-consensus rescue candidates against negative market value.

The controlled rescue builder is intentionally market-first: it can create
fallback candidates from paired bookmaker consensus when the normal model layer is
empty. After the hybrid Tier-B change, these rows must not become a source of
negative-EV Telegram candidates. A rescue candidate is only useful if the chosen
price is at least non-negative against the paired consensus probability before
any quality/fallback tier checks.

This patch does not loosen publication. It removes negative consensus-value
rescue rows earlier, so reports stop showing "reserve candidates" that are
mathematically dead on arrival.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-controlled-rescue-consensus-guard.json"
_INSTALLED = False


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(str(raw).replace(",", ".")) if raw not in (None, "") else default
    except Exception:
        return default


def _price(value: Any) -> float:
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else 0.0
    except Exception:
        return 0.0


def _inc(rejections: dict[str, int] | None, key: str) -> None:
    if not isinstance(rejections, dict):
        return
    try:
        rejections[key] = int(rejections.get(key) or 0) + 1
    except Exception:
        pass


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

    report: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "enabled": _truthy("CONTROLLED_RESCUE_REQUIRE_CONSENSUS_VALUE", True),
        "min_consensus_edge_pp": _float("CONTROLLED_RESCUE_MIN_CONSENSUS_EDGE_PP", 0.0),
    }
    try:
        from app.services import controlled_candidate_rescue as rescue
    except Exception as exc:
        report.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write(report)
        return report

    original = getattr(rescue, "_make_candidate", None)
    if not callable(original):
        report.update({"status": "skipped", "reason": "_make_candidate_missing"})
        _write(report)
        return report
    if getattr(original, "_harizon_consensus_value_guard", False):
        report.update({"status": "already_wrapped"})
        _write(report)
        return report

    def guarded_make_candidate(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        if not _truthy("CONTROLLED_RESCUE_REQUIRE_CONSENSUS_VALUE", True):
            return original(*args, **kwargs)

        bucket = kwargs.get("bucket")
        consensus_prob = kwargs.get("consensus_prob")
        rejections = kwargs.get("rejections")
        try:
            bucket_rows = list(bucket or [])
            best = max(bucket_rows, key=lambda item: _price(getattr(item, "price", None)))
            odds = _price(getattr(best, "price", None))
            consensus = float(consensus_prob)
            implied = 1.0 / odds if odds > 1.0 else 0.0
            edge_pp = (consensus - implied) * 100.0
        except Exception:
            return original(*args, **kwargs)

        min_edge = _float("CONTROLLED_RESCUE_MIN_CONSENSUS_EDGE_PP", 0.0)
        if edge_pp < min_edge:
            _inc(rejections, "controlled_rescue_consensus_value_guard")
            _write({
                "created_at_utc": datetime.now(UTC).isoformat(),
                "status": "installed",
                "last_block": {
                    "selection": kwargs.get("selection"),
                    "family": kwargs.get("family"),
                    "point": kwargs.get("point"),
                    "odds": round(odds, 4),
                    "consensus_probability": round(consensus, 6),
                    "selected_implied_probability": round(implied, 6),
                    "consensus_edge_pp": round(edge_pp, 3),
                    "min_consensus_edge_pp": min_edge,
                },
            })
            return None

        return original(*args, **kwargs)

    guarded_make_candidate._harizon_consensus_value_guard = True  # type: ignore[attr-defined]
    rescue._make_candidate = guarded_make_candidate  # type: ignore[assignment]
    report.update({
        "status": "installed",
        "wrapped": "app.services.controlled_candidate_rescue._make_candidate",
        "reason": "negative consensus-value rescue rows are removed before quality/fallback",
    })
    _write(report)
    return report
