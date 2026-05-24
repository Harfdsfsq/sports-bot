from __future__ import annotations

"""Run fixture expansion after provider policy is applied.

This hooks into the existing pre-run `apply_provider_request_budget.py` step when
the runtime startup chain is installed. The expansion is bounded and best-effort;
it can only add fixture rows to day inventory and never publishes predictions or
relaxes guards.
"""

import atexit
import os
import subprocess
import sys
from pathlib import Path

PATCH_MARKER = "_harizon_fixture_expansion_runtime_guard_v1"


def _is_provider_budget_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else "").replace("\\", "/")
    return argv0.endswith("scripts/apply_provider_request_budget.py") or argv0.endswith("apply_provider_request_budget.py")


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _run() -> None:
    if not _truthy(os.getenv("FIXTURE_EXPANSION_ENABLED"), True):
        return
    script = Path("scripts/expand_day_inventory_fixtures.py")
    if not script.exists():
        return
    try:
        subprocess.run([sys.executable, str(script)], check=False, timeout=int(float(os.getenv("FIXTURE_EXPANSION_GUARD_TIMEOUT_SECONDS", "30") or 30)))
    except Exception as exc:
        print(f"[fixture-expansion-guard] skipped after error: {type(exc).__name__}: {exc}", flush=True)


def install() -> bool:
    if getattr(sys, PATCH_MARKER, False):
        return False
    if not _is_provider_budget_process():
        return False
    setattr(sys, PATCH_MARKER, True)
    atexit.register(_run)
    print("[fixture-expansion-guard] enabled after provider budget policy", flush=True)
    return True
