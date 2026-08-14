from __future__ import annotations

"""Direct detailed-report Telegram sender with stale-artifact protection.

Independent Telegram sender for the detailed HARIZON report. Before sending it
rebuilds the source detailed report, rebuilds the clean/API/audit enriched text
without Telegram side effects, then sends the freshly rendered cleaned text with
a plain concatenated HTTPS URL. It always overwrites the send-status artifact.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
CLEANED = Path(".data/exports/latest-detailed-run-report-cleaned.txt")
RAW = Path(".data/exports/latest-detailed-run-report.txt")
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


def run_step(args: list[str], env_updates: dict[str, str] | None = None, timeout: int = 90) -> dict[str, Any]:
    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)
    try:
        completed = subprocess.run(
            [sys.executable, *args],
            check=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=env,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout_preview": (completed.stdout or "")[:1000],
            "stderr_preview": (completed.stderr or "")[:1000],
        }
    except Exception as exc:
        return {"args": args, "returncode": -1, "error": f"{type(exc).__name__}: {exc}"}


def rebuild_report_text() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    # Builder must not send. It should only refresh latest-detailed-run-report.*
    steps.append(run_step(["scripts/build_detailed_run_report.py"], {
        "DETAILED_RUN_REPORT_SEND_TELEGRAM": "false",
        "DETAILED_RUN_REPORT_FORCE_SEND": "false",
    }))
    # Clean/enrich in-process, without sending, so we do not use the historical
    # broken Telegram URL path and still get API work + ideal scorecard blocks.
    try:
        import scripts.send_clean_detailed_run_report as clean_base
        raw = read_text(RAW)
        if raw:
            cleaned = clean_base.clean_report(raw)
            CLEANED.parent.mkdir(parents=True, exist_ok=True)
            CLEANED.write_text(cleaned, encoding="utf-8")
            scorecard_status: dict[str, Any] = {"status": "not_run"}
            try:
                from scripts.patch_ideal_audit_scorecard import main as scorecard_main
                scorecard_main()
                try:
                    scorecard_status = json.loads(Path(".data/exports/latest-ideal-audit-scorecard-patch.json").read_text(encoding="utf-8"))
                except Exception:
                    scorecard_status = {"status": "unknown"}
            except Exception as exc:
                scorecard_status = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            steps.append({"args": ["clean_report_in_process"], "returncode": 0, "scorecard": scorecard_status})
        else:
            steps.append({"args": ["clean_report_in_process"], "returncode": 1, "error": "raw_report_missing"})
    except Exception as exc:
        steps.append({"args": ["clean_report_in_process"], "returncode": -1, "error": f"{type(exc).__name__}: {exc}"})
    return steps


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
    rebuild_steps = rebuild_report_text()
    text = read_text(CLEANED) or read_text(RAW)
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
        "source_path": str(CLEANED if CLEANED.exists() else RAW),
        "cleaned_path": str(CLEANED),
        "chunks": len(chunks),
        "ideal_audit_scorecard_added": "🧭 Ideal runtime audit" in text,
        "rebuild_steps": rebuild_steps,
        "sent": sent,
    }
    write_json(STATUS, payload)
    write_json(STATE, {"last_sent_at_utc": created, "chunks": len(chunks), "sender": "send_detailed_report_direct"})
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["status"] == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
