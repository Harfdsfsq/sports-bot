from __future__ import annotations

"""HARIZON Telegram run report v9.

v9 is a small renderer fix on top of v8:
- do not classify candidate source counters as pre-fallback filters;
- if controlled fallback actually evaluated candidates, hide the pool-filter block;
- keep real lifecycle filters such as stale/outside-window/not-in-inventory/prefilter;
- show a more precise conclusion when quality/value guards are the real reason.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"


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
    fallback = _load_json(EXPORT_DIR / "latest-controlled-fallback-report.json")
    pool_counts = fallback.get("pool_counts") if isinstance(fallback.get("pool_counts"), dict) else {}
    real_filters = pool_filter_counts(pool_counts)
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    seen = _as_int(funnel.get("fallback_candidates_seen"))
    evaluated = _as_int(funnel.get("fallback_evaluated"))
    if pool_counts:
        diag["controlled_fallback_pool_counts"] = dict(pool_counts)
        diag["controlled_fallback_pool_filter_counts"] = dict(real_filters)
        diag["controlled_fallback_pool_source_counts"] = {
            k: _as_int(v) for k, v in pool_counts.items()
            if k != "day_inventory_membership_keys" and not is_real_pool_filter(str(k))
        }
    if seen > 0 or evaluated > 0:
        # Candidates were evaluated; source counters are not pre-evaluation filters.
        if payload.get("status") == "candidates_filtered_before_fallback":
            payload["status"] = "candidates_but_quality_rejected"
            payload["status_ru"] = "🟡 кандидаты есть, quality/value не пропустили"
        if not real_filters:
            diag["controlled_fallback_pool_filter_counts"] = {}
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


def _insert_pool_source_note(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    source_counts = diag.get("controlled_fallback_pool_source_counts") if isinstance(diag.get("controlled_fallback_pool_source_counts"), dict) else {}
    if not source_counts:
        return text
    compact = {k: _as_int(v) for k, v in source_counts.items() if _as_int(v) > 0}
    if not compact:
        return text
    line = "• Pool sources: " + ", ".join(f"{k}: {v}" for k, v in sorted(compact.items())[:6])
    # Put it after the reserve/funnel block only if there is no filter block.
    marker = "\n📌 Вывод"
    if marker in text and "🧯 Controlled fallback pool filter" not in text:
        return text.replace(marker, "\n\n🧯 Controlled fallback pool\n" + line + "\n• Смысл: это источники пула, а не причины отказа.\n" + marker, 1)
    return text


def _patch_conclusion(text: str, payload: dict[str, Any]) -> str:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    reason_text = " ".join(str((r or {}).get("reason") if isinstance(r, dict) else r) for r in reasons).lower()
    if any(token in reason_text for token in ("quality", "edge", "ev", "xg", "tier")):
        text = text.replace(
            "• Нужно смотреть candidate factory/mapping: линии и контекст есть, но кандидаты не дошли до проверки.",
            "• Candidate pipeline работает: резерв проверял кандидатов, но value/xG/quality не разрешили публикацию."
        )
    return text


def render(payload: dict[str, Any]) -> str:
    text = v8.render(payload).replace("HARIZON run report v8", "HARIZON run report v9")
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    filters = diag.get("controlled_fallback_pool_filter_counts") if isinstance(diag.get("controlled_fallback_pool_filter_counts"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    if _as_int(funnel.get("fallback_candidates_seen")) > 0 or _as_int(funnel.get("fallback_evaluated")) > 0:
        if not filters:
            text = _strip_controlled_pool_block(text)
            text = _insert_pool_source_note(text, payload)
    elif filters:
        # Keep only real filters in the block.
        top = sorted(filters.items(), key=lambda item: _as_int(item[1]), reverse=True)[:6]
        block = "\n".join([
            "🧯 Controlled fallback pool filter",
            "• Pre-evaluation filters: " + ", ".join(f"{_reason_ru(k)}: {_as_int(v)}" for k, v in top),
            "• Смысл: raw-кандидат был найден, но fallback не оценивал его, потому что он не прошёл lifecycle/inventory-фильтр до финальной проверки.",
        ])
        text = _strip_controlled_pool_block(text)
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
        print(text)
        return 0
    except Exception as exc:
        _write_json(STATUS_PATH, {"status": "error", "renderer": "v9", "error": f"{type(exc).__name__}: {exc}"})
        print(text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
