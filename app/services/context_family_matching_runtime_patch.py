from __future__ import annotations

"""Runtime context matching and market-family support patch.

The model needs context for totals/BTTS/spreads. Some providers return context
under keys that do not exactly match the odds bootstrap key. This patch expands
context lookup before CandidateFactory builds candidates:

- exact match_key remains authoritative;
- loose same-day/team keys rescue context when strict ids differ;
- reversed home/away loose keys are considered only when teams are clearly the
  same pair;
- rescued context is annotated with family support diagnostics for totals, BTTS
  and spreads.

The patch is intentionally conservative: it never creates a context from odds
alone and never relaxes quality/publication guards.
"""

import copy
import json
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Match, MatchContext

UTC = timezone.utc
PATCH_MARKER = "_harizon_context_family_matching_patch_v1"
REPORT_PATH = Path(".data/exports/latest-context-family-matching-report.json")


def _truthy(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    return re.sub(r"[^a-z0-9а-я]+", "", text)


def _team_pair(home: Any, away: Any) -> tuple[str, str]:
    return _norm(home), _norm(away)


def _match_day(match: Match) -> str:
    try:
        return match.commence_time.astimezone(UTC).date().isoformat()
    except Exception:
        return ""


def _key_tokens(key: Any) -> tuple[str, str, str]:
    parts = [p for p in str(key or "").split("|") if p]
    if len(parts) >= 4:
        return _norm(parts[1]), _norm(parts[2]), str(parts[-1])[:10]
    if len(parts) >= 3:
        return _norm(parts[-3]), _norm(parts[-2]), str(parts[-1])[:10]
    return "", "", ""


def _loose_key(home: Any, away: Any, day: str = "") -> str:
    h, a = sorted(_team_pair(home, away))
    return f"{h}|{a}|{day}" if day else f"{h}|{a}"


def _context_payload(context: Any) -> dict[str, Any]:
    if isinstance(context, MatchContext):
        return context.payload if isinstance(context.payload, dict) else {}
    if isinstance(context, dict):
        payload = context.get("payload")
        return payload if isinstance(payload, dict) else context
    return {}


def _context_details(context: Any) -> dict[str, Any]:
    if isinstance(context, MatchContext):
        return dict(context.details or {})
    if isinstance(context, dict):
        details = context.get("details")
        return dict(details) if isinstance(details, dict) else {}
    return {}


def _context_confidence(context: Any) -> float:
    try:
        return float(getattr(context, "confidence", 0.0) or 0.0)
    except Exception:
        return 0.0


def _context_expected(context: Any) -> tuple[float | None, float | None]:
    for getter in (
        lambda c: (getattr(c, "expected_home", None), getattr(c, "expected_away", None)),
        lambda c: (_context_payload(c).get("expected_home"), _context_payload(c).get("expected_away")),
        lambda c: (_context_payload(c).get("home_xg"), _context_payload(c).get("away_xg")),
        lambda c: (_context_payload(c).get("xg_home"), _context_payload(c).get("xg_away")),
    ):
        try:
            h, a = getter(context)
            if h is not None and a is not None:
                return float(h), float(a)
        except Exception:
            continue
    return None, None


def _family_support(context: Any) -> dict[str, Any]:
    payload = _context_payload(context)
    details = _context_details(context)
    expected_home, expected_away = _context_expected(context)
    hwp = getattr(context, "home_win_probability", None) if isinstance(context, MatchContext) else payload.get("home_win_probability")
    awp = getattr(context, "away_win_probability", None) if isinstance(context, MatchContext) else payload.get("away_win_probability")
    total_probs = payload.get("total_probabilities") or payload.get("totals") or details.get("total_probabilities")
    btts_prob = payload.get("btts_probability") or payload.get("both_teams_to_score_probability") or details.get("btts_probability")
    return {
        "totals": bool(expected_home is not None and expected_away is not None) or isinstance(total_probs, (dict, list)),
        "btts": bool(expected_home is not None and expected_away is not None) or btts_prob is not None,
        "spreads": bool(expected_home is not None and expected_away is not None) or (hwp is not None and awp is not None),
        "expected_home": expected_home,
        "expected_away": expected_away,
        "confidence": _context_confidence(context),
    }


def _annotate_context(context: Any, *, rescued_by: str, source_key: str, target_key: str) -> Any:
    support = _family_support(context)
    if isinstance(context, MatchContext):
        details = dict(context.details or {})
        details["context_family_matching"] = {
            "rescued_by": rescued_by,
            "source_key": source_key,
            "target_key": target_key,
            "family_support": support,
        }
        try:
            return replace(context, details=details)
        except Exception:
            cloned = copy.copy(context)
            try:
                cloned.details = details
            except Exception:
                pass
            return cloned
    if isinstance(context, dict):
        cloned = dict(context)
        details = dict(cloned.get("details") or {})
        details["context_family_matching"] = {"rescued_by": rescued_by, "source_key": source_key, "target_key": target_key, "family_support": support}
        cloned["details"] = details
        return cloned
    return context


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _expand_contexts(matches: list[Match], offers_by_match: dict[str, Any], contexts_by_match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    expanded = dict(contexts_by_match or {})
    by_loose: dict[str, tuple[str, Any]] = {}
    by_loose_noday: dict[str, tuple[str, Any]] = {}
    for key, context in (contexts_by_match or {}).items():
        h, a, day = _key_tokens(key)
        if not h or not a:
            payload = _context_payload(context)
            h = _norm(payload.get("home_team") or payload.get("home") or payload.get("home_name"))
            a = _norm(payload.get("away_team") or payload.get("away") or payload.get("away_name"))
        if h and a:
            loose = "|".join(sorted([h, a]) + ([day] if day else []))
            current = by_loose.get(loose)
            if current is None or _context_confidence(context) >= _context_confidence(current[1]):
                by_loose[loose] = (str(key), context)
            loose2 = "|".join(sorted([h, a]))
            current2 = by_loose_noday.get(loose2)
            if current2 is None or _context_confidence(context) >= _context_confidence(current2[1]):
                by_loose_noday[loose2] = (str(key), context)
    rescued = 0
    exact_hits = 0
    missing_after = 0
    support_counts = {"totals": 0, "btts": 0, "spreads": 0}
    missing_by_family = {"totals": 0, "btts": 0, "spreads": 0}
    for match in matches or []:
        key = match.match_key
        families = {str(getattr(item, "family", "")) for item in (offers_by_match.get(key) or [])}
        if key in expanded:
            exact_hits += 1
            support = _family_support(expanded[key])
            for family in support_counts:
                support_counts[family] += int(bool(support.get(family)))
            continue
        day = _match_day(match)
        lookup = _loose_key(match.home_team, match.away_team, day)
        found = by_loose.get(lookup)
        rescued_by = "loose_same_day"
        if found is None:
            found = by_loose_noday.get(_loose_key(match.home_team, match.away_team, ""))
            rescued_by = "loose_team_pair"
        if found is not None:
            source_key, context = found
            expanded[key] = _annotate_context(context, rescued_by=rescued_by, source_key=source_key, target_key=key)
            rescued += 1
            support = _family_support(expanded[key])
            for family in support_counts:
                support_counts[family] += int(bool(support.get(family)))
        else:
            missing_after += 1
            for family in ("totals", "btts", "spreads"):
                if family in families:
                    missing_by_family[family] += 1
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "matches_seen": len(matches or []),
        "matches_with_offers": sum(1 for match in matches or [] if offers_by_match.get(match.match_key)),
        "contexts_before": len(contexts_by_match or {}),
        "contexts_after": len(expanded),
        "exact_hits": exact_hits,
        "rescued_contexts": rescued,
        "missing_context_after": missing_after,
        "family_support_counts": support_counts,
        "missing_context_by_family": missing_by_family,
    }
    return expanded, report


def install() -> bool:
    if not _truthy(os.getenv("CONTEXT_FAMILY_MATCHING_PATCH_ENABLED"), True):
        return False
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return False
    if getattr(CandidateFactory, PATCH_MARKER, False):
        return False
    original = CandidateFactory.build_candidates

    def build_candidates_patched(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        expanded, report = _expand_contexts(list(matches or []), dict(offers_by_match or {}), dict(contexts_by_match or {}))
        _write_report(report)
        return original(self, matches, offers_by_match, expanded, market_signals_by_match)

    CandidateFactory.build_candidates = build_candidates_patched
    setattr(CandidateFactory, PATCH_MARKER, True)
    return True
