from __future__ import annotations

import math
import re
import unicodedata
from datetime import UTC, datetime
from statistics import median
from typing import Iterable

CYRILLIC_MAP = {
    "a": "a",
}

TRANSLIT_MAP = {
    "a": "a",
    "b": "b",
    "v": "v",
    "g": "g",
    "d": "d",
    "e": "e",
    "z": "z",
    "i": "i",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "o",
    "p": "p",
    "r": "r",
    "s": "s",
    "t": "t",
    "u": "u",
    "f": "f",
    "h": "h",
    "c": "c",
    "y": "y",
}

_RU_MAP = {
    "a": "a",
}

_CYR_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

BOOKMAKER_ALIAS_MAP = {
    "unibet": "unibet",
    "unibetuk": "unibet",
    "unibetfr": "unibet",
    "unibetnl": "unibet",
    "unibetse": "unibet",
    "bet365": "bet365",
    "bet365com": "bet365",
    "bet365sportsbook": "bet365",
    "betfair": "betfair",
    "betfairexchange": "betfair",
}

TEAM_ALIAS_MAP = {
    "internacional": "internacional",
    "sc internacional": "internacional",
    "sport club internacional": "internacional",
    "athletico": "athletico pr",
    "athletico pr": "athletico pr",
    "athletico paranaense": "athletico pr",
    "atletico paranaense": "athletico pr",
    "red bull bragantino": "bragantino",
    "rb bragantino": "bragantino",
    "bragantino": "bragantino",
    "vasco gama": "vasco da gama",
    "vasco da gama": "vasco da gama",
    "atletico mineiro": "atletico mineiro",
    "atletico mineiro mg": "atletico mineiro",
    "parma permsky kray": "parma",
    "kk mega basket belgrade": "mega basket",
    "pole france": "pole france",
    "st vallier": "saint vallier",
    "saint vallier": "saint vallier",
    "cska st petersburg": "cska st petersburg",
    "ska st petersburg": "ska st petersburg",
    "dinamo neva st petersburg": "dinamo neva st petersburg",
    "north macedonia": "north macedonia",
    "czech republic": "czechia",
    "czechia": "czechia",
}

TEAM_STOP_WORDS = {
    "fc",
    "cf",
    "ac",
    "sc",
    "club",
    "fk",
    "bk",
    "afc",
    "calcio",
    "hc",
    "bc",
    "kk",
    "esporte",
    "clube",
    "deportivo",
    "de",
    "da",
    "del",
    "do",
    "the",
    "ec",
    "cd",
    "ud",
    "sd",
}

LOW_TIER_PATTERNS = [
    r"\bu17\b",
    r"\bu18\b",
    r"\bu19\b",
    r"\bu20\b",
    r"\bu21\b",
    r"\bu23\b",
    r"\byouth\b",
    r"\breserve\b",
    r"\breserves\b",
    r"\bwomen\b",
    r"\bwomens\b",
    r"\besports\b",
    r"\bfriendly\b",
]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def round2(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def avg(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def weighted_average(pairs: Iterable[tuple[float, float]]) -> float | None:
    total_weight = 0.0
    total_value = 0.0
    for value, weight in pairs:
        if weight <= 0:
            continue
        total_weight += weight
        total_value += value * weight
    if total_weight <= 0:
        return None
    return total_value / total_weight


def transliterate_cyrillic_to_latin(value: str) -> str:
    out: list[str] = []
    for ch in str(value or ""):
        lower = ch.lower()
        out.append(_CYR_MAP.get(lower, lower))
    return "".join(out)


def normalize_text(value: str) -> str:
    text = transliterate_cyrillic_to_latin(str(value or ""))
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = (
        text.lower()
        .replace("&", " and ")
        .replace("st.", " saint ")
        .replace("st ", " saint ")
    )
    replacements = {
        r"\bsaint\b": " saint ",
        r"\bu\.?s\.?a\.?\b": " usa ",
        r"\bunited states\b": " usa ",
        r"\bivory coast\b": " cote d ivoire ",
        r"\b(?:women|womens|ladies|zh|femminile|femenino|feminino)\b": " women ",
        r"\b(?:reserve|reserves|res|ii team|b team)\b": " reserves ",
        r"\b(?:u17|u18|u19|u20|u21|u23)\b": " ",
        r"\butd\b": " united ",
        r"\biii\b": " 3 ",
        r"\bii\b": " 2 ",
        r"\band\b": " ",
        r"\b(?:fc|cf|ac|sc|club|fk|bk|afc|calcio|hc|bc|kk|baseball|basketball|hockey|club de futbol|esporte clube|deportivo|de|da|del|cd|ud|sd)\b": " ",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonicalize_team_name(name: str) -> str:
    raw = normalize_text(name)
    if raw in TEAM_ALIAS_MAP:
        return TEAM_ALIAS_MAP[raw]
    parts = [part for part in raw.split() if part and part not in TEAM_STOP_WORDS]
    compact = " ".join(parts).strip()
    return TEAM_ALIAS_MAP.get(compact, compact)


def canonicalize_league_name(name: str) -> str:
    text = transliterate_cyrillic_to_latin(str(name or ""))
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\benglish premier league\b", "epl", text)
    text = re.sub(r"\bla liga\b", "laliga", text)
    return text


def normalize_bookmaker_name(name: str) -> str:
    raw = "".join(ch for ch in str(name or "").lower() if ch.isalnum())
    if not raw:
        return ""
    if raw.startswith("unibet"):
        return "unibet"
    if raw.startswith("bet365"):
        return "bet365"
    return BOOKMAKER_ALIAS_MAP.get(raw, raw)


def make_bookmaker_lookup(names: Iterable[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name in names:
        key = normalize_bookmaker_name(name)
        if key:
            out[key] = True
    return out


def is_low_tier_league(league_name: str) -> bool:
    value = canonicalize_league_name(league_name)
    return any(re.search(pattern, value) for pattern in LOW_TIER_PATTERNS)


def get_date_key(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date().isoformat()
    raw = str(value or "").strip()
    if not raw:
        return "nodate"
    try:
        return parse_datetime(raw).astimezone(UTC).date().isoformat()
    except Exception:
        return raw[:10]


def sort_pair(a: str, b: str) -> str:
    return "|".join(sorted([canonicalize_team_name(a), canonicalize_team_name(b)]))


def build_match_key(sport: str, home: str, away: str, when: datetime | str) -> str:
    return f"{sport}|{sort_pair(home, away)}|{get_date_key(when)}"


def build_loose_match_key(sport: str, home: str, away: str) -> str:
    return f"{sport}|{sort_pair(home, away)}"


def soft_contains_team(a: str, b: str) -> bool:
    ca = canonicalize_team_name(a)
    cb = canonicalize_team_name(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    return ca in cb or cb in ca


def token_similarity(a: str, b: str) -> float:
    a_tokens = set(canonicalize_team_name(a).split())
    b_tokens = set(canonicalize_team_name(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    if union <= 0:
        return 0.0
    return intersection / union


def fuzzy_teams_equivalent(home_a: str, away_a: str, home_b: str, away_b: str) -> bool:
    direct = soft_contains_team(home_a, home_b) and soft_contains_team(away_a, away_b)
    reverse = soft_contains_team(home_a, away_b) and soft_contains_team(away_a, home_b)
    return direct or reverse


def match_teams_equivalent(home_a: str, away_a: str, home_b: str, away_b: str) -> bool:
    a1 = canonicalize_team_name(home_a)
    a2 = canonicalize_team_name(away_a)
    b1 = canonicalize_team_name(home_b)
    b2 = canonicalize_team_name(away_b)
    return (a1 == b1 and a2 == b2) or (a1 == b2 and a2 == b1) or fuzzy_teams_equivalent(home_a, away_a, home_b, away_b)


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty datetime")
    raw = raw.replace("Z", "+00:00")
    if raw.endswith("+0000"):
        raw = raw[:-5] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def date_diff_hours(a: datetime | str, b: datetime | str) -> float | None:
    try:
        da = parse_datetime(a)
        db = parse_datetime(b)
    except Exception:
        return None
    return abs((da - db).total_seconds()) / 3600.0


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
    exact_tolerance_hours: float,
    fuzzy_tolerance_hours: float,
) -> tuple[float, str | None]:
    if not match_teams_equivalent(match_home, match_away, event_home, event_away):
        direct_home = token_similarity(match_home, event_home)
        direct_away = token_similarity(match_away, event_away)
        reverse_home = token_similarity(match_home, event_away)
        reverse_away = token_similarity(match_away, event_home)
        direct_score = direct_home + direct_away
        reverse_score = reverse_home + reverse_away
        teams_score = max(direct_score, reverse_score)
        if teams_score < 1.0:
            return 0.0, None
    diff = date_diff_hours(match_start, event_start)
    if diff is None:
        diff = 999.0
    league_same = canonicalize_league_name(match_league) == canonicalize_league_name(event_league)
    if build_match_key(sport, match_home, match_away, match_start) == build_match_key(sport, event_home, event_away, event_start):
        if diff <= exact_tolerance_hours:
            return 100.0 + max(0.0, 6.0 - diff), "exact"
    if build_loose_match_key(sport, match_home, match_away) == build_loose_match_key(sport, event_home, event_away):
        if diff <= exact_tolerance_hours:
            return 92.0 + max(0.0, 5.0 - diff), "loose"
    if diff > fuzzy_tolerance_hours:
        return 0.0, None
    direct_home = token_similarity(match_home, event_home)
    direct_away = token_similarity(match_away, event_away)
    reverse_home = token_similarity(match_home, event_away)
    reverse_away = token_similarity(match_away, event_home)
    direct_score = direct_home + direct_away
    reverse_score = reverse_home + reverse_away
    teams_score = max(direct_score, reverse_score)
    if teams_score < 1.0:
        return 0.0, None
    score = teams_score * 40.0
    score += max(0.0, 12.0 - diff) * 2.2
    if league_same:
        score += 10.0
    elif canonicalize_league_name(match_league) and canonicalize_league_name(match_league) in canonicalize_league_name(event_league):
        score += 4.0
    if score < 48.0:
        return 0.0, None
    return score, "fuzzy"


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        return 0.0
    return 1.0 / decimal_odds


def strip_vig_two_way(price_a: float, price_b: float) -> tuple[float, float] | None:
    if price_a <= 1.0 or price_b <= 1.0:
        return None
    inv_a = 1.0 / price_a
    inv_b = 1.0 / price_b
    total = inv_a + inv_b
    if total <= 0:
        return None
    return inv_a / total, inv_b / total


def strip_vig_three_way(price_home: float, price_draw: float, price_away: float) -> tuple[float, float, float] | None:
    if price_home <= 1.0 or price_away <= 1.0:
        return None
    inv_home = 1.0 / price_home
    inv_draw = 0.0 if price_draw <= 1.0 else 1.0 / price_draw
    inv_away = 1.0 / price_away
    total = inv_home + inv_draw + inv_away
    if total <= 0:
        return None
    return inv_home / total, inv_draw / total, inv_away / total


def weighted_median(values: list[tuple[float, float]]) -> float:
    expanded: list[float] = []
    for value, weight in values:
        expanded.extend([value] * max(1, int(round(weight))))
    if not expanded:
        raise ValueError("weighted_median requires values")
    return float(median(expanded))


def price_distance_pct(price: float, baseline_price: float) -> float | None:
    if price <= 1.0 or baseline_price <= 1.0:
        return None
    return abs(price - baseline_price) * 100.0 / baseline_price


def shrink_probability(model_prob: float, market_prob: float, confidence: float, shrink_min: float, shrink_max: float) -> float:
    shrink = clamp(shrink_min + (confidence / 100.0) * (shrink_max - shrink_min), shrink_min, shrink_max)
    return market_prob + (model_prob - market_prob) * shrink


def poisson_pmf(lmbda: float, k: int) -> float:
    if lmbda < 0 or k < 0:
        return 0.0
    return math.exp(-lmbda) * (lmbda ** k) / math.factorial(k)


def poisson_outcome_model(home_lambda: float, away_lambda: float, max_goals: int = 10) -> dict[str, float]:
    home = 0.0
    draw = 0.0
    away = 0.0
    btts_yes = 0.0
    over25 = 0.0
    for home_goals in range(max_goals + 1):
        p_home = poisson_pmf(home_lambda, home_goals)
        for away_goals in range(max_goals + 1):
            p = p_home * poisson_pmf(away_lambda, away_goals)
            if home_goals > away_goals:
                home += p
            elif home_goals == away_goals:
                draw += p
            else:
                away += p
            if home_goals > 0 and away_goals > 0:
                btts_yes += p
            if home_goals + away_goals >= 3:
                over25 += p
    total = home + draw + away
    if total > 0:
        home /= total
        draw /= total
        away /= total
    return {
        "home": clamp(home, 0.01, 0.98),
        "draw": clamp(draw, 0.01, 0.50),
        "away": clamp(away, 0.01, 0.98),
        "btts_yes": clamp(btts_yes, 0.01, 0.99),
        "over25": clamp(over25, 0.01, 0.99),
    }


def over_probability_from_lambda(total_lambda: float, line: float, max_goals: int = 12) -> float | None:
    if total_lambda <= 0 or not math.isfinite(line):
        return None
    threshold = math.floor(line)
    if abs(line - (threshold + 0.5)) < 1e-9:
        less_or_equal = sum(poisson_pmf(total_lambda, goals) for goals in range(0, threshold + 1))
        return clamp(1.0 - less_or_equal, 0.01, 0.99)
    if abs(line - threshold) < 1e-9:
        less = sum(poisson_pmf(total_lambda, goals) for goals in range(0, threshold))
        push = poisson_pmf(total_lambda, threshold)
        return clamp(1.0 - less - push, 0.01, 0.99)
    if abs(line - (threshold + 0.25)) < 1e-9:
        left = over_probability_from_lambda(total_lambda, threshold, max_goals)
        right = over_probability_from_lambda(total_lambda, threshold + 0.5, max_goals)
        if left is None or right is None:
            return None
        return clamp((left + right) / 2.0, 0.01, 0.99)
    if abs(line - (threshold + 0.75)) < 1e-9:
        left = over_probability_from_lambda(total_lambda, threshold + 0.5, max_goals)
        right = over_probability_from_lambda(total_lambda, threshold + 1.0, max_goals)
        if left is None or right is None:
            return None
        return clamp((left + right) / 2.0, 0.01, 0.99)
    less_or_equal = sum(poisson_pmf(total_lambda, goals) for goals in range(0, math.floor(line) + 1))
    return clamp(1.0 - less_or_equal, 0.01, 0.99)


def home_cover_probability_from_lambdas(home_lambda: float, away_lambda: float, line: float, max_goals: int = 8) -> float | None:
    if not all(math.isfinite(x) for x in [home_lambda, away_lambda, line]):
        return None

    def single_line_prob(single_line: float) -> float:
        win = 0.0
        push = 0.0
        for h in range(max_goals + 1):
            p_home = poisson_pmf(home_lambda, h)
            for a in range(max_goals + 1):
                p = p_home * poisson_pmf(away_lambda, a)
                diff = h - a + single_line
                if diff > 1e-6:
                    win += p
                elif abs(diff) <= 1e-6:
                    push += p
        return win + push * 0.5

    frac = abs(line % 1)
    frac = round(frac, 2)
    if frac == 0.25:
        return clamp((single_line_prob(line - 0.25) + single_line_prob(line + 0.25)) / 2.0, 0.01, 0.99)
    if frac == 0.75:
        return clamp((single_line_prob(line - 0.25) + single_line_prob(line + 0.25)) / 2.0, 0.01, 0.99)
    return clamp(single_line_prob(line), 0.01, 0.99)


def detect_market_family(market_key: str, market_name: str, sport_key: str) -> tuple[str, str] | None:
    key = str(market_key or "").lower()
    name = str(market_name or "").lower()

    is_team_totals = (
        "team_total" in key
        or "teamtotals" in key
        or "home_total" in key
        or "away_total" in key
        or "team total" in name
        or "home total" in name
        or "away total" in name
        or "individual total" in name
        or "total goals home" in name
        or "total goals away" in name
    )
    is_double_chance = (
        "double_chance" in key
        or "doublechance" in key
        or "double chance" in name
        or name in {"1x", "x2", "12"}
    )
    is_dnb = "draw_no_bet" in key or "drawnobet" in key or key == "dnb" or "draw no bet" in name or "dnb" in name
    is_btts = "both_teams_to_score" in key or "btts" in key or "both teams to score" in name or "btts" in name
    is_regulation = (
        "regulation" in key
        or "regular" in key
        or key.endswith("60")
        or "regulation" in name
        or "regular time" in name
        or "60 min" in name
        or "3-way" in name
        or "three way" in name
        or "full time result" in name
    )
    is_moneyline = (
        key == "h2h"
        or "moneyline" in key
        or "money line" in key
        or name in {"ml", "moneyline", "money line", "match winner", "winner", "match result", "1x2"}
        or "to win" in name
        or "match winner incl. overtime" in name
        or "full time result" in name
    )
    is_totals = (
        key == "totals"
        or "total" in key
        or "total" in name
        or "over/under" in name
        or "over under" in name
        or name == "ou"
        or "goals over/under" in name
        or "game total" in name
        or "total points" in name
        or "total runs" in name
        or "match total" in name
        or "goals o/u" in name
    )
    is_spreads = (
        key == "spreads"
        or "spread" in key
        or "handicap" in key
        or "spread" in name
        or "handicap" in name
        or "puck line" in name
        or "run line" in name
        or "point spread" in name
        or "alt spread" in name
        or "alternative handicap" in name
    )

    if is_team_totals:
        return "teamTotals", "team_totals"
    if is_double_chance:
        return "doubleChance", "double_chance"
    if is_dnb:
        return "dnb", "dnb"
    if is_btts:
        return "btts", "btts"
    if is_moneyline:
        if is_regulation:
            return "h2h", "regular_time_3way" if sport_key == "soccer" else "regular_time"
        if sport_key == "icehockey":
            return "h2h", "moneyline_ot"
        return "h2h", "moneyline"
    if is_totals:
        subtype = "asian_totals" if "asian" in key or "asian" in name else "totals"
        return "totals", subtype
    if is_spreads:
        subtype = "asian_spreads" if "asian" in key or "asian" in name else "spreads"
        return "spreads", subtype
    return None


def get_total_selection_key(name: str) -> str | None:
    raw = str(name or "").strip().lower()
    if raw.startswith("over") or raw == "o":
        return "over"
    if raw.startswith("under") or raw == "u":
        return "under"
    return None


def infer_team_total_side(market_name: str, market_key: str, outcome_name: str, home_team: str, away_team: str) -> str | None:
    raw = f"{market_name} {market_key} {outcome_name}".lower()
    if "home" in raw or "team 1" in raw or normalize_text(home_team) in raw:
        return "home"
    if "away" in raw or "team 2" in raw or normalize_text(away_team) in raw:
        return "away"
    return None


def get_outcome_key(name: str, home_team: str, away_team: str) -> str | None:
    raw = str(name or "").strip().lower()
    if raw in {"draw", "x"}:
        return "draw"
    if match_teams_equivalent(name, away_team, home_team, away_team):
        # not used directly, fall through to exact checks below
        pass
    if canonicalize_team_name(name) == canonicalize_team_name(home_team):
        return "home"
    if canonicalize_team_name(name) == canonicalize_team_name(away_team):
        return "away"
    if soft_contains_team(name, home_team):
        return "home"
    if soft_contains_team(name, away_team):
        return "away"
    return None


def get_spread_selection_key(name: str, home_team: str, away_team: str) -> str | None:
    return get_outcome_key(name, home_team, away_team)


def normalize_probability_percent(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if 0.0 <= number <= 1.0:
        return number * 100.0
    return number


def to_decimal_probability(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number > 1.0:
        return number / 100.0
    return number
