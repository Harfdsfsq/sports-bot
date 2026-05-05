from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
TXT_PATH = Path(".data/exports/latest-detailed-run-report.txt")
JSON_PATH = Path(".data/exports/latest-detailed-run-report.json")
OUT_PATH = Path(".data/exports/latest-detailed-run-report-cleaned.txt")
STATE_PATH = Path(".data/detailed-run-report-sent.json")
REMOVED_PROVIDERS = {"api_football", "bookies_api", "oddspapi"}


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw))) if raw not in (None, "") else default
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_deleted_provider_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def candidate_section(text: str) -> str:
    marker = "⚠️ Пограничные кандидаты"
    idx = text.find(marker)
    if idx < 0:
        marker = "⚠️ Остальные пограничные кандидаты"
        idx = text.find(marker)
    if idx < 0:
        return ""
    tail = text[idx:]
    for stop in ("\n🧠 ", "\n🔌 ", "\n📌 "):
        j = tail.find(stop)
        if j > 0:
            return tail[:j]
    return tail


def count_reason(text: str, reason: str) -> int:
    return text.lower().count(reason.lower())


def build_conclusion(text: str) -> list[str]:
    lowered = text.lower()
    section = candidate_section(text)
    duplicate_count = count_reason(section, "матч уже был опубликован ранее") + count_reason(section, "такой прогноз уже отправлялся ранее")
    confirm_count = count_reason(section, "confirmation sources below min:1/2")
    btts_count = count_reason(section, "BTTS-модель слишком расходится")
    spreads_count = count_reason(section, "закрытая семья рынка: форы")
    selected = "✅ Опубликовано" in text or "прогноз опубликован" in lowered
    lines = ["📌 Вывод"]
    if selected:
        lines.append("• Контролируемый прогноз выбран; он должен уходить отдельным Telegram-сообщением при live-publish режиме.")
    if duplicate_count:
        lines.append("• Часть сильных value-кандидатов не дублировалась, потому что матч уже был опубликован ранее.")
    if confirm_count:
        lines.append("• Лучшие новые value-кандидаты сейчас упёрлись во второй подтверждающий источник: confirmation sources 1/2.")
    if spreads_count:
        lines.append("• Форы закрыты guard’ом до полной проверки handicap-parser.")
    if btts_count:
        lines.append("• BTTS-кандидат отклонён из-за расхождения модели с xG.")
    if not any([selected, duplicate_count, confirm_count, spreads_count, btts_count]):
        lines.append("• Новых publishable-прогнозов нет: кандидаты не прошли финальные guards.")
    lines.append("• Guards не ослаблялись: публикация разрешается только для нового кандидата с достаточным подтверждением и стабильной value.")
    return lines


def replace_conclusion(text: str) -> str:
    conclusion = "\n".join(build_conclusion(text))
    marker = "📌 Вывод"
    idx = text.find(marker)
    if idx < 0:
        return text.rstrip() + "\n\n" + conclusion + "\n"
    return text[:idx].rstrip() + "\n\n" + conclusion + "\n"


def extract_provider_lines(text: str) -> list[str]:
    marker = "📡 Источники / фактическая работа"
    idx = text.find(marker)
    if idx < 0:
        return []
    tail = text[idx:].splitlines()[1:]
    lines: list[str] = []
    for line in tail:
        if not line.startswith("• "):
            if line.strip().startswith(("📈", "🚫", "⚠️", "✅", "🧠", "🔌", "📌")):
                break
            continue
        stripped = line.strip().lower()
        if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS):
            continue
        lines.append(line.strip())
    return lines


def provider_name(line: str) -> str:
    return line.split(":", 1)[0].replace("•", "").strip()


def provider_meaning(name: str, line: str) -> str:
    low = name.lower()
    if low == "odds_api_io":
        return "главный источник линий и списка матчей; даёт букмекерские предложения и покрытие по Bet365/Unibet/Betfair/Sbobet"
    if low == "bzzoiro":
        return "контекст/прогнозы; полезен как независимое подтверждение, но матчинг сейчас даёт мало exact/fuzzy совпадений"
    if low == "sstats":
        return "форма/статистика команд; сейчас основной поставщик контекста для near-window матчей"
    if low == "football_data":
        return "fixture/league календарь; нужен для добора матчей и алиасов, не как источник коэффициентов"
    if low == "thesportsdb":
        return "fixture и алиасы команд/лиг; бюджет быстро расходуется, но помогает расширять inventory"
    if low == "sportlogic":
        return "пока probe/context-кандидат; games получает, но events_matched/odds parsed = 0, значит нужен mapping/parser"
    if low == "openfootball":
        return "публичный календарь/алиасы; в этом run данных не дал"
    if low == "weather":
        return "погодный overlay для ближайших матчей"
    return "вспомогательный источник; смотри data/req/reason в строке"


def build_api_work_block(text: str) -> str:
    provider_lines = extract_provider_lines(text)
    quota_lines: list[str] = []
    marker = "🔌 API / квоты последнего run"
    idx = text.find(marker)
    if idx >= 0:
        for line in text[idx:].splitlines()[1:]:
            if not line.startswith("• "):
                if line.strip().startswith("📌"):
                    break
                continue
            stripped = line.strip().lower()
            if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS):
                continue
            quota_lines.append(line.strip())
    if not provider_lines and not quota_lines:
        return ""
    by_provider: dict[str, dict[str, str]] = {}
    for line in provider_lines:
        name = provider_name(line)
        by_provider.setdefault(name, {})["work"] = line.replace(f"• {name}: ", "")
    for line in quota_lines:
        name = provider_name(line)
        by_provider.setdefault(name, {})["quota"] = line.replace(f"• {name}: ", "")
    order = ["odds_api_io", "bzzoiro", "sstats", "football_data", "thesportsdb", "sportlogic", "openfootball", "weatherapi", "openweathermap", "futrixmetrics", "gnews", "meteostat", "oddsfeed", "sportsbook_api"]
    names = sorted(by_provider, key=lambda x: (order.index(x.lower()) if x.lower() in order else 99, x.lower()))
    lines = ["🧩 Работа API — разбор", ""]
    for name in names:
        item = by_provider[name]
        work = item.get("work", "нет runtime-строки")
        quota = item.get("quota", "нет quota-строки")
        lines.append(f"• {name}: {provider_meaning(name, work)}")
        lines.append(f"  - работа: {work}")
        lines.append(f"  - квота: {quota}")
    return "\n".join(lines)


def insert_api_work_block(text: str) -> str:
    block = build_api_work_block(text)
    if not block:
        return text
    marker = "🔌 API / квоты последнего run"
    idx = text.find(marker)
    if idx < 0:
        return text.rstrip() + "\n\n" + block + "\n"
    return text[:idx].rstrip() + "\n\n" + block + "\n\n" + text[idx:].rstrip() + "\n"


def clean_report(text: str) -> str:
    text = remove_deleted_provider_lines(text)
    text = insert_api_work_block(text)
    text = replace_conclusion(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def split_messages(text: str, limit: int = 3600) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > limit:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current).strip())
    total = len(chunks)
    if total <= 1:
        return chunks
    return [f"🧾 Подробный отчёт run — часть {i}/{total}\n\n{chunk}" for i, chunk in enumerate(chunks, 1)]


def telegram_send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with request.urlopen(url, data=data, timeout=20) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200, body[:500]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def should_send() -> bool:
    return env_bool("DETAILED_RUN_REPORT_SEND_TELEGRAM", True) or env_bool("DETAILED_RUN_REPORT_FORCE_SEND", False)


def main() -> int:
    raw = read_text(TXT_PATH)
    if not raw:
        payload = {"status": "skipped", "reason": "latest-detailed-run-report.txt missing", "path": str(TXT_PATH)}
        write_json(Path(".data/exports/latest-detailed-run-report-send-clean.json"), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    cleaned = clean_report(raw)
    write_text(OUT_PATH, cleaned)
    token = str(os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chunks = split_messages(cleaned, env_int("TELEGRAM_MESSAGE_SOFT_LIMIT", 3600))
    sent: list[dict[str, Any]] = []
    if should_send() and token and chat_id:
        for chunk in chunks:
            ok, body = telegram_send(token, chat_id, chunk)
            sent.append({"ok": ok, "response_preview": body})
    else:
        sent.append({"ok": False, "response_preview": "send_disabled_or_missing_telegram_credentials"})
    payload = {"status": "sent" if sent and all(item.get("ok") for item in sent) else "not_sent_or_partial", "created_at_utc": datetime.now(UTC).isoformat(), "source_path": str(TXT_PATH), "cleaned_path": str(OUT_PATH), "chunks": len(chunks), "removed_providers": sorted(REMOVED_PROVIDERS), "api_work_block_added": bool(build_api_work_block(cleaned)), "sent": sent, "json_report_present": JSON_PATH.exists()}
    write_json(Path(".data/exports/latest-detailed-run-report-send-clean.json"), payload)
    write_json(STATE_PATH, {"last_sent_at_utc": payload["created_at_utc"], "chunks": len(chunks), "cleaned_path": str(OUT_PATH)})
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
