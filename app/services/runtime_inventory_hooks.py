from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any

_INSTALLED = False
_RAN_POST_HOOKS = False

RUNTIME_STATE_DIRS: tuple[tuple[str, str], ...] = (
    (".data/cache/day_inventory", ".data/day_inventory"),
    (".data/cache/line_history", ".data/line_history"),
)


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _copy_tree(src: Path, dst: Path) -> dict[str, Any]:
    if not src.exists():
        return {"status": "missing", "src": str(src), "dst": str(dst), "files": 0}
    files = [p for p in src.rglob("*") if p.is_file()]
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for file_path in files:
        rel = file_path.relative_to(src)
        target = dst / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)
            copied += 1
        except Exception:
            continue
    return {"status": "ok", "src": str(src), "dst": str(dst), "files": copied}


def _sync_persistent_runtime_state(direction: str) -> list[dict[str, Any]]:
    if not _truthy(os.getenv("PERSIST_RUNTIME_INVENTORY_STATE_ENABLED"), True):
        return []
    results: list[dict[str, Any]] = []
    for cache_dir_raw, runtime_dir_raw in RUNTIME_STATE_DIRS:
        cache_dir = Path(cache_dir_raw)
        runtime_dir = Path(runtime_dir_raw)
        if direction == "restore":
            results.append(_copy_tree(cache_dir, runtime_dir))
        elif direction == "persist":
            results.append(_copy_tree(runtime_dir, cache_dir))
    return results


def _run_script(path: str) -> dict[str, Any]:
    script = Path(path)
    if not script.exists():
        return {"status": "missing", "path": str(script)}
    try:
        spec = importlib.util.spec_from_file_location(f"harizon_runtime_hook_{script.stem}", script)
        if spec is None or spec.loader is None:
            return {"status": "spec_failed", "path": str(script)}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        main = getattr(module, "main", None)
        if callable(main):
            rc = main()
            return {"status": "ok", "path": str(script), "return_code": int(rc or 0)}
        main_async = getattr(module, "main_async", None)
        if callable(main_async):
            import asyncio

            rc = asyncio.run(main_async())
            return {"status": "ok", "path": str(script), "return_code": int(rc or 0)}
        return {"status": "no_main", "path": str(script)}
    except Exception as exc:
        return {"status": "error", "path": str(script), "error": f"{type(exc).__name__}: {exc}"}


def run_pre_prediction_hooks() -> list[dict[str, Any]]:
    if not _truthy(os.getenv("RUNTIME_PRE_PREDICTION_INVENTORY_HOOKS_ENABLED"), True):
        return []
    results: list[dict[str, Any]] = []
    results.extend({"hook": "restore_persistent_state", **item} for item in _sync_persistent_runtime_state("restore"))
    # Expand the daily inventory before run-once decides which matches to scan.
    # This is bounded by provider runtime policy and fixture-only; it never creates picks.
    if _truthy(os.getenv("FIXTURE_EXPANSION_ENABLED"), True):
        results.append(_run_script("scripts/expand_day_inventory_fixtures.py"))
    return results


def run_post_prediction_hooks() -> list[dict[str, Any]]:
    global _RAN_POST_HOOKS
    if _RAN_POST_HOOKS:
        return []
    _RAN_POST_HOOKS = True
    if not _truthy(os.getenv("RUNTIME_POST_PREDICTION_INVENTORY_HOOKS_ENABLED"), True):
        return []
    results: list[dict[str, Any]] = []
    # Accumulate the just-seen matches into the persistent daily inventory.
    if _truthy(os.getenv("DAY_INVENTORY_ACCUMULATION_ENABLED"), True):
        results.append(_run_script("scripts/accumulate_latest_matches_into_day_inventory.py"))
    # Update kickoff priority and line movement guard before the workflow's
    # controlled fallback publication step reads candidate artifacts.
    if _truthy(os.getenv("LINE_MOVEMENT_GUARD_ENABLED"), True):
        results.append(_run_script("scripts/update_day_inventory_priority_and_line_state.py"))
    results.extend({"hook": "persist_persistent_state", **item} for item in _sync_persistent_runtime_state("persist"))
    return results


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    pre = run_pre_prediction_hooks()
    atexit.register(run_post_prediction_hooks)
    return {"status": "installed", "pre_hooks": pre, "post_hooks_registered": True}
