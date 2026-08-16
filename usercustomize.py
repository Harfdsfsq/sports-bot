from __future__ import annotations

"""User-level startup hook for runtime policy extensions.

Policy configuration comes from the workflow environment (see
``.github/workflows/run-bot.yml``) and from
``app/services/rules_compliant_pipeline.py``, which only fills in values that
are not already set.

This module used to force an ``A_TIER_ONLY_ENV`` block on every process and
then monkey-patch ``os.environ.update`` so the block was re-applied after any
other update. B-tier thresholds were pinned to ``999``, so B-tier could never
publish no matter what the workflow configured, and the workflow environment
was effectively unusable. It also patched ``publish_controlled_fallback``
through an ``importlib`` hook to append A-only rejection reasons.

All of that has been removed. Keep this file free of publication thresholds:
if a threshold needs to change, change it in the workflow environment or in
``RULES_ENV_DEFAULTS``.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sitecustomize import *  # noqa: F401,F403

ROOT = Path(__file__).resolve().parent


def _write_policy_report(payload: dict[str, Any]) -> None:
    try:
        out = ROOT / ".data" / "exports" / "latest-usercustomize-policy.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


_write_policy_report({
    "status": "loaded",
    "policy": "workflow_env_is_source_of_truth",
    "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
    "removed": [
        "A_TIER_ONLY_ENV environment override",
        "os.environ.update monkey patch",
        "importlib hook forcing A-only tier_reasons on publish_controlled_fallback",
    ],
    "note": "Publication thresholds live in the workflow env and in rules_compliant_pipeline defaults.",
})


# Runtime installers that must run at interpreter startup, before RuntimePreflight
# imports the autonomous runtime. The run-bot workflow commits only flat
# .data/exports/latest-* files and its fast prune removes subdirectories/JSONL,
# so the persistence redirect has to be installed this early.
for _installer_module in (
    "app.services.inventory_coverage_source_runtime_patch",
    "app.services.bzzoiro_gap_planner_fallback_patch",
    "app.services.autonomous_accumulation_persistence",
):
    try:
        __import__(_installer_module, fromlist=["install"]).install()
    except Exception:
        pass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_helper_process() -> bool:
    name = Path(str(sys.argv[0] or "")).name
    return (
        str(sys.argv[0] or "").strip() == "-"
        or name.startswith("publish_controlled_fallback")
        or os.getenv("HARIZON_SKIP_USERCUSTOMIZE_INSTALLERS") == "1"
    )


def install_legacy_usercustomize() -> dict[str, str]:
    try:
        from app.services import runtime_startup_chain

        result = runtime_startup_chain.install_all()
        return {"app.services.runtime_startup_chain": str(result)}
    except Exception as exc:
        return {"app.services.runtime_startup_chain": f"{type(exc).__name__}: {exc}"}


if _truthy(os.getenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED")) and not _is_helper_process():
    install_legacy_usercustomize()
