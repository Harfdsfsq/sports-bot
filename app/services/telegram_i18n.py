from __future__ import annotations

import re
from typing import Any

# Centralized Telegram text localization helpers.
# The bot keeps raw provider/team names in data, but outgoing Telegram text should be readable in Russian.

TEAM_ALIASES: dict[str, str] = {
    "New York City FC": "Нью-Йорк Сити",
    "New York City": "Нью-Йорк Сити",
    "FC Cincinnati": "Цинциннати",
    "Cincinnati": "Цинциннати",
    "Kolos Kovalivka": "Колос Ковалёвка",
    "SC Poltava": "Полтава",
    "Brisbane Strikers FC": "Брисбен Страйкерс",
    "St George Willawong FC": "Сент-Джордж Уиллавонг",
    "National Bank of Egypt SC": "Нэшнл Банк оф Иджипт",
    "Zed FC": "Зед",
    "Levante UD": "Леванте",
    "Sevilla FC": "Севилья",
    "PSV Eindhoven": "ПСВ Эйндховен",
    "PEC Zwolle": "Зволле",
    "VfB Stuttgart": "Штутгарт",
    "SC Freiburg": "Фрайбург",
    "FC Pyunik Yerevan": "Пюник",
    "FC Urartu Yerevan": "Урарту",
    "Al Mokawloon Al Arab": "Аль-Мокавлун",
    "Al Ittihad Al Sakandary": "Аль-Иттихад Александрия",
    "Capalaba Bulldogs": "Капалаба Буллдогс",
    "North Star FC": "Норт Стар",
    "Brunswick City SC": "Брансуик Сити",
    "Langwarrin SC": "Лангваррин",
    "Brunswick Juventus FC": "Брансуик Ювентус",
    "North Sunshine Eagles FC": "Норт Саншайн Иглз",
    "Springvale White Eagles": "Спрингвейл Уайт Иглз",
    "Kingston City FC": "Кингстон Сити",
    "Spanish Town Police FC": "Спэниш Таун Полис",
    "Portmore United": "Портмор Юнайтед",
}

LEAGUE_ALIASES: dict[str, str] = {
    "USA - MLS": "США - MLS",
    "Ukraine - Premier League": "Украина - Премьер-лига",
    "Australia - Queensland Premier League 1": "Австралия - Премьер-лига Квинсленда 1",
    "Australia - Victoria Premier League 1": "Австралия - Премьер-лига Виктории 1",
    "Australia - Victoria Premier League 2": "Австралия - Премьер-лига Виктории 2",
    "Egypt - Premier League": "Египет - Премьер-лига",
    "Spain - LaLiga": "Испания - Ла Лига",
    "Netherlands - Eredivisie": "Нидерланды - Эредивизи",
    "Germany - DFB Pokal": "Германия - Кубок DFB",
    "Armenia - Premier League": "Армения - Премьер-лига",
}

WORD_ALIASES: dict[str, str] = {
    "new": "Нью",
    "york": "Йорк",
    "city": "Сити",
    "united": "Юнайтед",
    "fc": "",
    "f.c.": "",
    "sc": "",
    "s.c.": "",
    "cf": "",
    "ud": "",
    "afc": "",
    "club": "Клуб",
    "athletic": "Атлетик",
    "real": "Реал",
    "deportivo": "Депортиво",
    "sporting": "Спортинг",
    "central": "Сентрал",
    "north": "Норт",
    "south": "Саут",
    "east": "Ист",
    "west": "Вест",
    "bank": "Банк",
    "national": "Нэшнл",
    "egypt": "Иджипт",
    "police": "Полис",
    "town": "Таун",
    "eagles": "Иглз",
    "white": "Уайт",
    "bulldogs": "Буллдогс",
    "strikers": "Страйкерс",
    "star": "Стар",
    "juventus": "Ювентус",
    "sunshine": "Саншайн",
}

# Simple fallback transliteration. It is intentionally conservative:
# known aliases are preferred; unknown names remain recognizable, not "machine-translated" beyond repair.
_MULTI = {
    "sh": "ш", "ch": "ч", "zh": "ж", "ya": "я", "yu": "ю", "yo": "ё", "ye": "е", "kh": "х", "ts": "ц",
}
_SINGLE = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
    "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
    "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з",
}


def _squash_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _strip_club_suffixes(text: str) -> str:
    text = _squash_spaces(text)
    text = re.sub(r"^(FC|SC|CF|AC|AFC|UD)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(FC|SC|CF|AC|AFC|UD|F\.C\.|S\.C\.)$", "", text, flags=re.IGNORECASE)
    return _squash_spaces(text)


def transliterate_word(word: str) -> str:
    src = str(word or "")
    low = src.lower().strip()
    if not low:
        return ""
    if low in WORD_ALIASES:
        return WORD_ALIASES[low]
    if re.fullmatch(r"[A-ZА-ЯЁ0-9]+", src) and len(src) <= 5:
        return src

    out: list[str] = []
    i = 0
    while i < len(low):
        matched = False
        for latin, cyr in _MULTI.items():
            if low.startswith(latin, i):
                out.append(cyr)
                i += len(latin)
                matched = True
                break
        if matched:
            continue
        ch = low[i]
        out.append(_SINGLE.get(ch, ch))
        i += 1
    text = "".join(out)
    return text[:1].upper() + text[1:]


def translate_team_name(name: Any) -> str:
    raw = _squash_spaces(str(name or ""))
    if not raw:
        return ""
    if raw in TEAM_ALIASES:
        return TEAM_ALIASES[raw]
    stripped = _strip_club_suffixes(raw)
    if stripped in TEAM_ALIASES:
        return TEAM_ALIASES[stripped]

    tokens = re.split(r"([ \-])", stripped)
    translated: list[str] = []
    for token in tokens:
        if token in {" ", "-"}:
            translated.append(token)
            continue
        if not token:
            continue
        if re.search(r"[A-Za-z]", token):
            translated.append(transliterate_word(token))
        else:
            translated.append(token)
    result = _squash_spaces("".join(translated))
    return result or stripped or raw


def translate_league_name(name: Any) -> str:
    raw = _squash_spaces(str(name or ""))
    if not raw:
        return ""
    if raw in LEAGUE_ALIASES:
        return LEAGUE_ALIASES[raw]
    parts = [part.strip() for part in raw.split("-", 1)]
    countries = {
        "USA": "США",
        "Ukraine": "Украина",
        "Australia": "Австралия",
        "Egypt": "Египет",
        "Spain": "Испания",
        "Netherlands": "Нидерланды",
        "Germany": "Германия",
        "Armenia": "Армения",
        "England": "Англия",
        "Italy": "Италия",
        "France": "Франция",
        "Portugal": "Португалия",
        "Belgium": "Бельгия",
        "Turkey": "Турция",
    }
    league_words = {
        "Premier League": "Премьер-лига",
        "Queensland Premier League": "Премьер-лига Квинсленда",
        "Victoria Premier League": "Премьер-лига Виктории",
        "LaLiga": "Ла Лига",
        "Eredivisie": "Эредивизи",
        "DFB Pokal": "Кубок DFB",
    }
    if len(parts) == 2:
        country = countries.get(parts[0], parts[0])
        league = parts[1]
        for en, ru in league_words.items():
            league = league.replace(en, ru)
        return f"{country} - {league}"
    return raw


def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str:
    text = _squash_spaces(str(selection or ""))
    if not text:
        return ""
    replacements = {
        str(home_team or ""): translate_team_name(home_team),
        str(away_team or ""): translate_team_name(away_team),
        "Over": "Больше",
        "Under": "Меньше",
        "Yes": "Да",
        "No": "Нет",
    }
    for src, dst in replacements.items():
        if src and dst:
            text = text.replace(src, dst)
    return _squash_spaces(text)


_REASON_TRANSLATIONS = {
    "canonical_negative_value": "отрицательная контрольная ценность",
    "match_time_outside_window": "матч вне текущего окна публикации",
    "match_already_started": "матч уже начался",
    "match_time_too_late": "матч слишком далеко по времени",
    "missing_commence_time": "нет времени начала матча",
    "duplicate_fallback_sent_index": "резервный прогноз уже отправлялся",
    "duplicate_state:bets": "ставка уже есть в опубликованных",
    "duplicate_state:published_candidates": "кандидат уже публиковался",
    "tier_a_quality_below_min": "уровень A: качество ниже минимума",
    "tier_a_canonical_edge_below_min": "уровень A: запас ниже минимума",
    "tier_a_canonical_ev_below_min": "уровень A: EV ниже минимума",
    "tier_b_quality_below_min": "уровень B: качество ниже минимума",
    "tier_b_canonical_edge_below_min": "уровень B: запас ниже минимума",
    "tier_b_canonical_ev_below_min": "уровень B: EV ниже минимума",
    "tier_c_quality_below_min": "уровень C: качество ниже минимума",
    "tier_c_canonical_edge_below_min": "уровень C: запас ниже минимума",
    "tier_c_canonical_ev_below_min": "уровень C: EV ниже минимума",
    "books_below_min": "недостаточно линий букмекеров",
    "sources_below_min": "недостаточно источников",
    "confidence_below_min": "уверенность ниже минимума",
    "quality_below_min": "качество ниже минимума",
    "publication_score_below_min": "публикационный балл ниже минимума",
}


def translate_reject_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return "неизвестная причина"
    return _REASON_TRANSLATIONS.get(text, text.replace("_", " "))


def normalize_telegram_text(text: Any) -> str:
    value = str(text or "")
    replacements = {
        "controlled fallback": "контролируемый резерв",
        "Controlled fallback": "Контролируемый резерв",
        "Tier A": "уровень A",
        "Tier B": "уровень B",
        "Tier C": "уровень C",
        "single-book": "одна линия букмекера",
        "single-source": "один источник",
        "heavy-shrink": "сильная корректировка",
        "non-core": "вне основного пула",
        "canonical value": "контрольная ценность",
        "Canonical value": "Контрольная ценность",
        "quality-layer": "слой качества",
        "quality": "качество",
        "value": "ценность",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value
