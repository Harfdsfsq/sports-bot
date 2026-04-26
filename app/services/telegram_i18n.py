from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ALIAS_PATH = Path("config/telegram_i18n_aliases.json")

_BUILTIN_TEAMS: dict[str, str] = {
    # Italy
    "AC Milan": "Милан",
    "Milan": "Милан",
    "Juventus Turin": "Ювентус",
    "Juventus": "Ювентус",
    "Inter Milan": "Интер",
    "Internazionale": "Интер",
    "AS Roma": "Рома",
    "Roma": "Рома",
    "Napoli": "Наполи",
    "Lazio": "Лацио",
    "Atalanta": "Аталанта",
    "Fiorentina": "Фиорентина",
    "Torino": "Торино",
    "Bologna": "Болонья",
    "Genoa CFC": "Дженоа",
    "Genoa": "Дженоа",
    "Pisa SC": "Пиза",

    # Spain
    "Real Madrid": "Реал Мадрид",
    "Barcelona": "Барселона",
    "FC Barcelona": "Барселона",
    "Atletico Madrid": "Атлетико Мадрид",
    "Atlético Madrid": "Атлетико Мадрид",
    "Sevilla FC": "Севилья",
    "Sevilla": "Севилья",
    "CA Osasuna": "Осасуна",
    "Osasuna": "Осасуна",
    "CD Leganes": "Леганес",
    "CD Leganés": "Леганес",
    "Leganes": "Леганес",
    "Leganés": "Леганес",
    "FC Andorra": "Андорра",
    "Andorra": "Андорра",
    "Valencia": "Валенсия",
    "Villarreal": "Вильярреал",
    "Real Sociedad": "Реал Сосьедад",
    "Athletic Bilbao": "Атлетик Бильбао",
    "Real Betis": "Бетис",
    "Levante UD": "Леванте",
    "Levante": "Леванте",

    # England / France / Germany
    "Manchester City": "Манчестер Сити",
    "Manchester United": "Манчестер Юнайтед",
    "Liverpool": "Ливерпуль",
    "Chelsea": "Челси",
    "Arsenal": "Арсенал",
    "Tottenham": "Тоттенхэм",
    "Paris Saint-Germain": "Пари Сен-Жермен",
    "PSG": "ПСЖ",
    "Olympique Lyon": "Лион",
    "Lyon": "Лион",
    "Marseille": "Марсель",
    "Olympique Marseille": "Марсель",
    "Bayern Munich": "Бавария",
    "Bayern München": "Бавария",
    "Borussia Dortmund": "Боруссия Дортмунд",
    "Bayer Leverkusen": "Байер Леверкузен",
    "RB Leipzig": "РБ Лейпциг",
    "VfB Stuttgart": "Штутгарт",
    "SC Freiburg": "Фрайбург",

    # USA / Americas
    "Los Angeles Galaxy": "Лос-Анджелес Гэлакси",
    "LA Galaxy": "Лос-Анджелес Гэлакси",
    "Real Salt Lake": "Реал Солт-Лейк",
    "Los Angeles FC": "Лос-Анджелес",
    "San Jose Earthquakes": "Сан-Хосе Эртквейкс",
    "New York City FC": "Нью-Йорк Сити",
    "New York City": "Нью-Йорк Сити",
    "FC Cincinnati": "Цинциннати",
    "Portmore United": "Портмор Юнайтед",

    # Other seen teams
    "FBC Melgar": "Мельгар",
    "Universitario de Deportes": "Университарио",
    "Operario FC MS": "Операрио MS",
    "Abecat Ouvidorense GO": "Абекат Овидоренсе",
    "Figueirense FC SC": "Фигейренсе",
    "Botafogo FC PB": "Ботафого PB",
    "Christchurch United": "Крайстчерч Юнайтед",
    "Northern AFC": "Нортерн",
}

_BUILTIN_TEAMS.update({
    "Dunedin City Royals FC": "Данидин Сити Ройалс",
    "Ferrymead Bays": "Ферримид Бэйс",
    "FK Haugesund 2": "Хаугесунд 2",
    "Stabaek Fotball 2": "Стабек 2",
    "Stabæk Fotball 2": "Стабек 2",
    "Union Espanola": "Унион Эспаньола",
    "CD Santa Cruz": "Санта-Крус",
    "Barranquilla FC": "Барранкилья",
    "Real Cartagena FC": "Реал Картахена",
    "Bali United": "Бали Юнайтед",
    "PSM Makassar": "ПСМ Макассар",
    "Дунедин Кити Роиалс ФК": "Данидин Сити Ройалс",
    "Ферримеад Баис": "Ферримид Бэйс",
    "ФК Хаугесунд 2": "Хаугесунд 2",
    "Стабаек Фотбалл 2": "Стабек 2",
    "Унион Еспанола": "Унион Эспаньола",
    "КД Санта Круз": "Санта-Крус",
    "Барранкуилла ФК": "Барранкилья",
    "Реал Картагена ФК": "Реал Картахена",
})

_BUILTIN_LEAGUES: dict[str, str] = {
    "Italy - Serie A": "Италия - Серия A",
    "Italy - Serie B": "Италия - Серия B",
    "Spain - LaLiga": "Испания - Ла Лига",
    "Spain - LaLiga 2": "Испания - Ла Лига 2",
    "Spain - Segunda Division": "Испания - Сегунда",
    "England - Premier League": "Англия - Премьер-лига",
    "England - Championship": "Англия - Чемпионшип",
    "France - Ligue 1": "Франция - Лига 1",
    "France - Ligue 2": "Франция - Лига 2",
    "Germany - Bundesliga": "Германия - Бундеслига",
    "Germany - 2. Bundesliga": "Германия - Вторая Бундеслига",
    "Germany - 3. Liga": "Германия - Третья лига",
    "Netherlands - Eredivisie": "Нидерланды - Эредивизи",
    "USA - MLS": "США - MLS",
    "Brazil - Serie A": "Бразилия - Серия A",
    "Brazil - Serie B": "Бразилия - Серия B",
    "Peru - Liga 1": "Перу - Лига 1",
    "France - National": "Франция - Насьональ",
    "Australia - A-League": "Австралия - A-Лига",
}

_COUNTRIES: dict[str, str] = {
    "Italy": "Италия",
    "Spain": "Испания",
    "England": "Англия",
    "France": "Франция",
    "Germany": "Германия",
    "Netherlands": "Нидерланды",
    "Portugal": "Португалия",
    "Brazil": "Бразилия",
    "Argentina": "Аргентина",
    "Peru": "Перу",
    "Chile": "Чили",
    "Colombia": "Колумбия",
    "USA": "США",
    "United States": "США",
    "Australia": "Австралия",
    "Japan": "Япония",
    "South Korea": "Южная Корея",
    "Saudi Arabia": "Саудовская Аравия",
    "United Arab Emirates": "ОАЭ",
    "UAE": "ОАЭ",
    "Egypt": "Египет",
    "Turkey": "Турция",
    "Belgium": "Бельгия",
    "Austria": "Австрия",
    "Switzerland": "Швейцария",
    "Denmark": "Дания",
    "Poland": "Польша",
    "Ukraine": "Украина",
}

_LEAGUE_WORDS: dict[str, str] = {
    "Premier League": "Премьер-лига",
    "Pro League": "Про-лига",
    "Professional League": "Профессиональная лига",
    "Championship": "Чемпионшип",
    "League One": "Лига 1",
    "League Two": "Лига 2",
    "First Division": "Первый дивизион",
    "Second Division": "Второй дивизион",
    "Third Division": "Третий дивизион",
    "Serie A": "Серия A",
    "Serie B": "Серия B",
    "LaLiga": "Ла Лига",
    "La Liga": "Ла Лига",
    "Segunda Division": "Сегунда",
    "Bundesliga": "Бундеслига",
    "2. Bundesliga": "Вторая Бундеслига",
    "3. Liga": "Третья лига",
    "Ligue 1": "Лига 1",
    "Ligue 2": "Лига 2",
    "Eredivisie": "Эредивизи",
    "Cup": "Кубок",
    "Super Cup": "Суперкубок",
    "League Cup": "Кубок лиги",
    "National": "Насьональ",
    "MLS": "MLS",
    "A-League": "A-Лига",
}

_REASON_MAP: dict[str, str] = {
    "canonical_negative_value": "отрицательная контрольная ценность после пересчёта по выбранному коэффициенту",
    "match_time_outside_window": "до начала матча меньше разрешённого запаса времени",
    "match_already_started": "матч уже начался",
    "match_time_too_late": "матч дальше окна публикации",
    "missing_commence_time": "нет времени начала матча",
    "xg_direction_conflict": "направление ставки конфликтует с xG",
    "xg_probability_gap_hard_reject": "слишком большой разрыв между моделью и xG-ориентиром",
    "btts_direction_conflict": "BTTS конфликтует с xG по обеим командам",
    "btts_probability_gap_hard_reject": "слишком большой разрыв BTTS-модели и xG",
    "dnb_direction_conflict": "DNB конфликтует с xG-проверкой",
    "tier_a_quality_below_min": "качество ниже минимума уровня A",
    "tier_b_quality_below_min": "качество ниже минимума уровня B",
    "tier_c_quality_below_min": "качество ниже минимума уровня C",
    "tier_a_confidence_below_min": "уверенность ниже минимума уровня A",
    "tier_b_confidence_below_min": "уверенность ниже минимума уровня B",
    "tier_c_confidence_below_min": "уверенность ниже минимума уровня C",
    "tier_a_canonical_edge_below_min": "запас value ниже минимума уровня A",
    "tier_b_canonical_edge_below_min": "запас value ниже минимума уровня B",
    "tier_c_canonical_edge_below_min": "запас value ниже минимума уровня C",
    "tier_a_canonical_ev_below_min": "EV ниже минимума уровня A",
    "tier_b_canonical_ev_below_min": "EV ниже минимума уровня B",
    "tier_c_canonical_ev_below_min": "EV ниже минимума уровня C",
    "tier_a_proxy_quality_not_allowed": "уровень A не принимает proxy-качество",
    "tier_b_proxy_quality_not_allowed": "уровень B не принимает proxy-качество",
    "tier_c_proxy_quality_not_allowed": "уровень C не принимает proxy-качество",
    "proxy_single_source_confidence_below_min": "proxy/single-source: уверенность ниже строгого минимума",
    "proxy_single_source_edge_below_min": "proxy/single-source: value-запас ниже строгого минимума",
    "proxy_single_source_ev_below_min": "proxy/single-source: EV ниже строгого минимума",
    "family_not_allowed:spreads": "семья рынка закрыта для Telegram: форы",
    "family_not_allowed:h2h": "семья рынка закрыта для Telegram: исходы 1X2",
    "family_not_allowed:teamtotals": "семья рынка закрыта для Telegram: индивидуальные тоталы",
    "family_not_allowed:btts": "семья рынка закрыта для Telegram: обе забьют",
    "h2h_rescue_odds_too_high": "исход 1X2 с коэффициентом выше безопасного лимита",
    "duplicate_fallback_sent_index": "такой прогноз уже отправлялся ранее",
    "odds_below_global_min": "коэффициент ниже общего минимума",
    "odds_above_global_max": "коэффициент выше общего максимума",
    "missing_books": "нет подтверждения линией букмекера",
    "missing_sources": "нет подтверждения источниками",
}


_REASON_MAP.update({
    "tier_a_books_below_min": "линий букмекеров меньше минимума уровня A",
    "tier_b_books_below_min": "линий букмекеров меньше минимума уровня B",
    "tier_c_books_below_min": "линий букмекеров меньше минимума уровня C",
    "tier_a_sources_below_min": "источников меньше минимума уровня A",
    "tier_b_sources_below_min": "источников меньше минимума уровня B",
    "tier_c_sources_below_min": "источников меньше минимума уровня C",
    "tier_a_xg_gap_above_max": "уровень A: разрыв с xG выше лимита",
    "tier_b_xg_gap_above_max": "уровень B: разрыв с xG выше лимита",
    "tier_c_xg_gap_above_max": "уровень C: разрыв с xG выше лимита",
    "tier_a_xg_confirmation_missing": "уровень A: нет подтверждения xG",
    "tier_b_xg_confirmation_missing": "уровень B: нет подтверждения xG",
    "tier_c_xg_confirmation_missing": "уровень C: нет подтверждения xG",
    "tier_a_market_confirmation_missing": "уровень A: нет рыночного подтверждения",
    "tier_b_market_confirmation_missing": "уровень B: нет рыночного подтверждения",
    "tier_c_market_confirmation_missing": "уровень C: нет рыночного подтверждения",
    "tier_b_canonical_edge_below_min": "запас value ниже минимума уровня B",
    "tier_b_canonical_ev_below_min": "EV ниже минимума уровня B",
    "tier_c_canonical_edge_below_min": "запас value ниже минимума уровня C",
    "tier_c_canonical_ev_below_min": "EV ниже минимума уровня C",
})


_REASON_MAP.update({
    "tier_a_odds_above_max": "уровень A: коэффициент выше безопасного максимума",
    "tier_b_odds_above_max": "уровень B: коэффициент выше безопасного максимума",
    "tier_c_odds_above_max": "уровень C: коэффициент выше безопасного максимума",
    "tier_a_odds_below_min": "уровень A: коэффициент ниже минимума",
    "tier_b_odds_below_min": "уровень B: коэффициент ниже минимума",
    "tier_c_odds_below_min": "уровень C: коэффициент ниже минимума",
})

_TEXT_REPLACEMENTS: list[tuple[str, str]] = [
    ("Over", "Больше"),
    ("Under", "Меньше"),
    ("Draw No Bet", "Фора 0"),
    ("DNB", "Фора 0"),
    ("Both Teams To Score", "Обе забьют"),
    ("BTTS", "Обе забьют"),
    ("Yes", "Да"),
    ("No", "Нет"),
    ("Home", "Хозяева"),
    ("Away", "Гости"),
    ("Total", "Тотал"),
    ("Spread", "Фора"),
    ("Moneyline", "Исход"),
    ("Match Winner", "Победитель матча"),
    ("Team Total", "Индивидуальный тотал"),
]

_TRANSLIT = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "у", "x": "кс",
    "y": "и", "z": "з",
}

_DIGRAPHS = {
    "sch": "ш", "sh": "ш", "ch": "ч", "th": "т", "ph": "ф", "kh": "х", "zh": "ж",
    "ts": "ц", "tz": "ц", "ck": "к", "qu": "кв", "oo": "у", "ee": "и", "ea": "и",
    "ou": "у", "ai": "ай", "ei": "ей", "ay": "ей", "ey": "ей",
}


def _clean_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


@lru_cache(maxsize=1)
def _external_aliases() -> dict[str, dict[str, str]]:
    if not ALIAS_PATH.exists():
        return {"teams": {}, "leagues": {}, "countries": {}, "words": {}}
    try:
        payload = json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"teams": {}, "leagues": {}, "countries": {}, "words": {}}
    if not isinstance(payload, dict):
        return {"teams": {}, "leagues": {}, "countries": {}, "words": {}}
    out: dict[str, dict[str, str]] = {}
    for section in ("teams", "leagues", "countries", "words"):
        raw = payload.get(section) or {}
        out[section] = {str(k): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()} if isinstance(raw, dict) else {}
    return out


def _lookup(value: Any, table: dict[str, str], external_section: str) -> str | None:
    text = _clean_key(value)
    if not text:
        return ""
    external = _external_aliases().get(external_section, {})
    for source in (external, table):
        if text in source:
            return source[text]
        low = text.lower()
        for k, v in source.items():
            if k.lower() == low:
                return v
    return None


def _transliterate_word(word: str) -> str:
    original = word
    lower = word.lower()
    if not re.search(r"[A-Za-z]", word):
        return word

    # Preserve common abbreviations.
    if original.isupper() and len(original) <= 4:
        return original

    result = ""
    i = 0
    while i < len(lower):
        matched = False
        for size in (3, 2):
            part = lower[i:i + size]
            if part in _DIGRAPHS:
                result += _DIGRAPHS[part]
                i += size
                matched = True
                break
        if matched:
            continue
        ch = lower[i]
        result += _TRANSLIT.get(ch, ch)
        i += 1

    # Capitalize first Cyrillic letter if source looked capitalized.
    if original[:1].isupper() and result:
        result = result[:1].upper() + result[1:]
    return result


def transliterate_unknown_name(value: Any) -> str:
    text = _clean_key(value)
    if not text:
        return ""
    # Split but preserve punctuation/hyphens/spaces.
    parts = re.split(r"(\s+|-|/|,|\(|\)|\.|')", text)
    return "".join(_transliterate_word(part) for part in parts)


def translate_team_name(name: Any) -> str:
    text = _clean_key(name)
    if not text:
        return ""
    exact = _lookup(text, _BUILTIN_TEAMS, "teams")
    if exact is not None:
        return exact

    # Remove common club suffixes before fallback, but keep if name is only abbreviation.
    cleaned = re.sub(r"\b(FC|CF|SC|AFC|CFC|CD|CA|UD|AC|AS|FK|PFC|SAD)\b\.?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    exact_clean = _lookup(cleaned, _BUILTIN_TEAMS, "teams") if cleaned else None
    if exact_clean:
        return exact_clean
    return transliterate_unknown_name(cleaned or text)


def translate_league_name(name: Any) -> str:
    text = _clean_key(name)
    if not text:
        return ""
    exact = _lookup(text, _BUILTIN_LEAGUES, "leagues")
    if exact is not None:
        return exact

    if " - " in text:
        country, league = text.split(" - ", 1)
        country_ru = _lookup(country, _COUNTRIES, "countries") or transliterate_unknown_name(country)
        league_ru = _lookup(league, _LEAGUE_WORDS, "words") or _translate_league_words(league)
        return f"{country_ru} - {league_ru}"

    return _translate_league_words(text)


def _translate_league_words(text: str) -> str:
    result = text
    external_words = _external_aliases().get("words", {})
    for source in (external_words, _LEAGUE_WORDS):
        for k, v in sorted(source.items(), key=lambda item: len(item[0]), reverse=True):
            result = re.sub(rf"\b{re.escape(k)}\b", v, result, flags=re.IGNORECASE)
    if re.search(r"[A-Za-z]", result):
        result = transliterate_unknown_name(result)
    return result


def translate_reject_reason(reason: Any) -> str:
    text = _clean_key(reason)
    if not text:
        return ""
    if text in _REASON_MAP:
        return _REASON_MAP[text]
    if text.startswith("family_not_allowed:"):
        family = text.split(":", 1)[1]
        family_ru = {
            "spreads": "форы",
            "h2h": "исходы 1X2",
            "teamtotals": "индивидуальные тоталы",
            "btts": "обе забьют",
            "totals": "тоталы",
            "dnb": "фора 0",
        }.get(family, family)
        return f"семья рынка закрыта для Telegram: {family_ru}"
    # Generic readable fallback for tier/family slugs not yet in the dictionary.
    generic = text
    generic = re.sub(r"^tier_([abc])_", lambda m: f"уровень {m.group(1).upper()}: ", generic)
    generic = generic.replace("odds above max", "коэффициент выше безопасного максимума")
    generic = generic.replace("odds below min", "коэффициент ниже минимума")
    generic = generic.replace("books below min", "линий букмекеров меньше минимума")
    generic = generic.replace("sources below min", "источников меньше минимума")
    generic = generic.replace("xg gap above max", "разрыв с xG выше лимита")
    generic = generic.replace("xg confirmation missing", "нет подтверждения xG")
    generic = generic.replace("market confirmation missing", "нет рыночного подтверждения")
    return generic.replace("_", " ")


def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str:
    text = _clean_key(selection)
    if not text:
        return ""
    home_raw = _clean_key(home_team)
    away_raw = _clean_key(away_team)
    if home_raw:
        text = re.sub(re.escape(home_raw), translate_team_name(home_raw), text, flags=re.IGNORECASE)
    if away_raw:
        text = re.sub(re.escape(away_raw), translate_team_name(away_raw), text, flags=re.IGNORECASE)

    for source, target in sorted(_TEXT_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"\b{re.escape(source)}\b", target, text, flags=re.IGNORECASE)

    # Common compact market patterns.
    text = re.sub(r"\bO\s*([0-9]+(?:\.[0-9]+)?)\b", r"Больше \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bU\s*([0-9]+(?:\.[0-9]+)?)\b", r"Меньше \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOver\s*\(?([0-9]+(?:\.[0-9]+)?)\)?", r"Больше \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bUnder\s*\(?([0-9]+(?:\.[0-9]+)?)\)?", r"Меньше \1", text, flags=re.IGNORECASE)
    return normalize_telegram_text(text)


def normalize_telegram_text(text: Any) -> str:
    value = str(text or "")
    if not value:
        return ""

    # Replace exact known team/league names anywhere in message.
    replacements: dict[str, str] = {}
    replacements.update(_external_aliases().get("teams", {}))
    replacements.update(_external_aliases().get("leagues", {}))
    replacements.update(_BUILTIN_TEAMS)
    replacements.update(_BUILTIN_LEAGUES)

    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if source and target:
            value = re.sub(re.escape(source), target, value, flags=re.IGNORECASE)

    for source, target in sorted(_TEXT_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
        value = re.sub(rf"\b{re.escape(source)}\b", target, value, flags=re.IGNORECASE)

    # Translate reason slugs if they appear literally.
    for source, target in sorted(_REASON_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(source, target)

    return re.sub(r"[ \t]+", " ", value).strip()
