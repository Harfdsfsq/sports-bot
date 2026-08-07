from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from app.utils import canonicalize_league_name, canonicalize_team_name, team_similarity

UTC = timezone.utc


@dataclass(slots=True)
class MatchIdentity:
    provider: str
    provider_event_id: str
    sport_key: str
    home: str
    away: str
    league: str
    start_utc: str
    country: str = ""
    gender: str = ""
    competition_type: str = ""
    raw: dict[str, Any] | None = None

    @property
    def home_key(self) -> str:
        return canonical_team(self.home)

    @property
    def away_key(self) -> str:
        return canonical_team(self.away)

    @property
    def league_key(self) -> str:
        return canonical_league(self.league)


@dataclass(slots=True)
class MatchScore:
    score: float
    quality: str
    reasons: list[str]
    swapped: bool = False

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


_ALIAS_CACHE: dict[str, Any] | None = None


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_alias_payload() -> dict[str, Any]:
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None:
        return _ALIAS_CACHE
    merged: dict[str, Any] = {"teams": {}, "leagues": {}}
    for rel in ("config/provider_aliases.json", "config/match_identity_aliases.json"):
        path = _root() / rel
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for group in ("teams", "team_aliases"):
            data = payload.get(group)
            if isinstance(data, dict):
                merged["teams"].update(data)
        for group in ("leagues", "league_aliases"):
            data = payload.get(group)
            if isinstance(data, dict):
                merged["leagues"].update(data)
    _ALIAS_CACHE = merged
    return merged


def _compact(value: str) -> str:
    value = str(value or "").casefold()
    value = re.sub(r"[\u2019'`´]", "", value)
    value = re.sub(r"[^a-zа-яё0-9]+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _strip_team_noise(value: str) -> str:
    raw = _compact(value)
    raw = re.sub(r"\b(fc|cf|sc|afc|ac|club|deportivo|cd|fk|sk|bk|if|calcio)\b", " ", raw)
    raw = re.sub(r"\b(women|woman|femenino|femenina|ladies|u\d{2}|reserves?|ii|b)\b", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def canonical_team(name: str) -> str:
    base = canonicalize_team_name(str(name or ""))
    cleaned = _strip_team_noise(base or name)
    payload = _load_alias_payload()
    lookup: dict[str, str] = {}
    for canonical, aliases in dict(payload.get("teams") or {}).items():
        key = _strip_team_noise(str(canonical))
        if key:
            lookup[key] = key
        if isinstance(aliases, list):
            for alias in aliases:
                alias_key = _strip_team_noise(str(alias))
                if alias_key:
                    lookup[alias_key] = key
    return lookup.get(cleaned, cleaned)


def canonical_league(name: str) -> str:
    base = canonicalize_league_name(str(name or ""))
    compact = _compact(base or name)
    payload = _load_alias_payload()
    lookup: dict[str, str] = {}
    for canonical, aliases in dict(payload.get("leagues") or {}).items():
        key = _compact(canonicalize_league_name(str(canonical)) or str(canonical))
        if key:
            lookup[key] = key
        if isinstance(aliases, list):
            for alias in aliases:
                alias_key = _compact(canonicalize_league_name(str(alias)) or str(alias))
                if alias_key:
                    lookup[alias_key] = key
    return lookup.get(compact, compact)


def _tag_flags(name: str, league: str = "") -> set[str]:
    raw = f"{name} {league}".casefold()
    flags: set[str] = set()
    if re.search(r"\b(women|woman|femenino|femenina|ladies|f)\b", raw):
        flags.add("women")
    if re.search(r"\bu\d{2}\b|\byouth\b|\bjuvenil\b", raw):
        flags.add("youth")
    if re.search(r"\breserves?\b|\bii\b|\bb\b", raw):
        flags.add("reserve")
    if re.search(r"\b(esoccer|e-soccer|simulated|cyber|esports?)\b", raw):
        flags.add("simulated")
    return flags


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            from app.utils import parse_datetime
            dt = parse_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _time_score(a: Any, b: Any, tolerance_hours: float = 12.0) -> tuple[float, float | None]:
    da = _parse_dt(a)
    db = _parse_dt(b)
    if da is None or db is None:
        return 0.0, None
    diff_hours = abs((da - db).total_seconds()) / 3600.0
    if diff_hours <= 0.25:
        return 100.0, diff_hours
    if diff_hours >= tolerance_hours:
        return 0.0, diff_hours
    return max(0.0, 100.0 * (1.0 - diff_hours / tolerance_hours)), diff_hours


def _name_score(a: str, b: str) -> float:
    ca = canonical_team(a)
    cb = canonical_team(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 100.0
    try:
        return max(0.0, min(100.0, float(team_similarity(ca, cb)) * 100.0))
    except Exception:
        return 0.0


def _league_score(a: str, b: str) -> float:
    ca = canonical_league(a)
    cb = canonical_league(b)
    if not ca or not cb:
        return 45.0
    if ca == cb:
        return 100.0
    tokens_a = set(ca.split())
    tokens_b = set(cb.split())
    if not tokens_a or not tokens_b:
        return 45.0
    jaccard = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    return max(0.0, min(100.0, 35.0 + 65.0 * jaccard))


def score_match_identity(
    reference: MatchIdentity,
    candidate: MatchIdentity,
    *,
    exact_tolerance_hours: float = 12.0,
    fuzzy_tolerance_hours: float = 8.0,
) -> MatchScore:
    reasons: list[str] = []
    ref_provider = str(reference.provider or "").strip().casefold()
    cand_provider = str(candidate.provider or "").strip().casefold()
    ref_event_id = str(reference.provider_event_id or "").strip()
    cand_event_id = str(candidate.provider_event_id or "").strip()

    # Provider event IDs are stronger than names and fuzzy time matching.  A
    # conflicting ID from the same provider must never be rescued by a fuzzy
    # name score; otherwise one provider event can be attached to another match.
    if ref_provider and cand_provider and ref_provider == cand_provider:
        if ref_event_id and cand_event_id and ref_event_id == cand_event_id:
            return MatchScore(100.0, "exact", ["provider_event_id_exact"])
        if ref_event_id and cand_event_id and ref_event_id != cand_event_id:
            return MatchScore(0.0, "reject", ["provider_event_id_conflict"])

    ref_flags = _tag_flags(reference.home + " " + reference.away, reference.league)
    cand_flags = _tag_flags(candidate.home + " " + candidate.away, candidate.league)
    hard_conflicts = {"women", "youth", "reserve", "simulated"}
    conflict = (ref_flags ^ cand_flags) & hard_conflicts
    if conflict:
        return MatchScore(score=0.0, quality="reject", reasons=[f"tag_conflict:{','.join(sorted(conflict))}"])

    home = _name_score(reference.home, candidate.home)
    away = _name_score(reference.away, candidate.away)
    swapped_home = _name_score(reference.home, candidate.away)
    swapped_away = _name_score(reference.away, candidate.home)
    swapped = min(swapped_home, swapped_away) > min(home, away) + 8.0
    if swapped:
        home, away = swapped_home, swapped_away
        reasons.append("home_away_swapped")

    time, diff_hours = _time_score(reference.start_utc, candidate.start_utc, max(exact_tolerance_hours, fuzzy_tolerance_hours, 1.0))
    league = _league_score(reference.league, candidate.league)

    score = (home * 0.35) + (away * 0.35) + (time * 0.20) + (league * 0.10)
    if swapped and league < 75.0:
        score -= 12.0
    if diff_hours is not None:
        reasons.append(f"time_diff_hours={diff_hours:.2f}")
    reasons.extend([f"home={home:.1f}", f"away={away:.1f}", f"league={league:.1f}", f"time={time:.1f}"])

    if score >= 92.0 and home >= 88.0 and away >= 88.0 and time >= 80.0:
        quality = "exact"
    elif score >= 82.0 and home >= 78.0 and away >= 78.0 and time >= 55.0:
        quality = "strong"
    elif score >= 68.0 and home >= 68.0 and away >= 68.0 and (diff_hours is None or diff_hours <= fuzzy_tolerance_hours):
        quality = "fuzzy"
    else:
        quality = "reject"
    return MatchScore(score=round(max(0.0, min(100.0, score)), 3), quality=quality, reasons=reasons, swapped=swapped)


def identity_from_match(match: Any, provider: str | None = None) -> MatchIdentity:
    return MatchIdentity(
        provider=str(provider or getattr(match, "source", "") or ""),
        provider_event_id=str(getattr(match, "source_event_id", "") or ""),
        sport_key=str(getattr(match, "sport_key", "") or "soccer"),
        home=str(getattr(match, "home_team", "") or ""),
        away=str(getattr(match, "away_team", "") or ""),
        league=str(getattr(match, "league_name", "") or ""),
        start_utc=str(getattr(match, "commence_time", "") or ""),
        raw=dict(getattr(match, "metadata", {}) or {}),
    )


def best_identity_match(reference: MatchIdentity, candidates: list[MatchIdentity]) -> tuple[MatchIdentity | None, MatchScore]:
    scored_candidates: list[tuple[MatchIdentity, MatchScore]] = [
        (candidate, score_match_identity(reference, candidate)) for candidate in candidates
    ]
    scored_candidates.sort(key=lambda item: item[1].score, reverse=True)
    if not scored_candidates:
        return None, MatchScore(0.0, "reject", ["no_candidates"])

    best, best_score = scored_candidates[0]
    if best_score.quality == "reject":
        return None, best_score

    # A fuzzy/strong winner must be materially better than the runner-up.  This
    # prevents same-team or same-league fixtures from being assigned greedily
    # when the provider omitted a stable event ID.
    if "provider_event_id_exact" not in best_score.reasons and len(scored_candidates) > 1:
        second_score = scored_candidates[1][1]
        margin = best_score.score - second_score.score
        if second_score.quality != "reject" and margin < 5.0:
            return None, MatchScore(
                score=best_score.score,
                quality="reject",
                reasons=[f"ambiguous_identity_margin:{margin:.3f}", "best_candidate_rejected"],
                swapped=best_score.swapped,
            )
    return best, best_score
