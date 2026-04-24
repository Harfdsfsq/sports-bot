from __future__ import annotations

import re
from typing import Any

# Telegram-facing localization.
# Goal: all public Telegram messages should be readable in Russian.
# Known names use explicit aliases; unknown names fall back to safe transliteration.

TEAM_ALIASES: dict[str, str] = {
    "MKS Znicz Pruszkow": "Знич Прушкув",
    "Znicz Pruszkow": "Знич Прушкув",
    "MKS Znicz Pruszków": "Знич Прушкув",
    "Znicz Pruszków": "Знич Прушкув",
    "Gornik Leczna": "Гурник Ленчна",
    "Górnik Łęczna": "Гурник Ленчна",
    "Gornik Łęczna": "Гурник Ленчна",
    "Górnik Leczna": "Гурник Ленчна",
    "Wisla Krakow": "Висла Краков",
    "Wisła Kraków": "Висла Краков",
    "Arka Gdynia": "Арка Гдыня",
    "Miedz Legnica": "Медзь Легница",
    "Miedź Legnica": "Медзь Легница",
    "Ruch Chorzow": "Рух Хожув",
    "Ruch Chorzów": "Рух Хожув",
    "Polonia Warszawa": "Полония Варшава",
    "Stal Rzeszow": "Сталь Жешув",
    "Stal Rzeszów": "Сталь Жешув",
    "Odra Opole": "Одра Ополе",
    "Termalica Bruk-Bet Nieciecza": "Термалика Брук-Бет Нецеча",
    "Bruk-Bet Termalica Nieciecza": "Брук-Бет Термалика Нецеча",
    "SKU Amstetten": "СКУ Амштеттен",
    "Amstetten": "Амштеттен",
    "First Vienna FC 1894": "Фёрст Вена 1894",
    "First Vienna FC": "Фёрст Вена",
    "First Vienna": "Фёрст Вена",
    "Vienna FC": "Вена",
    "Austria Lustenau": "Аустрия Лустенау",
    "Admira Wacker": "Адмира Ваккер",
    "SV Ried": "Рид",
    "SKN St. Polten": "Санкт-Пёльтен",
    "SKN St. Pölten": "Санкт-Пёльтен",
    "Kapfenberger SV": "Капфенберг",
    "Floridsdorfer AC": "Флоридсдорф",
    # Saudi Arabia / Gulf
    "Al-Fateh SC": "Аль-Фатех",
    "Al Fateh SC": "Аль-Фатех",
    "Al-Fateh": "Аль-Фатех",
    "Al Fateh": "Аль-Фатех",
    "Al-Khaleej Club": "Аль-Халидж",
    "Al Khaleej Club": "Аль-Халидж",
    "Al-Khaleej": "Аль-Халидж",
    "Al Khaleej": "Аль-Халидж",
    "Al Hilal": "Аль-Хиляль",
    "Al-Hilal": "Аль-Хиляль",
    "Al Nassr": "Аль-Наср",
    "Al-Nassr": "Аль-Наср",
    "Al Ittihad": "Аль-Иттихад",
    "Al-Ittihad": "Аль-Иттихад",
    "Al Ahli": "Аль-Ахли",
    "Al-Ahli": "Аль-Ахли",
    "Al Shabab": "Аль-Шабаб",
    "Al-Shabab": "Аль-Шабаб",
    "Al Taawoun": "Аль-Таавун",
    "Al-Taawoun": "Аль-Таавун",
    "Al Riyadh": "Аль-Рияд",
    "Al-Riyadh": "Аль-Рияд",
    "Al Ettifaq": "Аль-Иттифак",
    "Al-Ettifaq": "Аль-Иттифак",
    "Damac FC": "Дамак",
    "Abha Club": "Абха",

    # UAE
    "Dubai United FC": "Дубай Юнайтед",
    "Dubai United": "Дубай Юнайтед",
    "Gulf United": "Галф Юнайтед",
    "Gulf United FC": "Галф Юнайтед",
    "Al Arabi UAE": "Аль-Араби ОАЭ",
    "Al Dhafra": "Аль-Дафра",
    "Dibba Al Fujairah": "Дибба Аль-Фуджейра",
    "Hatta Club": "Хатта",
    "Masfout": "Масфут",
    "Al Hamriyah": "Аль-Хамрия",

    # Previously seen teams
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
    "El Gouna FC": "Эль-Гуна",
    "Pharco FC": "Фарко",
    "PFC Spartak Pleven": "Спартак Плевен",
    "FC Yantra Gabrovo": "Янтра Габрово",
}

LEAGUE_ALIASES: dict[str, str] = {
    "Poland - I Liga": "Польша - Первая лига",
    "Poland - Ekstraklasa": "Польша - Экстракласса",
    "Poland - II Liga": "Польша - Вторая лига",
    "Poland - Polish Cup": "Польша - Кубок Польши",
    "Austria - 2. Liga": "Австрия - Вторая лига",
    "Austria - Bundesliga": "Австрия - Бундеслига",
    "Austria - OFB Cup": "Австрия - Кубок OFB",
    "Saudi Arabia - Saudi Pro League": "Саудовская Аравия - Про-лига",
    "Saudi Arabia - Pro League": "Саудовская Аравия - Про-лига",
    "Saudi Arabia - Professional League": "Саудовская Аравия - Про-лига",
    "Saudi Arabia - First Division": "Саудовская Аравия - Первый дивизион",
    "Saudi Arabia - King Cup": "Саудовская Аравия - Кубок Короля",
    "United Arab Emirates - First Division": "ОАЭ - Первый дивизион",
    "United Arab Emirates - Pro League": "ОАЭ - Про-лига",
    "UAE - First Division": "ОАЭ - Первый дивизион",
    "UAE - Pro League": "ОАЭ - Про-лига",
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
    "Bulgaria - Vtora Liga": "Болгария - Вторая лига",
    "Romania - Liga III": "Румыния - Лига III",
}

COUNTRY_ALIASES: dict[str, str] = {
    "USA": "США",
    "United States": "США",
    "United States of America": "США",
    "England": "Англия",
    "Scotland": "Шотландия",
    "Wales": "Уэльс",
    "Ireland": "Ирландия",
    "Northern Ireland": "Северная Ирландия",
    "Spain": "Испания",
    "Italy": "Италия",
    "Germany": "Германия",
    "France": "Франция",
    "Portugal": "Португалия",
    "Netherlands": "Нидерланды",
    "Belgium": "Бельгия",
    "Austria": "Австрия",
    "Switzerland": "Швейцария",
    "Denmark": "Дания",
    "Sweden": "Швеция",
    "Norway": "Норвегия",
    "Finland": "Финляндия",
    "Iceland": "Исландия",
    "Poland": "Польша",
    "Czech Republic": "Чехия",
    "Slovakia": "Словакия",
    "Hungary": "Венгрия",
    "Romania": "Румыния",
    "Bulgaria": "Болгария",
    "Greece": "Греция",
    "Turkey": "Турция",
    "Croatia": "Хорватия",
    "Serbia": "Сербия",
    "Slovenia": "Словения",
    "Ukraine": "Украина",
    "Armenia": "Армения",
    "Georgia": "Грузия",
    "Azerbaijan": "Азербайджан",
    "Kazakhstan": "Казахстан",
    "Saudi Arabia": "Саудовская Аравия",
    "United Arab Emirates": "ОАЭ",
    "UAE": "ОАЭ",
    "Qatar": "Катар",
    "Bahrain": "Бахрейн",
    "Kuwait": "Кувейт",
    "Oman": "Оман",
    "Egypt": "Египет",
    "Morocco": "Марокко",
    "Tunisia": "Тунис",
    "Algeria": "Алжир",
    "South Africa": "ЮАР",
    "Australia": "Австралия",
    "New Zealand": "Новая Зеландия",
    "Japan": "Япония",
    "South Korea": "Южная Корея",
    "China": "Китай",
    "India": "Индия",
    "Brazil": "Бразилия",
    "Argentina": "Аргентина",
    "Chile": "Чили",
    "Colombia": "Колумбия",
    "Peru": "Перу",
    "Uruguay": "Уругвай",
    "Mexico": "Мексика",
    "Canada": "Канада",
}

LEAGUE_WORDS: dict[str, str] = {
    "Premier League": "Премьер-лига",
    "Professional League": "Профессиональная лига",
    "Pro League": "Про-лига",
    "Saudi Pro League": "Про-лига",
    "First Division": "Первый дивизион",
    "Second Division": "Второй дивизион",
    "Third Division": "Третий дивизион",
    "Championship": "Чемпионшип",
    "League One": "Лига 1",
    "League Two": "Лига 2",
    "2. Liga": "Вторая лига",
    "National League": "Национальная лига",
    "Super League": "Суперлига",
    "Superliga": "Суперлига",
    "Liga I": "Лига I",
    "I Liga": "Первая лига",
    "Liga II": "Лига II",
    "Liga III": "Лига III",
    "LaLiga": "Ла Лига",
    "Serie A": "Серия A",
    "Serie B": "Серия B",
    "Bundesliga": "Бундеслига",
    "2. Bundesliga": "Вторая Бундеслига",
    "Ligue 1": "Лига 1",
    "Ligue 2": "Лига 2",
    "Eredivisie": "Эредивизи",
    "Eerste Divisie": "Первый дивизион",
    "DFB Pokal": "Кубок DFB",
    "Cup": "Кубок",
    "King Cup": "Кубок Короля",
    "MLS": "MLS",
    "A-League": "A-Лига",
}

WORD_ALIASES: dict[str, str] = {
    "mks": "",
    "znicz": "Знич",
    "pruszkow": "Прушкув",
    "pruszków": "Прушкув",
    "gornik": "Гурник",
    "górnik": "Гурник",
    "leczna": "Ленчна",
    "łęczna": "Ленчна",
    "poland": "Польша",
    "polish": "Польский",
    "wisla": "Висла",
    "wisła": "Висла",
    "krakow": "Краков",
    "kraków": "Краков",
    "gdynia": "Гдыня",
    "legnica": "Легница",
    "chorzow": "Хожув",
    "chorzów": "Хожув",
    "warszawa": "Варшава",
    "rzeszow": "Жешув",
    "rzeszów": "Жешув",
    "opole": "Ополе",
    "amstetten": "Амштеттен",
    "first": "Фёрст",
    "vienna": "Вена",
    "austria": "Австрия",
    "liga": "Лига",
    "lustenau": "Лустенау",
    "admira": "Адмира",
    "wacker": "Ваккер",
    "ried": "Рид",
    "kapfenberger": "Капфенберг",
    "floridsdorfer": "Флоридсдорф",
    # club words
    "fc": "",
    "f.c.": "",
    "sc": "",
    "s.c.": "",
    "cf": "",
    "ac": "",
    "afc": "",
    "ud": "",
    "pfc": "",
    "club": "",
    "united": "Юнайтед",
    "city": "Сити",
    "town": "Таун",
    "county": "Каунти",
    "rovers": "Роверс",
    "wanderers": "Уондерерс",
    "rangers": "Рейнджерс",
    "athletic": "Атлетик",
    "sporting": "Спортинг",
    "real": "Реал",
    "deportivo": "Депортиво",
    "inter": "Интер",
    "internacional": "Интернасьонал",
    "olympic": "Олимпик",
    "olympique": "Олимпик",
    "dynamo": "Динамо",
    "dinamo": "Динамо",
    "lokomotiv": "Локомотив",
    "arsenal": "Арсенал",
    "juventus": "Ювентус",
    "central": "Сентрал",
    "north": "Норт",
    "south": "Саут",
    "east": "Ист",
    "west": "Вест",
    "stars": "Старз",
    "star": "Стар",
    "eagles": "Иглз",
    "white": "Уайт",
    "bulldogs": "Буллдогс",
    "strikers": "Страйкерс",
    "police": "Полис",
    "bank": "Банк",
    "national": "Нэшнл",

    # geographic/common words
    "new": "Нью",
    "york": "Йорк",
    "dubai": "Дубай",
    "gulf": "Галф",
    "emirates": "Эмирейтс",
    "uae": "ОАЭ",
    "saudi": "Саудовская",
    "arabia": "Аравия",
    "egypt": "Иджипт",

    # Arabic-style names
    "al": "Аль",
    "el": "Эль",
    "arabi": "Араби",
    "fateh": "Фатех",
    "khaleej": "Халидж",
    "hilal": "Хиляль",
    "nassr": "Наср",
    "ittihad": "Иттихад",
    "ahli": "Ахли",
    "shabab": "Шабаб",
    "taawoun": "Таавун",
    "riyadh": "Рияд",
    "ettifaq": "Иттифак",
    "raed": "Раед",
    "wehda": "Вахда",
    "qadsiah": "Кадисия",
    "damac": "Дамак",
    "abha": "Абха",
    "okhdood": "Охдуд",
    "dhafra": "Дафра",
    "dibba": "Дибба",
    "fujairah": "Фуджейра",
    "hatta": "Хатта",
    "masfout": "Масфут",
    "hamriyah": "Хамрия",
}

_MULTI = {
    "sh": "ш", "ch": "ч", "zh": "ж", "ya": "я", "yu": "ю", "yo": "ё", "ye": "е",
    "kh": "х", "ts": "ц", "th": "т", "ph": "ф", "oo": "у", "ee": "и", "ou": "у",
    "ck": "к", "qu": "кв",
}
_SINGLE = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "и", "z": "з",
}
CLUB_SUFFIX_RE = re.compile(
    r"^(FC|SC|CF|AC|AFC|PFC|UD)\s+|\s+(FC|SC|CF|AC|AFC|PFC|UD|Club|F\.C\.|S\.C\.)$",
    flags=re.IGNORECASE,
)


def _squash_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _norm_key(value: Any) -> str:
    text = _squash_spaces(str(value or "")).lower()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-zа-яё0-9]+", " ", text, flags=re.IGNORECASE)
    return _squash_spaces(text)


def _lookup_alias(value: Any, aliases: dict[str, str]) -> str | None:
    raw = _squash_spaces(str(value or ""))
    if not raw:
        return None
    if raw in aliases:
        return aliases[raw]
    raw_key = _norm_key(raw)
    for alias, translated in aliases.items():
        if _norm_key(alias) == raw_key:
            return translated
    stripped = _strip_club_suffixes(raw)
    if stripped in aliases:
        return aliases[stripped]
    stripped_key = _norm_key(stripped)
    for alias, translated in aliases.items():
        if _norm_key(alias) == stripped_key:
            return translated
    return None


def _strip_club_suffixes(text: str) -> str:
    value = _squash_spaces(text)
    previous = None
    while previous != value:
        previous = value
        value = CLUB_SUFFIX_RE.sub("", value)
        value = _squash_spaces(value)
    return value


def _title_ru(text: str) -> str:
    if not text:
        return text
    if text.isupper() and len(text) <= 5:
        return text
    return text[:1].upper() + text[1:]


def transliterate_word(word: str) -> str:
    src = str(word or "").strip()
    if not src:
        return ""
    low = src.lower().strip(" .,'\"()[]{}")
    if not low:
        return ""

    if low in WORD_ALIASES:
        return WORD_ALIASES[low]

    if re.fullmatch(r"[A-ZА-ЯЁ0-9]+", src) and len(src) <= 5:
        return src

    # Preserve numbers, roman numerals and short league acronyms.
    if re.fullmatch(r"[IVX]+", src.upper()):
        return src.upper()
    if re.fullmatch(r"\d+(\.\d+)?", src):
        return src

    out: list[str] = []
    i = 0
    while i < len(low):
        matched = False
        for latin, cyr in sorted(_MULTI.items(), key=lambda item: len(item[0]), reverse=True):
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
    return _title_ru("".join(out))


def _translate_free_text_name(value: Any) -> str:
    raw = _squash_spaces(str(value or ""))
    if not raw:
        return ""
    raw = _strip_club_suffixes(raw)
    raw = raw.replace("&", " and ")
    tokens = re.split(r"([ \-/])", raw)
    result: list[str] = []
    for token in tokens:
        if token in {" ", "-", "/"}:
            result.append(token)
            continue
        if not token:
            continue
        if re.search(r"[А-Яа-яЁё]", token):
            result.append(token)
        elif re.search(r"[A-Za-z]", token):
            result.append(transliterate_word(token))
        else:
            result.append(token)
    text = _squash_spaces("".join(result))
    # Arabic style: "Аль - Фатех" -> "Аль-Фатех"
    text = re.sub(r"\b(Аль|Эль)\s*-\s*", r"\1-", text)
    return text or raw


def translate_team_name(name: Any) -> str:
    raw = _squash_spaces(str(name or ""))
    if not raw:
        return ""
    alias = _lookup_alias(raw, TEAM_ALIASES)
    if alias:
        return alias
    return _translate_free_text_name(raw)


def translate_league_name(name: Any) -> str:
    raw = _squash_spaces(str(name or ""))
    if not raw:
        return ""
    alias = _lookup_alias(raw, LEAGUE_ALIASES)
    if alias:
        return alias

    if " - " in raw:
        country_raw, league_raw = [part.strip() for part in raw.split(" - ", 1)]
    elif "-" in raw:
        country_raw, league_raw = [part.strip() for part in raw.split("-", 1)]
    else:
        country_raw, league_raw = "", raw

    country = _lookup_alias(country_raw, COUNTRY_ALIASES) if country_raw else ""
    if not country and country_raw:
        country = _translate_free_text_name(country_raw)

    league = league_raw
    for en, ru in sorted(LEAGUE_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
        league = re.sub(re.escape(en), ru, league, flags=re.IGNORECASE)
    if re.search(r"[A-Za-z]", league):
        # Translate remaining unknown English words, but preserve common acronyms.
        league = _translate_free_text_name(league)

    if country:
        return f"{country} - {league}"
    return league


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
    for src, dst in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if src and dst:
            text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
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
    if text in _REASON_TRANSLATIONS:
        return _REASON_TRANSLATIONS[text]
    market_names = {
        "spreads": "фора",
        "h2h": "исход",
        "totals": "тотал",
        "btts": "обе забьют",
        "teamtotals": "индивидуальный тотал",
        "teamTotals": "индивидуальный тотал",
        "dnb": "фора 0 / DNB",
    }
    if text.startswith("family_not_allowed:"):
        family = text.split(":", 1)[1]
        return f"рынок не разрешён: {market_names.get(family, family)}"
    if "quality_stop_not_allowed:" in text:
        return "стоп слоя качества не разрешён для резерва"
    if text.startswith("tier_a_"):
        return "уровень A: " + text.removeprefix("tier_a_").replace("_", " ")
    if text.startswith("tier_b_"):
        return "уровень B: " + text.removeprefix("tier_b_").replace("_", " ")
    if text.startswith("tier_c_"):
        return "уровень C: " + text.removeprefix("tier_c_").replace("_", " ")
    return text.replace("_", " ")


def _replace_known_aliases(text: str) -> str:
    value = text
    alias_pairs: list[tuple[str, str]] = []
    alias_pairs.extend(TEAM_ALIASES.items())
    alias_pairs.extend(LEAGUE_ALIASES.items())
    alias_pairs.sort(key=lambda item: len(item[0]), reverse=True)
    for src, dst in alias_pairs:
        if not src or not dst:
            continue
        value = re.sub(re.escape(src), dst, value, flags=re.IGNORECASE)
    return value


def _normalize_match_line(line: str) -> str:
    # Examples:
    # 1. Team A — Team B
    # 12. Team A - Team B
    match = re.match(r"^(\s*\d+\.\s+)(.+?)\s+[—–-]\s+(.+?)\s*$", line)
    if not match:
        return line
    prefix, home, away = match.groups()
    return f"{prefix}{translate_team_name(home)} — {translate_team_name(away)}"


def _normalize_tournament_line(line: str) -> str:
    match = re.match(r"^(\s*🏆\s*Турнир:\s*)(.+?)\s*$", line)
    if not match:
        return line
    prefix, league = match.groups()
    return f"{prefix}{translate_league_name(league)}"


def _normalize_bet_line(line: str) -> str:
    if "🎯" not in line and "Ставка:" not in line:
        return line
    line = _replace_known_aliases(line)

    # Translate selections like:
    # 🎯 Ставка: Фора 0 / DNB — First Vienna FC 1894 (0)
    match = re.match(r"^(.*?—\s*)(.+?)(\s*\([^)]*\)\s*)$", line)
    if match:
        prefix, selection_name, suffix = match.groups()
        if re.search(r"[A-Za-z]", selection_name):
            return f"{prefix}{translate_team_name(selection_name)}{suffix}"

    match = re.match(r"^(.*?—\s*)(.+?)\s*$", line)
    if match:
        prefix, selection_name = match.groups()
        if re.search(r"[A-Za-z]", selection_name) and len(selection_name.split()) <= 5:
            return f"{prefix}{translate_team_name(selection_name)}"
    return line


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

    normalized_lines: list[str] = []
    for line in value.splitlines():
        out = _normalize_match_line(line)
        out = _normalize_tournament_line(out)
        out = _normalize_bet_line(out)
        out = _replace_known_aliases(out)
        normalized_lines.append(out.rstrip())

    return "\n".join(normalized_lines).strip()
