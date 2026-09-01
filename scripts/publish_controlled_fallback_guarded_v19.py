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


def _int_env(name: str, default: int) -> int:
    try:
        value = os.getenv(name)
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _positive_env(name: str) -> int | None:
    """Return an explicitly configured positive integer, otherwise ``None``.

    An explicit production cap is a hard operator decision. Runtime preflight may
    supply a default only when that decision is absent; it must never raise a
    smaller positive value to an internal target.
    """
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        value = int(float(str(raw).strip()))
    except Exception:
        return None
    return value if value > 0 else None


def _local_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit[:10]
    try:
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    return datetime.now(UTC).astimezone(tz).date().isoformat()


def _apply_daily_slot_target_policy() -> dict[str, Any]:
    if _truthy(os.getenv("CONTROLLED_FALLBACK_DISABLE_DAILY_CAP_FLOOR"), False):
        return {"status": "disabled"}

    default_limit = max(
        1,
        _int_env(
            "CONTROLLED_FALLBACK_DAILY_MAX_FLOOR",
            _int_env("HARIZON_TARGET_DAILY_MAX_PICKS", 5),
        ),
    )
    before = {
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"),
        "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"),
        "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS": os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS"),
    }

    explicit_published = _positive_env("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED")
    explicit_b_tier = _positive_env("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER")

    if explicit_published is None:
        os.environ["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"] = str(default_limit)
        effective_published = default_limit
    else:
        effective_published = explicit_published

    if explicit_b_tier is None:
        # When the total cap is explicitly configured, use the same value for the
        # missing B-tier cap rather than silently creating a wider five-pick lane.
        os.environ["CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"] = str(effective_published)
        effective_b_tier = effective_published
    else:
        effective_b_tier = explicit_b_tier

    os.environ.setdefault("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS", "1")
    after = {
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"),
        "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"),
        "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS": os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS"),
    }
    return {
        "status": "ok",
        # Keep the legacy field for report readers, but make its new semantics
        # explicit: this is a default, not a lower bound over operator config.
        "floor": default_limit,
        "default_limit": default_limit,
        "before": before,
        "after": after,
        "explicit_published_preserved": explicit_published is not None,
        "explicit_b_tier_preserved": explicit_b_tier is not None,
        "effective_published_limit": effective_published,
        "effective_b_tier_limit": effective_b_tier,
        "policy": "positive explicit daily caps are authoritative; internal target is applied only when a cap is unset or invalid",
    }


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
    daily_slot_policy = _apply_daily_slot_target_policy()
    steps = [
        _run_step("runtime_json_state_guard", _module_main("scripts.runtime_json_state_guard")),
        _run_step("normalize_persisted_publication_indexes", _module_main("scripts.normalize_persisted_publication_indexes")),
        _run_step("normalize_day_inventory_time_windows", _module_main("scripts.normalize_day_inventory_time_windows")),
        _run_step("backfill_inventory_bookmaker_coverage", _module_main("scripts.backfill_inventory_bookmaker_coverage")),
        _run_step("extend_day_inventory_for_target_shortfall", _module_main("scripts.extend_day_inventory_for_target_shortfall")),
        _run_step("repair_day_inventory_blank_rows", _module_main("scripts.repair_day_inventory_blank_rows")),
        _run_step("build_b_cover_candidate_gap_report", _module_main("scripts.build_b_cover_candidate_gap_report")),
        _run_step("promote_a_cover_value_candidates", _module_main("scripts.promote_a_cover_value_candidates")),
        _run_step("normalize_rescue_candidate_keys", _module_main("scripts.normalize_rescue_candidate_keys")),
        _run_step("enrich_rescue_candidates_xg_confirmation", _module_main("scripts.enrich_rescue_candidates_xg_confirmation")),
        _run_step("replace_rescue_proxy_placeholder_xg", _module_main("scripts.replace_rescue_proxy_placeholder_xg")),
        _run_step("update_day_inventory_priority_and_line_state_safe_clock", _module_main("scripts.update_day_inventory_priority_and_line_state_safe_clock")),
        _run_step("build_a_cover_candidate_gap_report", _module_main("scripts.build_a_cover_candidate_gap_report")),
    ]
    payload = {
        "status": "ok" if all(step.get("status") in {"ok", "disabled"} for step in steps) else "completed_with_errors",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "date_local": day,
        "previous_day_env": previous_day,
        "daily_slot_policy": daily_slot_policy,
        "steps": steps,
    }
    _write_json(OUT, payload)
    return payload
