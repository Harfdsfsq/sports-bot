from __future__ import annotations

"""Runtime aliases for cross-provider football matching.

This layer targets the concrete failure mode visible in run reports: providers
return the same fixture under different team spellings, abbreviations, or local
characters, so odds/context rows do not attach to the inventory match.

The patch is intentionally narrow:
- it only changes normalization/matching helpers;
- it does not relax publication, EV, xG, or market-integrity guards;
- it records SportLogic source-id matches when the day inventory already knows
  the provider fixture id.
"""

import os
import re
import unicodedata
from typing import Any

PATCH_MARKER = "_harizon_provider_matching_alias_runtime_patch_v1"

SPECIAL_LATIN_TRANSLATION = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ö": "o",
        "Ö": "O",
        "ó": "o",
        "Ó": "O",
        "ò": "o",
        "Ò": "O",
        "ô": "o",
        "Ô": "O",
        "õ": "o",
        "Õ": "O",
        "å": "a",
        "Å": "A",
        "ä": "a",
        "Ä": "A",
        "á": "a",
        "Á": "A",
        "à": "a",
        "À": "A",
        "â": "a",
        "Â": "A",
        "ã": "a",
        "Ã": "A",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "é": "e",
        "É": "E",
        "è": "e",
        "È": "E",
        "ê": "e",
        "Ê": "E",
        "ë": "e",
        "Ë": "E",
        "í": "i",
        "Í": "I",
        "ì": "i",
        "Ì": "I",
        "î": "i",
        "Î": "I",
        "ï": "i",
        "Ï": "I",
        "ú": "u",
        "Ú": "U",
        "ù": "u",
        "Ù": "U",
        "û": "u",
        "Û": "U",
        "ü": "u",
        "Ü": "U",
        "ý": "y",
        "Ý": "Y",
        "ÿ": "y",
        "ç": "c",
        "Ç": "C",
        "ñ": "n",
        "Ñ": "N",
        "ğ": "g",
        "Ğ": "G",
        "ş": "s",
        "Ş": "S",
        "ı": "i",
        "İ": "I",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
    }
)

TEAM_STOP_WORDS_EXTRA = {
    "ad", "as", "asd", "ca", "cs", "csd", "csm", "csp", "deportes", "deportivo",
    "if", "ilk", "ks", "lkp", "nk", "ofk", "rb", "sk", "sporting", "sv", "tus", "us", "bw",
}

TEAM_ALIASES_EXTRA = {
    # Kenya / aliases seen in the latest run priority list.
    "kcb": "kenya commercial bank",
    "kcb bank": "kenya commercial bank",
    "kenya commercial bank": "kenya commercial bank",
    "kenya commercial bank bandari": "kenya commercial bank bandari",
    "bandari": "bandari",
    "bandari fc": "bandari",

    # Norway: providers alternate o/oe/ø and keep/remove legal suffixes.
    "lillestrom": "lillestrom",
    "lillestroem": "lillestrom",
    "lillestrom sk": "lillestrom",
    "lillestroem sk": "lillestrom",
    "lillestrøm": "lillestrom",
    "rosenborg": "rosenborg",
    "rosenborg bk": "rosenborg",

    # Poland / Central Europe.
    "wisla plock": "wisla plock",
    "wisła płock": "wisla plock",
    "motor lublin": "motor lublin",
    "lkp motor lublin": "motor lublin",
    "banik ostrava b": "banik ostrava b",
    "usti nad labem": "usti nad labem",
    "usti n labem": "usti nad labem",
    "usti nad labem b": "usti nad labem b",
    "zamora": "zamora",
    "zamora cf": "zamora",
    "lugo": "lugo",
    "cd lugo": "lugo",

    # Uruguay / LATAM.
    "ca river plate uru": "river plate montevideo",
    "river plate uru": "river plate montevideo",
    "river plate montevideo": "river plate montevideo",
    "ca river plate": "river plate montevideo",
    "miramar misiones": "miramar misiones",

    # Malaysia / Vietnam.
    "kuala lumpur city": "kuala lumpur city",
    "kuala lumpur city fc": "kuala lumpur city",
    "negeri sembilan": "negeri sembilan",
    "cong an ha noi": "cong an ha noi",
    "cong an ha noi fc": "cong an ha noi",
    "nam dinh": "nam dinh",
    "nam dinh fc": "nam dinh",

    # Germany lower leagues seen in priority rows.
    "eintracht hohkeppel": "eintracht hohkeppel",
    "sv eintracht hohkeppel": "eintracht hohkeppel",
    "konigsdorf": "konigsdorf",
    "koenigsdorf": "konigsdorf",
    "tus bw konigsdorf": "konigsdorf",
    "tus bw koenigsdorf": "konigsdorf",

    # Generic high-value European aliases from previous reports.
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


def _patch_utils() -> bool:
    import app.utils as utils

    if getattr(utils, PATCH_MARKER, False):
        return False

    original_normalize_text = utils.normalize_text
    original_canonicalize_team_name = utils.canonicalize_team_name
    original_canonicalize_league_name = utils.canonicalize_league_name
    original_team_similarity = utils.team_similarity

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
        # Handle Scandinavian oe/o and German oe/o drift after the main aliases.
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

    utils.normalize_text = normalize_text_patched
    utils.canonicalize_team_name = canonicalize_team_name_patched
    utils.canonicalize_league_name = canonicalize_league_name_patched
    utils.team_similarity = team_similarity_patched
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
            mapping[key] = {
                "match": match,
                "row": row,
                "event_id": event_id,
                "score": 120.0,
                "quality": "source_id",
            }
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
