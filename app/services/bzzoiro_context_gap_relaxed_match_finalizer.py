from __future__ import annotations

"""Relaxed matching for Bzzoiro context gap pass only.

After source-id safety fixes, the Bzzoiro gap pass stopped producing 404s but
still matched 0/55 targets. It fetches useful rows, yet most target matches have
no Bzzoiro source id, so we need a controlled fuzzy fallback.

This patch does not change normal Bzzoiro matching. It wraps the gap-pass call and
sets a transient `_harizon_bzzoiro_gap_mode` flag on the provider. While that flag
is enabled, `_acceptance_diagnostic` permits slightly lower scores for fuzzy/loose
matches. Publication guards still require 2+ sources and positive value.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-bzzoiro-context-gap-relaxed-match-finalizer.json"


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _float_env(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw in (None, ""):
            return default
        return float(str(raw).replace(",", "."))
    except Exception:
        return default


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
        from app.services import bzzoiro_context_gap_finalizer as gap
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload

    min_loose = _float_env("BZZOIRO_CONTEXT_GAP_MIN_LOOSE_SCORE", 56.0)
    min_fuzzy = _float_env("BZZOIRO_CONTEXT_GAP_MIN_FUZZY_SCORE", 60.0)

    current_accept = BzzoiroContextProvider._acceptance_diagnostic
    if not getattr(current_accept, "_harizon_gap_relaxed", False):
        def acceptance_diagnostic_gap_relaxed(self, match, event_league: str, quality: str | None, score: float):  # type: ignore[no-untyped-def]
            accepted, reason, required = current_accept(self, match, event_league, quality, score)
            if accepted or not getattr(self, "_harizon_bzzoiro_gap_mode", False):
                return accepted, reason, required
            if quality is None:
                return accepted, reason, required
            # Keep some league protection for fuzzy rows, but do not require full
            # league name match for competitions where providers format youth,
            # reserve or regional league names differently.
            min_score = min_fuzzy if quality == "fuzzy" else min_loose
            if score >= min_score:
                return True, "accepted_gap_relaxed", min_score
            return accepted, reason, required
        acceptance_diagnostic_gap_relaxed._harizon_gap_relaxed = True  # type: ignore[attr-defined]
        BzzoiroContextProvider._acceptance_diagnostic = acceptance_diagnostic_gap_relaxed  # type: ignore[assignment]

    current_gap_pass = gap._gap_pass
    if not getattr(current_gap_pass, "_harizon_gap_mode_flag", False):
        async def gap_pass_with_mode_flag(self, matches, existing_contexts):  # type: ignore[no-untyped-def]
            old = getattr(self, "_harizon_bzzoiro_gap_mode", False)
            try:
                setattr(self, "_harizon_bzzoiro_gap_mode", True)
                return await current_gap_pass(self, matches, existing_contexts)
            finally:
                try:
                    setattr(self, "_harizon_bzzoiro_gap_mode", old)
                except Exception:
                    pass
        gap_pass_with_mode_flag._harizon_gap_mode_flag = True  # type: ignore[attr-defined]
        gap._gap_pass = gap_pass_with_mode_flag  # type: ignore[assignment]

    payload.update({
        "status": "installed",
        "min_loose_score": min_loose,
        "min_fuzzy_score": min_fuzzy,
        "scope": "bzzoiro_context_gap_pass_only",
    })
    _write_json(REPORT_PATH, payload)
    return payload
