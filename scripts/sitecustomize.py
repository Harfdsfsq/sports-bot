"""Startup hook for scripts executed as `python scripts/*.py`.

For script entrypoints Python imports this file from the scripts directory.
Every patch is isolated: failure in one patch must not prevent the controlled
fallback confirmation-source patch from running.
"""

from __future__ import annotations


def _safe_run(label: str, func) -> None:
    try:
        func()
    except Exception as exc:
        try:
            print(f"sitecustomize patch skipped: {label}: {type(exc).__name__}: {exc}")
        except Exception:
            pass


def _apply_api_patch() -> None:
    import api_max_usage_patch

    api_max_usage_patch.apply_api_max_usage_patch()


def _apply_runtime_patches() -> None:
    import runtime_startup_patches

    runtime_startup_patches.apply_all()


def _apply_confirmation_source_patch() -> None:
    import apply_confirmation_source_fallback_patch

    apply_confirmation_source_fallback_patch.main()


def _install_import_hook() -> None:
    import runtime_startup_patches

    runtime_startup_patches.install_import_hook()


_safe_run("api_max_usage_patch", _apply_api_patch)
_safe_run("runtime_startup_patches.apply_all", _apply_runtime_patches)
_safe_run("apply_confirmation_source_fallback_patch", _apply_confirmation_source_patch)
_safe_run("runtime_startup_patches.install_import_hook", _install_import_hook)
