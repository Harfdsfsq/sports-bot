from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".data" / "exports" / "latest-controlled-fallback-runtime-preflight.json"


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _local_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit[:10]
    try:
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    return datetime.now(UTC).astimezone(tz).date().isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_step(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    started = datetime.now(UTC)
    item: dict[str, Any] = {"name": name, "started_at_utc": started.isoformat(), "status": "starting"}
    try:
        result = fn()
        if isinstance(result, int):
            item["exit_code"] = result
            item["status"] = "ok" if result == 0 else "non_zero"
        else:
            item["status"] = "ok"
            if result is not None:
                item["result"] = str(result)[:500]
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        item["exit_code"] = code
        item["status"] = "ok" if code == 0 else "system_exit_non_zero"
    except Exception as exc:
        item["status"] = "error"
        item["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        item["finished_at_utc"] = datetime.now(UTC).isoformat()
        item["duration_seconds"] = round((datetime.now(UTC) - started).total_seconds(), 3)
    return item


def _module_main(module_name: str) -> Callable[[], Any]:
    def run() -> Any:
        module = importlib.import_module(module_name)
        return module.main()
    return run


def run_preflight() -> dict[str, Any]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_RUNTIME_PREFLIGHT_ENABLED"), True):
        payload = {"status": "disabled", "created_at_utc": datetime.now(UTC).isoformat(), "steps": []}
        _write_json(OUT, payload)
        return payload

    day = _local_date()
    previous_day = os.getenv("DAY_INVENTORY_TARGET_DATE")
    os.environ["DAY_INVENTORY_TARGET_DATE"] = day
    os.environ.setdefault("DAY_INVENTORY_CACHE_DATE", day)
    steps = [
        _run_step("runtime_json_state_guard", _module_main("scripts.runtime_json_state_guard")),
        _run_step("normalize_day_inventory_time_windows", _module_main("scripts.normalize_day_inventory_time_windows")),
        _run_step("backfill_inventory_bookmaker_coverage", _module_main("scripts.backfill_inventory_bookmaker_coverage")),
        _run_step("build_b_cover_candidate_gap_report", _module_main("scripts.build_b_cover_candidate_gap_report")),
        _run_step("promote_a_cover_value_candidates", _module_main("scripts.promote_a_cover_value_candidates")),
        _run_step("update_day_inventory_priority_and_line_state", _module_main("scripts.update_day_inventory_priority_and_line_state")),
        _run_step("build_a_cover_candidate_gap_report", _module_main("scripts.build_a_cover_candidate_gap_report")),
    ]
    payload = {
        "status": "ok" if all(step.get("status") in {"ok", "disabled"} for step in steps) else "completed_with_errors",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "date_local": day,
        "previous_day_env": previous_day,
        "steps": steps,
    }
    _write_json(OUT, payload)
    return payload


def main() -> int:
    run_preflight()
    from scripts.publish_controlled_fallback_guarded_v18 import main as v18_main

    return int(v18_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
