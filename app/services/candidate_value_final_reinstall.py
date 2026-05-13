from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-candidate-value-final-reinstall.json"


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "purpose": "final runtime wrappers before PredictionRunner",
    }
    try:
        from app.services.model import CandidateFactory

        # Must be installed immediately before the final value wrapper. It expands
        # CandidateFactory's bookmaker universe for multi-source SStats/Bzzoiro/
        # SportLogic line buckets, otherwise coverage sees 2-source lines but model
        # generation silently drops external books.
        try:
            import app.services.core_line_bookmaker_universe_patch as line_universe_patch
            payload["core_line_bookmaker_universe_install"] = line_universe_patch.install()
        except Exception as exc:
            payload["core_line_bookmaker_universe_install"] = f"failed:{type(exc).__name__}: {exc}"

        import app.services.candidate_value_runtime_patch as value_patch

        current_build = getattr(CandidateFactory, "build_candidates", None)
        payload["build_before"] = getattr(current_build, "__name__", str(current_build))
        payload["had_value_patch_flag_before"] = bool(getattr(CandidateFactory, "_harizon_candidate_value_patch", False))

        try:
            setattr(value_patch, "_INSTALLED", False)
        except Exception:
            pass
        try:
            setattr(CandidateFactory, "_harizon_candidate_value_patch", False)
        except Exception:
            pass

        result = value_patch.install()
        payload["install_result"] = result
        current_after = getattr(CandidateFactory, "build_candidates", None)
        payload["build_after"] = getattr(current_after, "__name__", str(current_after))
        payload["had_value_patch_flag_after"] = bool(getattr(CandidateFactory, "_harizon_candidate_value_patch", False))
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write(payload)
    return payload
