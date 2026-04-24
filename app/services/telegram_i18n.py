from __future__ import annotations

import re
from typing import Any


def _replace_case_insensitive(text: str, old: str, new: str) -> str:
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


def normalize_telegram_text_ru(message: Any) -> str:
    """Normalize user-facing Telegram text to Russian wording.

    This is deliberately conservative: it does not change numbers, teams,
    markets, odds, or payload structure. It only translates operational labels
    and risk terms that were still leaking in English.
    """
    text = str(message or "").strip()
    if not text:
        return ""

    replacements = {
        "controlled fallback": "контролируемый резерв",
        "Controlled fallback": "Контролируемый резерв",
        "fallback": "резерв",
        "Fallback": "Резерв",
        "Tier A": "Уровень A",
        "Tier B": "Уровень B",
        "Tier C": "Уровень C",
        "single-book": "одна линия букмекера",
        "single book": "одна линия букмекера",
        "single-source": "один источник",
        "single source": "один источник",
        "heavy-shrink": "сильная корректировка",
        "non-core": "вне основного пула",
        "high-risk": "высокий риск",
        "low-stake": "малый размер ставки",
        "dry_run": "тестовый запуск",
        "dry run": "тестовый запуск",
        "quality-layer": "слой качества",
        "quality layer": "слой качества",
        "quality": "качество",
        "Quality": "Качество",
        "canonical value": "контрольная ценность",
        "Canonical value": "Контрольная ценность",
        "canonical EV": "контрольный EV",
        "Canonical EV": "Контрольный EV",
        "canonical edge": "контрольный перевес",
        "Canonical edge": "Контрольный перевес",
        "edge": "перевес",
        "Edge": "Перевес",
        "value": "ценность",
        "Value": "Ценность",
        "market": "рынок",
        "Market": "Рынок",
        "books": "линии",
        "Books": "Линии",
        "sources": "источники",
        "Sources": "Источники",
        "source": "источник",
        "Source": "Источник",
        "passed": "прошёл",
        "rejected": "отклонён",
        "safe": "безопасный",
        "reserve": "резервный",
        "test stake": "тестовая ставка",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Normalize common technical fragments that may appear in reports.
    regex_replacements = [
        (r"\bEV\b", "EV"),
        (r"\bno[-_ ]pick\b", "нет прогноза"),
        (r"\bmain[-_ ]publish\b", "основная публикация"),
        (r"\bpublish\b", "публикация"),
        (r"\bshadow[_ -]?bets\b", "теневые ставки"),
        (r"\bbets\b", "ставки"),
        (r"\bpublished[_ -]?candidates\b", "опубликованные кандидаты"),
        (r"\bduplicate\b", "дубль"),
        (r"\bnegative[_ -]?value\b", "отрицательная ценность"),
        (r"\bconfidence\b", "уверенность"),
        (r"\bbookmakers\b", "букмекеры"),
        (r"\bbookmaker\b", "букмекер"),
    ]
    for pattern, repl in regex_replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # Clean awkward phrase variants created by historical message templates.
    text = text.replace("контролируемый резерв Уровень", "контролируемый резерв, уровень")
    text = text.replace("Контролируемый резерв Уровень", "Контролируемый резерв, уровень")
    text = text.replace("качество-score", "оценка качества")
    text = text.replace("качество score", "оценка качества")
    text = text.replace("качество-стопоров", "стопоров качества")
    text = text.replace("quality-стопоров", "стопоров качества")
    text = text.replace("xG", "ожидаемые голы") if "📈" not in text else text

    # Typographic fixes.
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
