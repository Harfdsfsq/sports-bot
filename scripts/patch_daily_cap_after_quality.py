from __future__ import annotations

"""Move the daily publication cap behind tier/final candidate evaluation.

The daily cap is a portfolio limit, not a quality signal.  When it runs inside the
hard-reject stage, ``evaluate_candidate`` returns before tier and final guards are
executed.  Diagnostics then cannot distinguish a genuinely clean candidate from a
proxy/single-source candidate that merely hit the cap first.

This patch suppresses the v18 daily-cap call while the hard-reject wrapper runs and
adds the same cap/reserved-slot reasons from the final publication guard instead.
No candidate can be published above the cap; candidates that never pass a tier do
not need a portfolio-limit reason.
"""

from datetime import UTC, datetime
from typing import Any


DAILY_REASON_PREFIXES = (
    "controlled_fallback_daily_limit_reached",
    "controlled_fallback_reserved_daily_slot_held_until",
)


def _is_daily_reason(value: Any) -> bool:
    text = str(value or "")
    return any(text.startswith(prefix) for prefix in DAILY_REASON_PREFIXES)


def install(v18: Any) -> dict[str, Any]:
    base = getattr(v18, "base", None)
    if base is None:
        return {"status": "skipped", "reason": "base_module_missing"}
    if getattr(base, "_daily_cap_after_quality_patch_installed", False):
        return {"status": "already_installed"}

    original_hard = getattr(base, "hard_reject_reasons", None)
    original_final = getattr(base, "final_publish_guard_reasons", None)
    daily_reasons = getattr(v18, "_daily_limit_reasons", None)
    if not callable(original_hard) or not callable(original_final) or not callable(daily_reasons):
        return {"status": "skipped", "reason": "required_functions_missing"}

    def hard_reject_without_daily_cap(
        candidate: dict[str, Any],
        metrics: dict[str, Any],
        sent_index: dict[str, Any],
    ) -> list[str]:
        # v18's hard wrapper resolves ``_daily_limit_reasons`` from its module at
        # call time. Replace it only for this call, so the cap produces neither an
        # early reason nor a misleading guard event.
        current_daily = getattr(v18, "_daily_limit_reasons", None)
        setattr(v18, "_daily_limit_reasons", lambda _candidate, _metrics: [])
        try:
            reasons = list(original_hard(candidate, metrics, sent_index) or [])
        finally:
            setattr(v18, "_daily_limit_reasons", current_daily)
        # Defensive removal in case another wrapper cached a prior daily function.
        return [reason for reason in reasons if not _is_daily_reason(reason)]

    def final_publish_guard_with_daily_cap(
        candidate: dict[str, Any],
        metrics: dict[str, Any],
        tier: str,
    ) -> list[str]:
        reasons = list(original_final(candidate, metrics, tier) or [])
        for reason in list(daily_reasons(candidate, metrics) or []):
            if reason not in reasons:
                reasons.append(reason)
        return reasons

    base.hard_reject_reasons = hard_reject_without_daily_cap
    base.final_publish_guard_reasons = final_publish_guard_with_daily_cap
    base._daily_cap_after_quality_patch_installed = True
    return {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "policy": "daily cap is enforced after tier/final quality checks; publication limit is unchanged",
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
