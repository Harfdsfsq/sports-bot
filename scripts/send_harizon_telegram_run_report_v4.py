from __future__ import annotations

"""HARIZON Telegram run report v4.

Separates three states that were previously mixed:
- main runner published a pick;
- controlled fallback selected a pick;
- controlled fallback actually sent the separate Telegram pick.

If the final prepublish guard blocked Telegram send, the detailed report must not
show the run as `прогноз опубликован` only because fallback had a selected row.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

V3_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v3.py")
EXPORT_DIR = Path(".data/exports")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_v3() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_telegram_report_v3", V3_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v3 report wrapper: {V3_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v3 = _load_v3()
base = v3.base
_original_published_candidates = base.published_candidates
_original_diagnosis_lines = base.diagnosis_lines
_original_quick_status_lines = base.quick_status_lines


def _publication_status() -> dict[str, Any]:
    fallback = _load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    guard = _load_json(EXPORT_DIR / "latest-controlled-fallback-prepublish-guard.json", {})
    status = {
        "fallback_selected": bool(_original_published_candidates(fallback if isinstance(fallback, dict) else {})),
        "fallback_telegram_guard_active": bool(guard.get("active")) if isinstance(guard, dict) else False,
        "fallback_telegram_final_allowed": guard.get("final_allowed") if isinstance(guard, dict) else None,
        "fallback_telegram_blocked": False,
        "fallback_telegram_block_reason": None,
    }
    if isinstance(guard, dict) and guard:
        blocked = int(guard.get("blocked_telegram_sends") or 0) > 0 or guard.get("final_allowed") is False
        status["fallback_telegram_blocked"] = bool(blocked)
        status["fallback_telegram_block_reason"] = guard.get("final_reason") if blocked else None
    out = EXPORT_DIR / "latest-publication-status.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    return status


def published_candidates_v4(fallback: dict[str, Any]) -> list[dict[str, Any]]:
    status = _publication_status()
    if status.get("fallback_telegram_blocked"):
        return []
    return _original_published_candidates(fallback)


def quick_status_lines_v4(summary: dict[str, Any], fallback: dict[str, Any], source_stats: dict[str, Any], refresh_plan: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    lines = _original_quick_status_lines(summary, fallback, source_stats, refresh_plan, line_guard)
    status = _publication_status()
    if status.get("fallback_selected") and status.get("fallback_telegram_blocked"):
        reason = status.get("fallback_telegram_block_reason") or "guard_blocked"
        patched: list[str] = []
        for line in lines:
            if line.startswith("• Итог:"):
                patched.append("• Итог: 🟡 fallback выбрал кандидат, но Telegram-пик заблокирован guard")
            elif line.startswith("• Воронка:"):
                patched.append(line.replace("опубликовано 1", "опубликовано 0"))
            else:
                patched.append(line)
        patched.append(f"• Fallback Telegram: blocked | reason {reason}")
        return patched
    if status.get("fallback_selected"):
        lines.append("• Fallback Telegram: selected; отдельное сообщение должно быть отправлено, если guard не блокировал sendMessage")
    return lines


def diagnosis_lines_v4(summary: dict[str, Any], fallback: dict[str, Any], source_stats: dict[str, Any], refresh_plan: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    lines = _original_diagnosis_lines(summary, fallback, source_stats, refresh_plan, line_guard)
    status = _publication_status()
    if status.get("fallback_selected") and status.get("fallback_telegram_blocked"):
        reason = status.get("fallback_telegram_block_reason") or "guard_blocked"
        return [
            "📌 Вывод",
            "• Controlled fallback выбрал кандидата, но отдельное Telegram-сообщение не отправлено: финальный prepublish guard заблокировал sendMessage.",
            f"• Причина блокировки: {reason}.",
            "• Это правильное поведение: отчёт не должен считать такой run опубликованным, пока Telegram-пик реально не прошёл финальный guard.",
        ]
    return lines


base.published_candidates = published_candidates_v4
base.quick_status_lines = quick_status_lines_v4
base.diagnosis_lines = diagnosis_lines_v4


if __name__ == "__main__":
    raise SystemExit(base.main())
