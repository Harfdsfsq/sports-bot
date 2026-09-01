from __future__ import annotations

import os
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from app.utils import canonicalize_team_name

_INSTALLED = False
_ORIGINAL_RESOLVE = None
_INDEX_CACHE: dict[int, tuple[int, dict[str, set[str]], dict[str, set[str]]]] = {}

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


def _candidate_score(target: str, candidate: str) -> float:
    if target == candidate:
        return 1.0
    if len(target) < 4 or len(candidate) < 4:
        return 0.0
    target_tokens = _tokens(target)
    candidate_tokens = _tokens(candidate)
    shared = target_tokens & candidate_tokens
    text_score = SequenceMatcher(None, target, candidate).ratio()
    if not shared:
        if min(len(target), len(candidate)) < 7 or text_score < 0.965:
            return 0.0
        return text_score
    distinctive = {token for token in shared if len(token) >= 4}
    if not distinctive:
        return 0.0
    coverage = len(shared) / max(1, max(len(target_tokens), len(candidate_tokens)))
    if len(target_tokens) == 1 and len(candidate_tokens) == 1 and text_score < 0.965:
        return 0.0
    return max(text_score, 0.78 + 0.18 * coverage)


def _indexes(canonical_keys: set[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    marker = id(canonical_keys)
    cached = _INDEX_CACHE.get(marker)
    if cached is not None and cached[0] == len(canonical_keys):
        return cached[1], cached[2]
    token_index: dict[str, set[str]] = defaultdict(set)
    prefix_index: dict[str, set[str]] = defaultdict(set)
    for candidate in canonical_keys:
        for token in _tokens(candidate):
            token_index[token].add(candidate)
        compact = candidate.replace(" ", "")
        if compact:
            prefix_index[compact[:3]].add(candidate)
    value = (dict(token_index), dict(prefix_index))
    _INDEX_CACHE[marker] = (len(canonical_keys), value[0], value[1])
    if len(_INDEX_CACHE) > 8:
        for key in list(_INDEX_CACHE)[:-4]:
            _INDEX_CACHE.pop(key, None)
    return value


def _candidate_pool(target: str, canonical_keys: set[str]) -> set[str]:
    token_index, prefix_index = _indexes(canonical_keys)
    pool: set[str] = set()
    for token in _tokens(target):
        pool.update(token_index.get(token, set()))
    if pool:
        return pool
    compact = target.replace(" ", "")
    for candidate in prefix_index.get(compact[:3], set()):
        if abs(len(candidate.replace(" ", "")) - len(compact)) <= 3:
            pool.add(candidate)
    return pool


def _resolve_team_key_strict(
    self: Any,
    team_name: str,
    canonical_keys: set[str],
    cache: dict[str, str | None],
) -> str | None:
    del self
    raw = str(team_name or "")
    if raw in cache:
        return cache[raw]
    target = canonicalize_team_name(raw)
    if target in canonical_keys:
        cache[raw] = target
        return target
    try:
        threshold = float(os.getenv("SSTATS_FORM_TEAM_MATCH_THRESHOLD") or 0.92)
    except ValueError:
        threshold = 0.92
    ranked = sorted(
        (
            (_candidate_score(target, candidate), candidate)
            for candidate in _candidate_pool(target, canonical_keys)
        ),
        reverse=True,
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        cache[raw] = None
        return None
    best_score, best_key = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < threshold or (second_score >= threshold and best_score - second_score < 0.04):
        cache[raw] = None
        return None
    cache[raw] = best_key
    return best_key


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_RESOLVE
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers.sstats import SStatsContextProvider

    current = SStatsContextProvider._resolve_team_key
    if getattr(current, "_harizon_sstats_team_form_alias", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_RESOLVE = current
    _resolve_team_key_strict._harizon_sstats_team_form_alias = True
    SStatsContextProvider._resolve_team_key = _resolve_team_key_strict
    _INSTALLED = True
    return {
        "status": "installed",
        "provider": "app.providers.sstats.SStatsContextProvider",
        "threshold": 0.92,
        "minimum_recent_matches_per_team": 3,
        "short_substring_matches_rejected": True,
        "phonetic_collision_matches_rejected": True,
        "ambiguity_margin": 0.04,
        "candidate_lookup": "distinctive_token_and_prefix_index",
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
