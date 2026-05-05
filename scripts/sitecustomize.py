"""Startup hook for scripts executed as `python scripts/*.py`.

For script entrypoints Python imports this file from the scripts directory.
Every patch is isolated: failure in one patch must not prevent the controlled
fallback confirmation-source patch from running.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _apply_sportlogic_unquarantine() -> None:
    import apply_sportlogic_policy_unquarantine

    apply_sportlogic_policy_unquarantine.main()


def _apply_runtime_patches() -> None:
    import runtime_startup_patches

    runtime_startup_patches.apply_all()


def _apply_sportlogic_hardening() -> None:
    from app.providers import sportlogic_hardening

    sportlogic_hardening.patch_provider_file(ROOT)
    sportlogic_hardening.install()


def _apply_confirmation_source_patch() -> None:
    import apply_confirmation_source_fallback_patch

    apply_confirmation_source_fallback_patch.main()


def _apply_controlled_fallback_prepublish_guard() -> None:
    import controlled_fallback_prepublish_guard

    controlled_fallback_prepublish_guard.install()


def _apply_telegram_controlled_pick_safety() -> None:
    import telegram_controlled_pick_safety

    telegram_controlled_pick_safety.install()


def _install_import_hook() -> None:
    import runtime_startup_patches

    runtime_startup_patches.install_import_hook()


_safe_run("controlled_fallback_prepublish_guard", _apply_controlled_fallback_prepublish_guard)
_safe_run("telegram_controlled_pick_safety", _apply_telegram_controlled_pick_safety)
_safe_run("api_max_usage_patch", _apply_api_patch)
_safe_run("sportlogic_policy_unquarantine", _apply_sportlogic_unquarantine)
_safe_run("runtime_startup_patches.apply_all", _apply_runtime_patches)
_safe_run("sportlogic_hardening", _apply_sportlogic_hardening)
_safe_run("apply_confirmation_source_fallback_patch", _apply_confirmation_source_patch)
_safe_run("runtime_startup_patches.install_import_hook", _install_import_hook)
