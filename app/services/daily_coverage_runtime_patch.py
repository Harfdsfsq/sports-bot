from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-daily-coverage-runtime-patch.json"
_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services import evidence
        from app.services import runner as runner_module
        from app.services.coverage_planner import CoveragePlanner
        from app.services.daily_coverage_runtime_boundary import (
            install as install_boundary,
        )
        from app.services.daily_coverage_runtime_providers import (
            install as install_providers,
        )
        from app.services.runner import PredictionRunner
        from app.services.strict_coverage_metrics import (
            install as install_strict_metrics,
        )

        strict_result = install_strict_metrics()
        provider_result = install_providers(PredictionRunner, CoveragePlanner)
        boundary_result = install_boundary(PredictionRunner, runner_module, evidence)
    except Exception as exc:
        payload = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write(payload)
        return payload
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "strict_metrics": strict_result,
        "providers": provider_result,
        "boundary": boundary_result,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["install"]
