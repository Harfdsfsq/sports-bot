from __future__ import annotations

"""Runtime entry hooks for the sports bot.

This package-level hook applies safety guardrails early for every normal entrypoint
(`python -m app.cli`, FastAPI import, etc.) without forcing a large invasive patch
inside the existing production modules.
"""

try:
    from app.runtime_bot_fix import apply_runtime_fixes

    apply_runtime_fixes()
except Exception:
    # Never break package import because of an optional runtime guardrail patch.
    pass
