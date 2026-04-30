"""Startup patches for scripts executed as `python scripts/*.py`.

When Python runs a file from the scripts directory, this directory is first on
sys.path.  This module is therefore imported before the target script and can
apply safe runtime guards without relying on workflow command changes.
"""

from __future__ import annotations

from pathlib import Path


def _patch_controlled_fallback_source_guard() -> None:
    path = Path(__file__).resolve().with_name("publish_controlled_fallback.py")
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return
    if "controlled_fallback_sources_below_min" in text:
        return

    marker = '    if metrics["sources_count"] <= 0:\n        reasons.append("missing_sources")\n'
    inject = marker + (
        '    if env_bool("CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES", True):\n'
        '        min_sources = max(1, env_int("CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES", 2))\n'
        '        if int(metrics.get("sources_count") or 0) < min_sources:\n'
        '            reasons.append(f"controlled_fallback_sources_below_min:{int(metrics.get(\"sources_count\") or 0)}/{min_sources}")\n'
    )
    if marker not in text:
        return
    try:
        path.write_text(text.replace(marker, inject, 1), encoding="utf-8")
    except Exception:
        return


_patch_controlled_fallback_source_guard()
