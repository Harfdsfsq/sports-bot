from __future__ import annotations

"""Allow the reserved daily slot to be used by candidates that expire before release.

The guarded fallback keeps one late-day slot so early B-tier picks do not consume
all daily capacity.  That is useful only when a candidate can still be published
after the reserved-slot release time.  If the candidate's latest allowed publish
time is before the release time, holding the slot means the pick can never be
sent.  This runtime patch preserves the reserve policy for later matches while
letting expiring candidates compete for the last slot.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _candidate_latest_publish_utc(v18: Any, candidate: dict[str, Any]) -> datetime | None:
    kickoff = v18._parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
    if kickoff is None:
        return None
    min_lead = _as_int(os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES") or os.getenv("MIN_KICKOFF_LEAD_MINUTES") or 15, 15)
    return kickoff - timedelta(minutes=max(0, min_lead))


def _reserved_release_local(v18: Any, now_utc: datetime) -> datetime:
    tz = v18._local_tz()
    now_local = now_utc.astimezone(tz)
    release_hour = max(0, min(23, _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_HOUR") or 18, 18)))
    release_minute = max(0, min(59, _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_MINUTE") or 0, 0)))
    return now_local.replace(hour=release_hour, minute=release_minute, second=0, microsecond=0)


def install(v18: Any) -> None:
    original = getattr(v18, "_daily_limit_reasons", None)
    if not callable(original):
        return
    if getattr(v18, "_reserved_slot_expiring_candidate_patch_installed", False):
        return

    def patched_daily_limit_reasons(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(original(candidate, metrics) or [])
        reserved_reasons = [
            reason for reason in reasons
            if str(reason).startswith("controlled_fallback_reserved_daily_slot_held_until:")
        ]
        if not reserved_reasons:
            return reasons
        if not _truthy(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_ALLOW_EXPIRING_CANDIDATE"), True):
            return reasons

        now_utc = datetime.now(UTC)
        release_at_local = _reserved_release_local(v18, now_utc)
        latest_publish = _candidate_latest_publish_utc(v18, candidate)
        if latest_publish is None:
            return reasons
        latest_publish_local = latest_publish.astimezone(release_at_local.tzinfo)
        if latest_publish_local > release_at_local:
            return reasons

        filtered = [reason for reason in reasons if reason not in reserved_reasons]
        event = {
            "guard": "controlled_fallback_reserved_daily_slot_expiring_candidate_override",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "removed_reasons": reserved_reasons,
            "latest_publish_local": latest_publish_local.isoformat(),
            "release_at_local": release_at_local.isoformat(),
            "reasons": ["reserved_slot_override_expiring_before_release"],
        }
        try:
            v18._GUARD_EVENTS.append(event)
        except Exception:
            pass
        return filtered

    v18._daily_limit_reasons = patched_daily_limit_reasons
    v18._reserved_slot_expiring_candidate_patch_installed = True
