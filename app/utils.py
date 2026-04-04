from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def normalize_bookmaker_name(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return "".join(ch for ch in raw if ch.isalnum())


def _strip_accents_like(value: str) -> str:
    return value.replace("ё", "е")


def canonicalize_team_name(value: str | None) -> str:
    text = _strip_accents_like((value or "").lower())
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|fk|cf|sc|ac|cd|ud|bk|if|sk|jk|fk|afc|bsc|sv|club|deportivo|atletico|athletic)\b", " ", text)
    text = re.sub(r"\b(u\d{2}|u\d{1,2}|women|woman|ladies|reserves|b|ii|iii)\b", " ", text)
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonicalize_league_name(value: str | None) -> str:
    text = _strip_accents_like((value or "").lower())
    text = re.sub(r"[^\w\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_match_key(sport_key: str, home_team: str, away_team: str, commence_time: datetime) -> str:
    dt = commence_time.astimezone(UTC).strftime("%Y%m%d%H%M")
    return "|".join([sport_key, canonicalize_team_name(home_team), canonicalize_team_name(away_team), dt])


def build_loose_match_key(sport_key: str, home_team: str, away_team: str) -> str:
    return "|".join([sport_key, canonicalize_team_name(home_team), canonicalize_team_name(away_team)])


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        number = int(value)
        if abs(number) > 1_000_000_000_000:
            number = number // 1000
        return datetime.fromtimestamp(number, tz=UTC)
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty datetime")
    if text.isdigit():
        return parse_datetime(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _token_overlap(a: str, b: str) -> float:
    a_set = set(a.split())
    b_set = set(b.split())
    if not a_set or not b_set:
        return 0.0
    inter = len(a_set & b_set)
    union = len(a_set | b_set)
    return inter / union if union else 0.0


def score_event_match(
    *,
    sport: str,
    match_home: str,
    match_away: str,
    match_start: datetime,
    match_league: str,
    event_home: str,
    event_away: str,
    event_start: datetime,
    event_league: str,
    exact_tolerance_hours: float = 12.0,
    fuzzy_tolerance_hours: float = 8.0,
) -> tuple[float, str]:
    home_a = canonicalize_team_name(match_home)
    away_a = canonicalize_team_name(match_away)
    home_b = canonicalize_team_name(event_home)
    away_b = canonicalize_team_name(event_away)
    league_a = canonicalize_league_name(match_league)
    league_b = canonicalize_league_name(event_league)

    same_order = _token_overlap(home_a, home_b) * 0.5 + _token_overlap(away_a, away_b) * 0.5
    reverse_order = _token_overlap(home_a, away_b) * 0.5 + _token_overlap(away_a, home_b) * 0.5
    teams_score = max(same_order, reverse_order) * 70.0

    time_diff_hours = abs((match_start.astimezone(UTC) - event_start.astimezone(UTC)).total_seconds()) / 3600.0
    if time_diff_hours <= max(0.25, fuzzy_tolerance_hours):
        time_score = 20.0
        quality = "exact"
    elif time_diff_hours <= max(exact_tolerance_hours, fuzzy_tolerance_hours * 2):
        time_score = 12.0
        quality = "loose"
    else:
        time_score = 0.0
        quality = "fuzzy"

    league_score = _token_overlap(league_a, league_b) * 10.0
    total = teams_score + time_score + league_score
    if total >= 92:
        quality = "exact"
    elif total >= 75:
        quality = "loose"
    else:
        quality = "fuzzy"
    return total, quality


def detect_market_family(value: Any = None, *args: Any, **kwargs: Any) -> str | None:
    text_parts = [str(value or "")] + [str(arg or "") for arg in args] + [str(v or "") for v in kwargs.values()]
    text = " ".join(text_parts).lower()
    if any(key in text for key in ["double chance", "1x", "x2", "12"]):
        return "doubleChance"
    if "draw no bet" in text or "dnb" in text:
        return "dnb"
    if "both teams" in text or "btts" in text:
        return "btts"
    if "team total" in text or "individual total" in text:
        return "teamTotals"
    if any(key in text for key in ["spread", "handicap", "asian handicap", "european handicap"]):
        return "spreads"
    if any(key in text for key in ["total", "goals over/under", "over/under", "goal line"]):
        return "totals"
    if any(key in text for key in ["moneyline", "match winner", "result", "ml", "1x2", "winner"]):
        return "h2h"
    return None


def get_outcome_key(value: Any = None, *args: Any, **kwargs: Any) -> str:
    text = " ".join([str(value or "")] + [str(arg or "") for arg in args] + [str(v or "") for v in kwargs.values()]).strip().lower()
    mapping = {
        "1": "home",
        "home": "home",
        "host": "home",
        "2": "away",
        "away": "away",
        "x": "draw",
        "draw": "draw",
        "tie": "draw",
        "yes": "yes",
        "no": "no",
        "1x": "home_or_draw",
        "x2": "away_or_draw",
        "12": "home_or_away",
    }
    return mapping.get(text, text)


def get_total_selection_key(value: Any = None, *args: Any, **kwargs: Any) -> str:
    text = " ".join([str(value or "")] + [str(arg or "") for arg in args] + [str(v or "") for v in kwargs.values()]).lower()
    if "under" in text or "less" in text or "мень" in text:
        return "under"
    return "over"


def get_spread_selection_key(value: Any = None, *args: Any, **kwargs: Any) -> str:
    text = " ".join([str(value or "")] + [str(arg or "") for arg in args] + [str(v or "") for v in kwargs.values()]).lower().strip()
    if text in {"1", "home", "host"}:
        return "home"
    if text in {"2", "away", "guest"}:
        return "away"
    if "home" in text or "1" == text:
        return "home"
    if "away" in text or "2" == text:
        return "away"
    return text or "home"


def infer_team_total_side(value: Any = None, *args: Any, **kwargs: Any) -> str | None:
    text = " ".join([str(value or "")] + [str(arg or "") for arg in args] + [str(v or "") for v in kwargs.values()]).lower()
    if any(key in text for key in ["home", "host", "1"]):
        return "home"
    if any(key in text for key in ["away", "guest", "2"]):
        return "away"
    return None
