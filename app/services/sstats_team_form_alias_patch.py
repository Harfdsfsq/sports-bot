from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, team_similarity

_INSTALLED = False
_ORIGINAL_TEAM_FORM = None

_NOISE = {
    "fc",
    "cf",
    "sc",
    "ac",
    "club",
    "football",
    "city",
    "united",
    "athletic",
    "sporting",
    "de",
    "the",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in canonicalize_team_name(value).split()
        if token and token not in _NOISE and len(token) >= 3
    }


def _resolve_key(
    team_name: str,
    team_rows: dict[str, list[dict[str, Any]]],
    token_index: dict[str, set[str]],
    cache: dict[str, tuple[str | None, float]],
) -> tuple[str | None, float]:
    target = canonicalize_team_name(team_name)
    if target in cache:
        return cache[target]
    if target in team_rows:
        cache[target] = (target, 1.0)
        return cache[target]
    tokens = _tokens(target)
    candidates: set[str] = set()
    for token in tokens:
        candidates.update(token_index.get(token, set()))
    if not candidates:
        cache[target] = (None, 0.0)
        return cache[target]
    ranked: list[tuple[float, str]] = []
    for candidate in candidates:
        shared = tokens & _tokens(candidate)
        if not shared:
            continue
        score = float(team_similarity(target, candidate))
        coverage = len(shared) / max(1, min(len(tokens), len(_tokens(candidate))))
        score = max(score, 0.70 + 0.24 * coverage)
        ranked.append((score, candidate))
    ranked.sort(reverse=True)
    if not ranked:
        cache[target] = (None, 0.0)
        return cache[target]
    try:
        threshold = float(os.getenv("SSTATS_FORM_TEAM_MATCH_THRESHOLD") or 0.86)
    except ValueError:
        threshold = 0.86
    best_score, best_key = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < threshold or (second_score >= threshold and best_score - second_score < 0.035):
        cache[target] = (None, best_score)
    else:
        cache[target] = (best_key, best_score)
    return cache[target]


def _team_form_contexts(
    self: Any,
    matches: list[Match],
    rows: list[dict[str, Any]],
    preview: dict[str, Any],
) -> dict[str, MatchContext]:
    assert callable(_ORIGINAL_TEAM_FORM)
    contexts = dict(_ORIGINAL_TEAM_FORM(self, matches, rows, preview) or {})
    missing = [match for match in matches if match.match_key not in contexts]
    if not missing:
        return contexts
    team_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        start = self._start(row)
        home = self._team(row, "home")
        away = self._team(row, "away")
        home_goals = self._goals(row, "home")
        away_goals = self._goals(row, "away")
        if start is None or not home or not away or home_goals is None or away_goals is None:
            continue
        home_key = canonicalize_team_name(home)
        away_key = canonicalize_team_name(away)
        if home_key:
            team_rows[home_key].append(
                {"start": start, "gf": float(home_goals), "ga": float(away_goals), "home": True}
            )
        if away_key:
            team_rows[away_key].append(
                {"start": start, "gf": float(away_goals), "ga": float(home_goals), "home": False}
            )
    for values in team_rows.values():
        values.sort(key=lambda item: item["start"], reverse=True)
    token_index: dict[str, set[str]] = defaultdict(set)
    for key in team_rows:
        for token in _tokens(key):
            token_index[token].add(key)
    cache: dict[str, tuple[str | None, float]] = {}
    for match in missing:
        home_key, home_score = _resolve_key(match.home_team, team_rows, token_index, cache)
        away_key, away_score = _resolve_key(match.away_team, team_rows, token_index, cache)
        if not home_key or not away_key:
            continue
        home_recent = team_rows.get(home_key, [])[: self.recent_limit]
        away_recent = team_rows.get(away_key, [])[: self.recent_limit]
        if len(home_recent) < 3 or len(away_recent) < 3:
            continue
        home_for = sum(item["gf"] for item in home_recent) / len(home_recent)
        home_against = sum(item["ga"] for item in home_recent) / len(home_recent)
        away_for = sum(item["gf"] for item in away_recent) / len(away_recent)
        away_against = sum(item["ga"] for item in away_recent) / len(away_recent)
        expected_home = clamp((home_for + away_against) / 2.0, 0.25, 3.75)
        expected_away = clamp((away_for + home_against) / 2.0, 0.25, 3.75)
        confidence = clamp(
            48.0 + min(len(home_recent), len(away_recent)) * 0.8 + min(home_score, away_score) * 4.0,
            52.0,
            62.0,
        )
        effective = max(home_recent[0]["start"], away_recent[0]["start"])
        if isinstance(effective, datetime):
            if effective.tzinfo is None:
                effective = effective.replace(tzinfo=UTC)
            effective_at = effective.astimezone(UTC).isoformat()
        else:
            effective_at = None
        contexts[match.match_key] = MatchContext(
            source="sstats_form_v1",
            payload={"cache_compacted": True},
            expected_home=round(expected_home, 3),
            expected_away=round(expected_away, 3),
            confidence=round(float(confidence), 2),
            details={
                "sstats_api_version": "v1",
                "sstats_mode": "team_form_alias",
                "home_team_key": home_key,
                "away_team_key": away_key,
                "home_match_score": round(home_score, 4),
                "away_match_score": round(away_score, 4),
                "home_recent_sample": len(home_recent),
                "away_recent_sample": len(away_recent),
                "home_gf_avg": round(home_for, 3),
                "home_ga_avg": round(home_against, 3),
                "away_gf_avg": round(away_for, 3),
                "away_ga_avg": round(away_against, 3),
                "effective_at": effective_at,
            },
        )
        if len(preview.setdefault("team_form_alias_examples", [])) < 10:
            preview["team_form_alias_examples"].append(
                {
                    "match_key": match.match_key,
                    "home_key": home_key,
                    "away_key": away_key,
                    "home_score": round(home_score, 3),
                    "away_score": round(away_score, 3),
                }
            )
    return contexts


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_TEAM_FORM
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers.sstats_v1 import SStatsContextProvider

    current = SStatsContextProvider._team_form_contexts
    if getattr(current, "_harizon_sstats_team_form_alias", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_TEAM_FORM = current
    _team_form_contexts._harizon_sstats_team_form_alias = True
    SStatsContextProvider._team_form_contexts = _team_form_contexts
    _INSTALLED = True
    return {
        "status": "installed",
        "threshold": 0.86,
        "minimum_recent_matches_per_team": 3,
        "requires_distinctive_token_overlap": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
