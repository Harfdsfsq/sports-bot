from __future__ import annotations

"""Guard against cross-league / cross-time fuzzy fixture matches.

The provider smoke samples exposed a concrete bad match:
Bzzoiro Benfica — Braga, Liga Portugal Betclic, 19:15 was matched to an odds
U23 cup fixture at 15:00.  Teams looked similar, but competition family and
kickoff did not match. This layer wraps app.utils.score_event_match and rejects
that class of false-positive.
"""

from datetime import datetime, timezone
from typing import Any

PATCH_MARKER = "_harizon_provider_match_time_league_guard_v1"

YOUTH_TOKENS = {"u17", "u18", "u19", "u20", "u21", "u23", "reserve", "reserves", "women", "w"}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            from app.utils import parse_datetime
            dt = parse_datetime(value)
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_between(left: Any, right: Any) -> float | None:
    a = _parse_time(left)
    b = _parse_time(right)
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds()) / 3600.0


def _norm(value: Any) -> str:
    try:
        from app.utils import normalize_text
        return normalize_text(str(value or ""))
    except Exception:
        return str(value or "").lower().strip()


def _tokens(value: Any) -> set[str]:
    return {part for part in _norm(value).replace("-", " ").split() if part}


def _has_youth_mismatch(match_league: Any, event_league: Any) -> bool:
    left = _tokens(match_league)
    right = _tokens(event_league)
    return bool((left & YOUTH_TOKENS) ^ (right & YOUTH_TOKENS))


def _league_related(utils: Any, match_league: Any, event_league: Any) -> bool:
    try:
        if utils.league_similarity(str(match_league or ""), str(event_league or "")) >= 0.52:
            return True
    except Exception:
        pass
    left = _tokens(match_league)
    right = _tokens(event_league)
    important = {"premier", "league", "liga", "superliga", "serie", "cup", "taca", "championship", "allsvenskan", "ekstraklasa"}
    return bool((left & right) & important)


def _should_reject(utils: Any, kwargs: dict[str, Any], quality: str | None) -> bool:
    if quality != "fuzzy":
        return False
    match_league = kwargs.get("match_league")
    event_league = kwargs.get("event_league")
    delta = _hours_between(kwargs.get("match_start"), kwargs.get("event_start"))
    if _has_youth_mismatch(match_league, event_league):
        return True
    related = _league_related(utils, match_league, event_league)
    if not related and delta is not None and delta > 1.5:
        return True
    if not related and delta is None:
        return True
    # Same teams in a different competition can still be a wrong fixture if the
    # kickoff differs materially. Require tight time when league family is weak.
    if not related:
        return True
    return False


def install() -> bool:
    import app.utils as utils

    if getattr(utils, PATCH_MARKER, False):
        return False
    original = getattr(utils, "_harizon_time_league_guard_original_score_event_match", utils.score_event_match)
    setattr(utils, "_harizon_time_league_guard_original_score_event_match", original)

    def score_event_match_guarded(**kwargs: Any):
        score, quality = original(**kwargs)
        if score > 0 and _should_reject(utils, kwargs, quality):
            return 0.0, None
        return score, quality

    utils.score_event_match = score_event_match_guarded
    setattr(utils, PATCH_MARKER, True)
    return True
