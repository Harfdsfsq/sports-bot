from __future__ import annotations

"""Absolute-final installer for windowed core coverage.

`sitecustomize` can load before dependencies are installed, and `usercustomize`
loads many runtime wrappers after it. This module deliberately forces the
windowed-core policy to be the final PredictionRunner / CandidateFactory wrapper.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-windowed-core-finalizer.json"


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    os.environ.setdefault("WINDOWED_CORE_COVERAGE_ENABLED", "true")
    os.environ.setdefault("CORE_COVERAGE_WINDOW_HOURS", "4")
    os.environ.setdefault("CORE_COVERAGE_CRON_INTERVAL_HOURS", "2")
    os.environ.setdefault("CORE_COVERAGE_MIN_ODDS_SOURCES", "2")
    os.environ.setdefault("CORE_COVERAGE_MIN_CONTEXT_SOURCES", "2")
    os.environ.setdefault("CORE_COVERAGE_MIN_CORE_PROVIDERS", "2")
    os.environ.setdefault("CONTEXT_ENRICHMENT_REQUIRES_OFFERS", "false")
    os.environ.setdefault("ENABLE_ODDS_API_IO", "true")
    os.environ.setdefault("SSTATS_ENABLED", "true")
    os.environ.setdefault("ENABLE_SSTATS_CONTEXT", "true")
    os.environ.setdefault("ENABLE_BZZOIRO_CONTEXT", "true")
    os.environ.setdefault("BZZOIRO_V2_ENRICHMENT_ENABLED", "true")
    os.environ.setdefault("SSTATS_DEEP_ENDPOINTS_ENABLED", "true")

    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "forced": True,
        "steps": [],
    }
    try:
        from app.services import windowed_core_coverage_runtime_patch as patch
        # Reset module/class markers so the windowed wrappers are applied after
        # all other usercustomize wrappers, not before them.
        patch._INSTALLED = False
        try:
            from app.services.runner import PredictionRunner
            for attr in ("_harizon_windowed_targets_patch",):
                if hasattr(PredictionRunner, attr):
                    delattr(PredictionRunner, attr)
        except Exception:
            pass
        try:
            from app.services.model import CandidateFactory
            for attr in ("_harizon_windowed_core_guard_patch",):
                if hasattr(CandidateFactory, attr):
                    delattr(CandidateFactory, attr)
        except Exception:
            pass
        try:
            from app.providers.bzzoiro import BzzoiroContextProvider
            for attr in ("_harizon_windowed_v2_patch",):
                if hasattr(BzzoiroContextProvider, attr):
                    delattr(BzzoiroContextProvider, attr)
        except Exception:
            pass
        try:
            from app.providers.sstats import SStatsContextProvider
            for attr in ("_harizon_deep_endpoints_patch",):
                if hasattr(SStatsContextProvider, attr):
                    delattr(SStatsContextProvider, attr)
        except Exception:
            pass
        try:
            from app.providers.odds_api_io import OddsApiIoProvider
            for attr in ("_harizon_windowed_priority_patch",):
                if hasattr(OddsApiIoProvider, attr):
                    delattr(OddsApiIoProvider, attr)
        except Exception:
            pass
        result = patch.install()
        payload["status"] = "installed"
        payload["patch_result"] = result
        payload["steps"].append("windowed_core_coverage_runtime_patch_reinstalled_last")
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write(payload)
    return payload
