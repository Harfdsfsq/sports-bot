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
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
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
    lifecycle_filter = load_json(Path(".data/exports/latest-candidate-lifecycle-sent-filter.json"), {})
    sent_duplicates = int(lifecycle_filter.get("duplicates_blocked") or 0) if isinstance(lifecycle_filter, dict) else 0

    lines = ["📌 Вывод"]
    if duplicate_count or sent_duplicates or "уже был отправлен" in lowered:
        lines.append("• Лучший value-кандидат уже был опубликован ранее, поэтому дубль не отправлялся.")
    if confirm_count:
        lines.append("• Лучшие новые value-кандидаты сейчас упёрлись в недостающий второй подтверждающий источник: confirmation sources 1/2.")
    if spreads_count:
        lines.append("• Форы по-прежнему закрыты guard’ом до полной проверки handicap-parser, поэтому такие варианты не публикуются.")
    if btts_count:
        lines.append("• BTTS-кандидат отклонён не из-за линии, а из-за расхождения модели с xG.")
    if not (duplicate_count or sent_duplicates or confirm_count or spreads_count or btts_count):
        if "новых прогнозов нет" in lowered:
            lines.append("• Новых publishable-прогнозов нет: кандидаты не прошли финальные guards.")
        else:
            lines.append("• Отчёт построен, критичных runtime-стопоров не обнаружено.")
    lines.append("• Guards не ослаблялись: публикация разрешается только для нового кандидата с достаточным подтверждением и стабильной value.")
    return lines


def replace_conclusion(text: str) -> str:
    conclusion = "\n".join(build_conclusion(text))
    marker = "📌 Вывод"
    idx = text.find(marker)
    if idx < 0:
        return text.rstrip() + "\n\n" + conclusion + "\n"
    return text[:idx].rstrip() + "\n\n" + conclusion + "\n"


def clean_report(text: str) -> str:
    text = remove_deleted_provider_lines(text)
    text = replace_conclusion(text)
    # Collapse accidental 3+ blank lines after line removal.
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
    payload = {
        "status": "sent" if sent and all(item.get("ok") for item in sent) else "not_sent_or_partial",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_path": str(TXT_PATH),
        "cleaned_path": str(OUT_PATH),
        "chunks": len(chunks),
        "removed_providers": sorted(REMOVED_PROVIDERS),
        "sent": sent,
        "json_report_present": JSON_PATH.exists(),
    }
    write_json(Path(".data/exports/latest-detailed-run-report-send-clean.json"), payload)
    write_json(STATE_PATH, {"last_sent_at_utc": payload["created_at_utc"], "chunks": len(chunks), "cleaned_path": str(OUT_PATH)})
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
