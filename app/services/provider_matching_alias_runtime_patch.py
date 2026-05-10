from __future__ import annotations

"""Runtime aliases and strict guards for cross-provider football matching.

This layer targets concrete run-report failures:
- providers return the same fixture under different team spellings;
- fuzzy matching can over-accept unrelated teams with similar text/time.

The patch only changes normalization/matching helpers. It does not relax
publication, EV, xG, market-integrity, or risk guards.
"""

import os
import re
import unicodedata
from typing import Any

PATCH_MARKER = "_harizon_provider_matching_alias_runtime_patch_v2"

SPECIAL_LATIN_TRANSLATION = str.maketrans(
    {
        "ø": "o", "Ø": "O", "ö": "o", "Ö": "O", "ó": "o", "Ó": "O", "ò": "o", "Ò": "O",
        "ô": "o", "Ô": "O", "õ": "o", "Õ": "O", "å": "a", "Å": "A", "ä": "a", "Ä": "A",
        "á": "a", "Á": "A", "à": "a", "À": "A", "â": "a", "Â": "A", "ã": "a", "Ã": "A",
        "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "é": "e", "É": "E", "è": "e", "È": "E",
        "ê": "e", "Ê": "E", "ë": "e", "Ë": "E", "í": "i", "Í": "I", "ì": "i", "Ì": "I",
        "î": "i", "Î": "I", "ï": "i", "Ï": "I", "ú": "u", "Ú": "U", "ù": "u", "Ù": "U",
        "û": "u", "Û": "U", "ü": "u", "Ü": "U", "ý": "y", "Ý": "Y", "ÿ": "y",
        "ç": "c", "Ç": "C", "ñ": "n", "Ñ": "N", "ğ": "g", "Ğ": "G", "ş": "s", "Ş": "S",
        "ı": "i", "İ": "I", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D",
        "þ": "th", "Þ": "TH",
    }
)

TEAM_STOP_WORDS_EXTRA = {
    "ad", "as", "asd", "ca", "cs", "csd", "csm", "csp", "deportes", "deportivo",
    "if", "ilk", "ks", "lkp", "nk", "ofk", "rb", "sk", "sporting", "sv", "tus", "us", "bw",
}

TEAM_ALIASES_EXTRA = {
    "kcb": "kenya commercial bank",
    "kcb bank": "kenya commercial bank",
    "kenya commercial bank": "kenya commercial bank",
    "bandari": "bandari",
    "bandari fc": "bandari",

    "lillestrom": "lillestrom",
    "lillestroem": "lillestrom",
    "lillestrom sk": "lillestrom",
    "lillestroem sk": "lillestrom",
    "lillestrøm": "lillestrom",
    "rosenborg": "rosenborg",
    "rosenborg bk": "rosenborg",

    "wisla plock": "wisla plock",
    "wisła płock": "wisla plock",
    "motor lublin": "motor lublin",
    "lkp motor lublin": "motor lublin",
    "banik ostrava b": "banik ostrava b",
    "usti nad labem": "usti nad labem",
    "usti n labem": "usti nad labem",
    "zamora": "zamora",
    "zamora cf": "zamora",
    "lugo": "lugo",
    "cd lugo": "lugo",

    "ca river plate uru": "river plate montevideo",
    "river plate uru": "river plate montevideo",
    "river plate montevideo": "river plate montevideo",
    "ca river plate": "river plate montevideo",
    "miramar misiones": "miramar misiones",

    "kuala lumpur city": "kuala lumpur city",
    "kuala lumpur city fc": "kuala lumpur city",
    "negeri sembilan": "negeri sembilan",
    "cong an ha noi": "cong an ha noi",
    "cong an ha noi fc": "cong an ha noi",
    "nam dinh": "nam dinh",
    "nam dinh fc": "nam dinh",

    "eintracht hohkeppel": "eintracht hohkeppel",
    "sv eintracht hohkeppel": "eintracht hohkeppel",
    "konigsdorf": "konigsdorf",
    "koenigsdorf": "konigsdorf",
    "tus bw konigsdorf": "konigsdorf",
    "tus bw koenigsdorf": "konigsdorf",

    "atletico de madrid": "atletico madrid",
    "club atletico de madrid": "atletico madrid",
    "athletic bilbao": "athletic club",
    "athletic club bilbao": "athletic club",
    "bayern munich": "bayern munich",
    "fc bayern munich": "bayern munich",
    "inter milan": "internazionale",
    "internazionale milano": "internazionale",
    "man utd": "manchester united",
    "man united": "manchester united",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "ac fiorentina": "fiorentina",
    "fiorentina": "fiorentina",
    "genoa cfc": "genoa",
    "genoa": "genoa",
}

LEAGUE_ALIASES_EXTRA = {
    "uruguay segunda division": "uruguay segunda division",
    "segunda division uruguay": "uruguay segunda division",
    "poland ekstraklasa": "poland ekstraklasa",
    "ekstraklasa poland": "poland ekstraklasa",
    "norway eliteserien": "norway eliteserien",
    "eliteserien norway": "norway eliteserien",
    "malaysia super league": "malaysia super league",
    "vietnam v league 1": "vietnam v league",
    "v league 1 vietnam": "vietnam v league",
}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _prepare_text(value: Any) -> str:
    text = str(value or "").translate(SPECIAL_LATIN_TRANSLATION)
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _norm_simple(value: Any) -> str:
    text = _prepare_text(value).lower().replace("&", " and ")
    text = re.sub(r"\b(?:futbol|football|soccer)\b", " ", text)
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_tokens(value: str, stop_words: set[str]) -> str:
    return " ".join(token for token in _norm_simple(value).split() if token and token not in stop_words).strip()


def _canonical_tokens(value: str) -> set[str]:
    return {token for token in str(value or "").split() if token and not token.isdigit()}


def _has_token_overlap(left: str, right: str) -> bool:
    lt = _canonical_tokens(left)
    rt = _canonical_tokens(right)
    return bool(lt and rt and (lt & rt))


def _strong_side_match(utils: Any, left: str, right: str, score: float) -> bool:
    if score >= 0.90:
        return True
    if score >= 0.78 and _has_token_overlap(utils.canonicalize_team_name(left), utils.canonicalize_team_name(right)):
        return True
    try:
        return bool(utils.soft_contains_team(left, right))
    except Exception:
        return False


def _fuzzy_pair_is_safe(utils: Any, match_home: str, match_away: str, event_home: str, event_away: str, league_related: bool) -> bool:
    direct_home = utils.team_similarity(match_home, event_home)
    direct_away = utils.team_similarity(match_away, event_away)
    reverse_home = utils.team_similarity(match_home, event_away)
    reverse_away = utils.team_similarity(match_away, event_home)

    candidates = [
        (direct_home, direct_away, match_home, event_home, match_away, event_away),
        (reverse_home, reverse_away, match_home, event_away, match_away, event_home),
    ]
    best = max(candidates, key=lambda item: item[0] + item[1])
    a, b, left_a, right_a, left_b, right_b = best
    floor = min(a, b)
    ceiling = max(a, b)

    if _strong_side_match(utils, left_a, right_a, a) and _strong_side_match(utils, left_b, right_b, b):
        return True
    if league_related and floor >= 0.66 and ceiling >= 0.86 and (a + b) >= 1.56:
        return True
    if floor >= 0.74 and ceiling >= 0.88 and (a + b) >= 1.66:
        return True
    return False


def _patch_utils() -> bool:
    import app.utils as utils

    if getattr(utils, PATCH_MARKER, False):
        return False

    original_normalize_text = getattr(utils, "_harizon_original_normalize_text", utils.normalize_text)
    original_canonicalize_team_name = getattr(utils, "_harizon_original_canonicalize_team_name", utils.canonicalize_team_name)
    original_canonicalize_league_name = getattr(utils, "_harizon_original_canonicalize_league_name", utils.canonicalize_league_name)
    original_team_similarity = getattr(utils, "_harizon_original_team_similarity", utils.team_similarity)
    original_score_event_match = getattr(utils, "_harizon_original_score_event_match", utils.score_event_match)

    setattr(utils, "_harizon_original_normalize_text", original_normalize_text)
    setattr(utils, "_harizon_original_canonicalize_team_name", original_canonicalize_team_name)
    setattr(utils, "_harizon_original_canonicalize_league_name", original_canonicalize_league_name)
    setattr(utils, "_harizon_original_team_similarity", original_team_similarity)
    setattr(utils, "_harizon_original_score_event_match", original_score_event_match)

    aliases = getattr(utils, "TEAM_ALIAS_MAP", None)
    if isinstance(aliases, dict):
        aliases.update(TEAM_ALIASES_EXTRA)
    stops = getattr(utils, "TEAM_STOP_WORDS", None)
    if isinstance(stops, set):
        stops.update(TEAM_STOP_WORDS_EXTRA)

    def normalize_text_patched(value: str) -> str:
        return original_normalize_text(_prepare_text(value))

    def canonicalize_team_name_patched(name: str) -> str:
        prepared = _prepare_text(name)
        raw = original_normalize_text(prepared)
        stop_words = getattr(utils, "TEAM_STOP_WORDS", set())
        candidates = [
            raw,
            _norm_simple(prepared),
            _compact_tokens(prepared, stop_words if isinstance(stop_words, set) else set()),
            original_canonicalize_team_name(prepared),
        ]
        alias_map = getattr(utils, "TEAM_ALIAS_MAP", {})
        if isinstance(alias_map, dict):
            for item in candidates:
                if item in alias_map:
                    return str(alias_map[item])
        for item in candidates:
            simplified = item.replace("oe", "o").replace("ae", "a").replace("ue", "u")
            if isinstance(alias_map, dict) and simplified in alias_map:
                return str(alias_map[simplified])
            if simplified:
                return simplified
        return original_canonicalize_team_name(prepared)

    def canonicalize_league_name_patched(name: str) -> str:
        value = original_canonicalize_league_name(_prepare_text(name))
        compact = _norm_simple(value)
        return LEAGUE_ALIASES_EXTRA.get(compact, LEAGUE_ALIASES_EXTRA.get(value, value))

    def team_similarity_patched(a: str, b: str) -> float:
        ca = canonicalize_team_name_patched(a)
        cb = canonicalize_team_name_patched(b)
        if ca and cb:
            if ca == cb:
                return 1.0
            if ca in cb or cb in ca:
                shorter = min(len(ca), len(cb))
                if shorter >= 4:
                    return max(0.96, original_team_similarity(a, b))
        return original_team_similarity(_prepare_text(a), _prepare_text(b))

    def score_event_match_patched(**kwargs: Any) -> tuple[float, str | None]:
        score, quality = original_score_event_match(**kwargs)
        if quality != "fuzzy" or score <= 0:
            return score, quality
        league_score = utils.league_similarity(str(kwargs.get("match_league") or ""), str(kwargs.get("event_league") or ""))
        league_related = league_score >= 0.52
        safe = _fuzzy_pair_is_safe(
            utils,
            str(kwargs.get("match_home") or ""),
            str(kwargs.get("match_away") or ""),
            str(kwargs.get("event_home") or ""),
            str(kwargs.get("event_away") or ""),
            league_related,
        )
        if not safe:
            return 0.0, None
        # Penalize fuzzy matches without a related league so mediocre cross-league
        # text similarities cannot beat exact candidates in another competition.
        if not league_related:
            score -= 12.0
            if score < 62.0:
                return 0.0, None
        return score, quality

    utils.normalize_text = normalize_text_patched
    utils.canonicalize_team_name = canonicalize_team_name_patched
    utils.canonicalize_league_name = canonicalize_league_name_patched
    utils.team_similarity = team_similarity_patched
    utils.score_event_match = score_event_match_patched
    setattr(utils, PATCH_MARKER, True)
    return True


def _match_source_ids(match: Any, source_name: str) -> set[str]:
    ids: set[str] = set()
    source = str(getattr(match, "source", "") or "").strip().lower()
    source_event_id = str(getattr(match, "source_event_id", "") or "").strip()
    if source == source_name and source_event_id:
        ids.add(source_event_id)
    meta = getattr(match, "metadata", {}) or {}
    if isinstance(meta, dict):
        for key in (f"{source_name}_id", f"{source_name}_event_id", "fixture_id", "game_id"):
            value = str(meta.get(key) or "").strip()
            if value:
                ids.add(value)
        for raw in (meta.get("source_ids"), meta.get("provider_source_ids")):
            if isinstance(raw, dict):
                value = str(raw.get(source_name) or raw.get(source_name.replace("_", "-")) or "").strip()
                if value:
                    ids.add(value)
    return {item for item in ids if item}


def _patch_sportlogic_source_id_matching() -> bool:
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, f"{PATCH_MARKER}_sportlogic", False):
        return False
    original = getattr(cls, "_match_fixtures", None)
    if not callable(original):
        return False

    def match_fixtures_patched(self: Any, matches: list[Any], fixtures: list[dict[str, Any]], stats: dict[str, Any]):
        mapping = original(self, matches, fixtures, stats)
        if not _truthy(os.getenv("SPORTLOGIC_SOURCE_ID_MATCHING_ENABLED"), True):
            return mapping
        rows_by_id: dict[str, dict[str, Any]] = {}
        for row in fixtures or []:
            try:
                event_id = str(self._event_id(row) or "").strip()
            except Exception:
                event_id = ""
            if event_id:
                rows_by_id[event_id] = row
        added = 0
        for match in matches or []:
            key = getattr(match, "match_key", "")
            if not key or key in mapping:
                continue
            ids = _match_source_ids(match, "sportlogic")
            row = next((rows_by_id[item] for item in ids if item in rows_by_id), None)
            if row is None:
                continue
            try:
                event_id = str(self._event_id(row) or "").strip()
            except Exception:
                event_id = ""
            mapping[key] = {"match": match, "row": row, "event_id": event_id, "score": 120.0, "quality": "source_id"}
            added += 1
        if added:
            stats["matched_source_id"] = int(stats.get("matched_source_id") or 0) + added
            stats["matched_exact"] = int(stats.get("matched_exact") or 0) + added
        return mapping

    cls._match_fixtures = match_fixtures_patched
    setattr(cls, f"{PATCH_MARKER}_sportlogic", True)
    return True


def install() -> bool:
    if not _truthy(os.getenv("PROVIDER_MATCHING_ALIAS_PATCH_ENABLED"), True):
        return False
    changed = False
    try:
        changed = _patch_utils() or changed
    except Exception:
        pass
    try:
        changed = _patch_sportlogic_source_id_matching() or changed
    except Exception:
        pass
    return changed
