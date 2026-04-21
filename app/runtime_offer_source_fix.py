from __future__ import annotations

import os

_PATCH_APPLIED = False

def _apply_env_overrides() -> None:
    # The bot currently receives almost all offer rows from a single odds provider
    # (typically odds_api_io). Requiring 2 distinct offer sources kills candidates
    # before they even reach quality filters. Keep publication protection at the
    # bookmaker/non-core/context layers, but allow raw candidate creation with 1 source.
    os.environ["MIN_SOURCES_PUBLISH"] = "1"
    os.environ.setdefault("MIN_BOOKS_PUBLISH", "2")

def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, "_offer_source_fix_applied", False):
        return

    original = CandidateFactory._candidate_from_bucket

    def patched(self, *args, **kwargs):
        old_value = None
        mutated = False
        try:
            if hasattr(self, "settings") and getattr(self.settings, "min_sources_publish", None) not in (None, 1):
                old_value = getattr(self.settings, "min_sources_publish")
                try:
                    setattr(self.settings, "min_sources_publish", 1)
                    mutated = True
                except Exception:
                    mutated = False
            return original(self, *args, **kwargs)
        finally:
            if mutated:
                try:
                    setattr(self.settings, "min_sources_publish", old_value)
                except Exception:
                    pass

    CandidateFactory._candidate_from_bucket = patched
    CandidateFactory._offer_source_fix_applied = True

def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _apply_env_overrides()
    _patch_candidate_factory()
    _PATCH_APPLIED = True

_apply()
