from __future__ import annotations

"""Direct detailed-report Telegram sender with stale-artifact protection.

This script is intentionally independent from the historical clean sender. It
reads the already rendered cleaned detailed report, sends it to Telegram with a
plain concatenated HTTPS URL, and always writes a fresh send status artifact.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
CLEANED = Path(".data/exports/latest-detailed-run-report-cleaned.txt")
FALLBACK = Path(".data/exports/latest-detailed-run-report.txt")
STATUS = Path(".data/exports/latest-detailed-run-report-send-clean.json")
STATE = Path(".data/detailed-run-report-sent.json")


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw))) if raw not in (None, "") else default
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


def split_messages(text: str, limit: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if cur and size + extra > limit:
            chunks.append("\n".join(cur).strip())
            cur = []
            size = 0
        cur.append(line)
        size += extra
    if cur:
        chunks.append("\n".join(cur).strip())
    total = len(chunks)
    if total <= 1:
        return chunks
    return [f"🧾 Подробный отчёт run — часть {i}/{total}\n\n{chunk}" for i, chunk in enumerate(chunks, 1)]


def send_one(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = "https://api.telegram.org/bot" + str(token) + "/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=25) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return 200 <= int(resp.status) < 300, body[:700]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    created = datetime.now(UTC).isoformat()
    text = read_text(CLEANED) or read_text(FALLBACK)
    token = str(os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chunks = split_messages(text, env_int("TELEGRAM_MESSAGE_SOFT_LIMIT", 3600))
    sent: list[dict[str, Any]] = []
    if not text:
        sent.append({"ok": False, "response_preview": "report_text_missing"})
    elif not token or not chat_id:
        sent.append({"ok": False, "response_preview": "missing_telegram_credentials"})
    else:
        for chunk in chunks:
            ok, preview = send_one(token, chat_id, chunk)
            sent.append({"ok": ok, "response_preview": preview})
    payload = {
        "status": "sent" if sent and all(x.get("ok") for x in sent) else "not_sent_or_partial",
        "created_at_utc": created,
        "sender": "send_detailed_report_direct",
        "source_path": str(CLEANED if CLEANED.exists() else FALLBACK),
        "cleaned_path": str(CLEANED),
        "chunks": len(chunks),
        "ideal_audit_scorecard_added": "🧭 Ideal runtime audit" in text,
        "sent": sent,
    }
    write_json(STATUS, payload)
    write_json(STATE, {"last_sent_at_utc": created, "chunks": len(chunks), "sender": "send_detailed_report_direct"})
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
