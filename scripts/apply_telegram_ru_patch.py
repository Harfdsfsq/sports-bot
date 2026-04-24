from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_PATH = ROOT / "app" / "services" / "telegram.py"
FALLBACK_PATH = ROOT / "scripts" / "publish_controlled_fallback.py"


def patch_telegram_py() -> bool:
    if not TELEGRAM_PATH.exists():
        return False
    text = TELEGRAM_PATH.read_text(encoding="utf-8")
    original = text

    import_line = "from app.services.telegram_i18n import normalize_telegram_text_ru\n"
    if import_line not in text:
        marker = "from app.utils import russian_market_name, russian_selection\n"
        if marker in text:
            text = text.replace(marker, marker + import_line)

    old = '        text = str(message or "").strip()\n'
    new = '        text = normalize_telegram_text_ru(str(message or "").strip())\n'
    if old in text and new not in text:
        text = text.replace(old, new, 1)

    # Translate recurring labels in the main Telegram renderer while keeping logic unchanged.
    direct_replacements = {
        'f"🛡 Профиль сигнала: {trust[\'grade\']} {trust[\'score\']:.1f}/100"': 'f"🛡 Профиль сигнала: {trust[\'grade\']} {trust[\'score\']:.1f}/100"',
        'f"quality {trust[\'quality_score\']:.1f}"': 'f"качество {trust[\'quality_score\']:.1f}"',
        'risk_flags.append("single-book")': 'risk_flags.append("одна линия")',
        'risk_flags.append("single-source")': 'risk_flags.append("один источник")',
        'risk_flags.append("non-core")': 'risk_flags.append("вне основного пула")',
        'risk_flags.append("heavy-shrink")': 'risk_flags.append("сильная корректировка")',
        '"• Линия и value: {edge_text}"': '"• Линия и ценность: {edge_text}"',
        '"xG"),': '"Ожидаемые голы"),',
    }
    for old_s, new_s in direct_replacements.items():
        text = text.replace(old_s, new_s)

    if text != original:
        TELEGRAM_PATH.write_text(text, encoding="utf-8")
        return True
    return False


def patch_fallback_py() -> bool:
    if not FALLBACK_PATH.exists():
        return False
    text = FALLBACK_PATH.read_text(encoding="utf-8")
    original = text

    import_line = "\ntry:\n    from app.services.telegram_i18n import normalize_telegram_text_ru\nexcept Exception:\n    def normalize_telegram_text_ru(message):\n        return str(message or '').strip()\n"
    if "normalize_telegram_text_ru" not in text:
        marker = "from urllib import parse, request\n"
        if marker in text:
            text = text.replace(marker, marker + import_line)

    # Make sure every outgoing Telegram text is normalized.
    old = '    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")\n'
    new = '    text = normalize_telegram_text_ru(text)\n    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")\n'
    if old in text and new not in text:
        text = text.replace(old, new, 1)

    replacements = {
        '"🔥 1 контролируемый прогноз на ближайшие 24 часа\\n\\n"': '"🔥 1 контролируемый прогноз на ближайшие 24 часа\\n\\n"',
        '"контролируемый fallback Tier A: 2+ букмекера и нормальный запас value."': '"контролируемый резерв, уровень A: 2+ букмекера и нормальный запас ценности."',
        '"контролируемый fallback Tier B: ставка снижена, потому что основной quality-layer не дал чистую ставку."': '"контролируемый резерв, уровень B: ставка снижена, потому что основной слой качества не дал чистую ставку."',
        '"controlled fallback Tier C: single-book/пограничный резерв, минимальная тестовая сумма."': '"контролируемый резерв, уровень C: одна линия или пограничный резерв, минимальная тестовая сумма."',
        'f"✅ Уверенность: {metrics[\'confidence\']:.1f}% | quality {metrics[\'quality_score\']:.1f} | {tier}\\n"': 'f"✅ Уверенность: {metrics[\'confidence\']:.1f}% | качество {metrics[\'quality_score\']:.1f} | {tier.replace(\'Tier\', \'Уровень\')}\\n"',
        'f"🧮 Canonical value: edge {metrics[\'canonical_edge_pp\']:+.1f} п.п. | EV {metrics[\'canonical_ev_pct\']:+.1f}%\\n"': 'f"🧮 Контрольная ценность: перевес {metrics[\'canonical_edge_pp\']:+.1f} п.п. | EV {metrics[\'canonical_ev_pct\']:+.1f}%\\n"',
        '"Основной quality-layer не нашёл чистую ставку, а controlled fallback не нашёл безопасный резервный вариант."': '"Основной слой качества не нашёл чистую ставку, а контролируемый резерв не нашёл безопасный вариант."',
        '"Кандидатов fallback проверено: {report.get(\'candidates_seen\', 0)}",': '"Проверено резервных кандидатов: {report.get(\'candidates_seen\', 0)}",',
        '"📝 Комментарий: основной quality-layer не дал чистую ставку. Публикация разрешена только после повторного пересчёта EV от выбранного коэффициента."': '"📝 Комментарий: основной слой качества не дал чистую ставку. Публикация разрешена только после повторного пересчёта EV от выбранного коэффициента."',
    }
    for old_s, new_s in replacements.items():
        text = text.replace(old_s, new_s)

    if text != original:
        FALLBACK_PATH.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = []
    if patch_telegram_py():
        changed.append(str(TELEGRAM_PATH.relative_to(ROOT)))
    if patch_fallback_py():
        changed.append(str(FALLBACK_PATH.relative_to(ROOT)))
    print("Telegram RU patch applied" + (": " + ", ".join(changed) if changed else ": no changes needed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
