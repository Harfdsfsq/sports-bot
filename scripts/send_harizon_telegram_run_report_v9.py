from __future__ import annotations

"""HARIZON Telegram run report v9.

v9 is a renderer fix on top of v8:
- do not classify candidate source counters as pre-fallback filters;
- if controlled fallback actually evaluated candidates, never say that fallback did
  not evaluate them;
- keep real lifecycle filters such as stale/outside-window/not-in-inventory/prefilter;
- support mixed pools: some candidates evaluated, some filtered before evaluation;
- show a more precise conclusion when value/xG/quality guards are the real reason.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"
RUN_LOG_PATH = EXPORT_DIR / "latest-run-bot.log"


def _load_v8() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v8", V8_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V8_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v8 = _load_v8()

SOURCE_POOL_KEYS = {
    "debug_candidates_before_quality",
    "latest_rescue_candidates",
    "artifact_rescue_candidates",
    "candidates_before_quality",
    "passed_candidates",
    "publishable_candidates",
}
FILTER_SUFFIXES = (
    "_not_in_day_inventory",
    "_stale_or_outside_window",
    "_canonical_negative_value_prefilter",
    "_prefilter",
    "_stale_payload",
)


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass



def _runtime_error_from_log() -> dict[str, Any]:
    """Return a fatal runtime error only for top-level run failures.

    Discovery/preflight helper scripts can log non-fatal lines such as
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``.
    The production run may still complete, build candidates, and run controlled
    fallback.  v9 must not turn those warnings into a red "runtime failed"
    report.
    """
    try:
        if not RUN_LOG_PATH.exists() or RUN_LOG_PATH.stat().st_size <= 0:
            return {}
        text = RUN_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}

    fatal_markers = (
        "Traceback (most recent call last)",
        "AttributeError: 'RuntimePreflight' object has no attribute 'apply_phase_policy'",
        "Usage: python -m app.cli run-once",
    )
    if not any(marker in text for marker in fatal_markers):
        return {}

    # If fallback/candidate artifacts prove that the pipeline reached evaluation,
    # treat helper-script tracebacks as non-fatal unless the specific old
    # apply_phase_policy crash is present.
    fallback = _load_json(EXPORT_DIR / "latest-controlled-fallback-report.json")
    fallback_seen = _as_int(fallback.get("candidates_seen")) or len(fallback.get("evaluated") or [])
    debug = _load_json(Path(".logs/debug-last-run.json"))
    debug_summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else {}
    matches_seen = _as_int(debug_summary.get("matches_seen") or debug_summary.get("matches_in_run"))
    old_preflight_crash = "RuntimePreflight" in text and "apply_phase_policy" in text and "AttributeError:" in text
    if not old_preflight_crash and (fallback_seen > 0 or matches_seen > 0):
        return {}

    reason = "runtime_error"
    if old_preflight_crash:
        reason = "runtime_preflight_apply_phase_policy_missing"
    idx = text.find("Traceback (most recent call last)")
    if idx < 0:
        for marker in fatal_markers:
            idx = text.find(marker)
            if idx >= 0:
                break
    excerpt = text[idx: idx + 2200] if idx >= 0 else text[-2200:]
    return {
        "status": "error",
        "reason": reason,
        "excerpt": excerpt,
        "log_path": str(RUN_LOG_PATH),
    }


def is_real_pool_filter(key: str) -> bool:
    name = str(key or "").strip()
    if not name or name == "day_inventory_membership_keys":
        return False
    if name.endswith("_duplicate_in_pool"):
        return False
    if name in SOURCE_POOL_KEYS:
        return False
    return name.endswith(FILTER_SUFFIXES)


def pool_filter_counts(pool_counts: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(pool_counts, dict):
        return out
    for key, value in pool_counts.items():
        count = _as_int(value)
        if count > 0 and is_real_pool_filter(str(key)):
            out[str(key)] = count
    return out


def build_payload() -> dict[str, Any]:
    payload = v8.build_payload()
    payload["version"] = "harizon-telegram-report-v9-pool-filter-classifier"
    payload["report_renderer"] = "v9"
    diag = payload.setdefault("diagnostics", {})
    runtime_error = _runtime_error_from_log()
    if runtime_error:
        diag["runtime_error"] = runtime_error
        payload["status"] = "runtime_failed"
        payload["status_ru"] = "🔴 runtime упал до построения полного пайплайна"
        payload["top_reason"] = runtime_error.get("reason") or "runtime_error"
        payload["reasons"] = [{
            "reason": payload["top_reason"],
            "reason_ru": "ошибка runtime до полного построения кандидатов",
            "count": 1,
        }]
    fallback = _load_json(EXPORT_DIR / "latest-controlled-fallback-report.json")
    pool_counts = fallback.get("pool_counts") if isinstance(fallback.get("pool_counts"), dict) else {}
    real_filters = pool_filter_counts(pool_counts)
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    seen = max(_as_int(funnel.get("fallback_candidates_seen")), _as_int(fallback.get("candidates_seen")))
    evaluated = max(_as_int(funnel.get("fallback_evaluated")), len(fallback.get("evaluated") or []))

    if pool_counts:
        diag["controlled_fallback_pool_counts"] = dict(pool_counts)
        diag["controlled_fallback_pool_filter_counts"] = dict(real_filters)
        diag["controlled_fallback_pool_source_counts"] = {
            k: _as_int(v)
            for k, v in pool_counts.items()
            if k != "day_inventory_membership_keys" and not is_real_pool_filter(str(k)) and _as_int(v) > 0
        }
        diag["controlled_fallback_pool_membership_keys"] = _as_int(pool_counts.get("day_inventory_membership_keys"))
        diag["controlled_fallback_evaluated_count"] = evaluated

    if seen > 0 or evaluated > 0:
        # Candidates were evaluated; source counters are not pre-evaluation filters.
        if payload.get("status") == "candidates_filtered_before_fallback":
            payload["status"] = "candidates_but_quality_rejected"
            payload["status_ru"] = "🟡 кандидаты есть, quality/value не пропустили"
        # Keep real filters for mixed pools, but render them as "also filtered",
        # never as "fallback did not evaluate".
        diag["controlled_fallback_pool_filter_counts"] = dict(real_filters)

    _write_json(STATUS_PATH, {"status": "installed", "renderer": "v9", "pool_filter_classifier": True})
    return payload


def _reason_ru(reason: str) -> str:
    if reason.endswith("_not_in_day_inventory"):
        return "кандидат не входит в frozen day inventory"
    if reason.endswith("_stale_or_outside_window"):
        return "кандидат вне окна публикации или устарел"
    if reason.endswith("_canonical_negative_value_prefilter") or reason.endswith("_prefilter"):
        return "отрицательная контрольная ценность до fallback"
    return reason.replace("_", " ")


def _strip_controlled_pool_block(text: str) -> str:
    pattern = r"\n\n🧯 Controlled fallback pool filter\n(?:.+\n)*?(?=\n📌 Вывод|\n🧩 CandidateFactory diagnostics|\Z)"
    return re.sub(pattern, "\n\n", text, flags=re.MULTILINE)


def _pool_info_block(payload: dict[str, Any]) -> str:
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    source_counts = diag.get("controlled_fallback_pool_source_counts") if isinstance(diag.get("controlled_fallback_pool_source_counts"), dict) else {}
    filters = diag.get("controlled_fallback_pool_filter_counts") if isinstance(diag.get("controlled_fallback_pool_filter_counts"), dict) else {}
    evaluated = _as_int(diag.get("controlled_fallback_evaluated_count"))
    membership = _as_int(diag.get("controlled_fallback_pool_membership_keys"))

    lines: list[str] = []
    if source_counts or filters or membership:
        title = "🧯 Controlled fallback pool"
        lines.append(title)
    if membership:
        lines.append(f"• Frozen day inventory keys: {membership}")
    if source_counts:
        lines.append("• Pool sources: " + ", ".join(f"{k}: {_as_int(v)}" for k, v in sorted(source_counts.items())[:6]))
        lines.append("• Смысл: это источники пула, а не причины отказа.")
    if filters:
        top = sorted(filters.items(), key=lambda item: _as_int(item[1]), reverse=True)[:6]
        prefix = "Также отфильтровано до fallback" if evaluated > 0 else "Pre-evaluation filters"
        lines.append("• " + prefix + ": " + ", ".join(f"{_reason_ru(k)}: {_as_int(v)}" for k, v in top))
        if evaluated > 0:
            lines.append("• Смысл: часть кандидатов fallback реально оценил; отдельные строки были отсеяны до evaluation.")
        else:
            lines.append("• Смысл: raw-кандидат был найден, но fallback не оценивал его, потому что он не прошёл lifecycle/inventory-фильтр до финальной проверки.")
    return "\n".join(lines)


def _patch_conclusion(text: str, payload: dict[str, Any]) -> str:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    reason_text = " ".join(str((r or {}).get("reason") if isinstance(r, dict) else r) for r in reasons).lower()
    if any(token in reason_text for token in ("quality", "edge", "ev", "xg", "tier", "telegram_publish_odds_sources")):
        text = text.replace(
            "• Нужно смотреть candidate factory/mapping: линии и контекст есть, но кандидаты не дошли до проверки.",
            "• Candidate pipeline работает: резерв проверял кандидатов, но value/xG/quality/coverage guards не разрешили публикацию."
        )
        text = text.replace(
            "• Progressive coverage считает только active core-contract текущего запуска. Главный gap сейчас — добор Bzzoiro/SStats и второго odds source на ближайшие окна, а не ослабление guards.",
            "• Candidate pipeline работает: резерв проверял кандидатов, но value/xG/quality/coverage guards не разрешили публикацию."
        )
    return text



def _runtime_error_block(payload: dict[str, Any]) -> str:
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    err = diag.get("runtime_error") if isinstance(diag.get("runtime_error"), dict) else {}
    if not err:
        return ""
    reason = str(err.get("reason") or "runtime_error")
    excerpt = str(err.get("excerpt") or "").replace("\r", "")
    # Show only the most useful traceback lines.
    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    useful = []
    for line in lines:
        if any(token in line for token in ("Traceback", "File ", "AttributeError", "RuntimeError", "Exception")):
            useful.append(line)
        if len(useful) >= 6:
            break
    return "\n".join([
        "🚨 Runtime error",
        f"• Причина: {reason}",
        "• Важно: этот run не дошёл до полного CandidateFactory/fallback цикла; coverage/ledger ниже могут быть построены по cached inventory.",
        "• Trace: " + " | ".join(useful[:6]) if useful else "• Trace: см. latest-run-bot.log",
    ])


def render(payload: dict[str, Any]) -> str:
    text = v8.render(payload).replace("HARIZON run report v8", "HARIZON run report v9")
    runtime_block = _runtime_error_block(payload)
    if runtime_block and "\n📦 Покрытие" in text:
        text = text.replace("\n📦 Покрытие", "\n\n" + runtime_block + "\n\n📦 Покрытие", 1)
    text = _strip_controlled_pool_block(text)
    block = _pool_info_block(payload)
    if block and "\n📌 Вывод" in text:
        text = text.replace("\n📌 Вывод", "\n\n" + block + "\n\n📌 Вывод", 1)
    text = _patch_conclusion(text, payload)
    return text


def main() -> int:
    payload = build_payload()
    text = render(payload)
    payload["text_length"] = len(text)
    payload["telegram_sent"] = False
    payload["report_renderer"] = "v9"
    payload["report_renderer_status"] = "direct_v9_main"
    try:
        v8.v7.v5.write_json(v8.v7.v5.OUT_V5_JSON, payload)
        v8.v7.v5.write_text(v8.v7.v5.OUT_V5_TXT, text + "\n")
        v8.v7.v5.write_json(v8.v7.v5.OUT_JSON, payload)
        v8.v7.v5.write_text(v8.v7.v5.OUT_TXT, text + "\n")
        result = v8.v7.v5.send_telegram(text)
        payload["telegram_sent"] = bool(result.get("sent")) if isinstance(result, dict) else False
        payload["telegram_result"] = result
        v8.v7.v5.write_json(v8.v7.v5.OUT_V5_JSON, payload)
        v8.v7.v5.write_json(v8.v7.v5.OUT_JSON, payload)
        _write_json(STATUS_PATH, {"status": "sent", "renderer": "v9", "payload_version": payload.get("version")})
        print(text)
        return 0
    except Exception as exc:
        _write_json(STATUS_PATH, {"status": "error", "renderer": "v9", "error": f"{type(exc).__name__}: {exc}"})
        print(text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
