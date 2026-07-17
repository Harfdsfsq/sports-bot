"""Discovery-first runtime preparation for run-bot.

A full provider discovery pass is useful, but it must not consume the 600-second
``run-once`` budget on every two-hour CronJob invocation. This version reuses a
fresh same-day full preparation, performs a light incremental validation on
regular runs, and bounds optional full-refresh steps so candidate construction
still has time to finish.
"""

from __future__ import annotations

import asyncio
import json
import os
import runpy
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "runbot-discovery-first-prepare.json"
TXT_OUT = OUT_DIR / "runbot-discovery-first-prepare.txt"
LATEST_JSON_OUT = OUT_DIR / "latest-runbot-discovery-first-prepare.json"
LATEST_TXT_OUT = OUT_DIR / "latest-runbot-discovery-first-prepare.txt"


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def truthy(name: str, default: bool = True) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = env(name)
        return int(float(raw)) if raw else default
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        raw = env(name)
        return float(raw) if raw else default
    except Exception:
        return default


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inventory_matches() -> int:
    best = 0
    for path in (
        Path(".data/day_inventory/latest.json"),
        Path(".data/day_inventory/current.json"),
        Path(".data/day_inventory/today.json"),
        Path(".data/day_inventory") / f"{env('DAY_INVENTORY_TARGET_DATE')}.json",
    ):
        payload = load(path)
        rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        best = max(best, len(rows))
    return best


def summarize_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: dict[str, Any] = {}
    for key in (
        "mode",
        "status",
        "inventory_matches",
        "inventory_matches_after",
        "canonical_rows_seen",
        "matched_existing",
        "appended",
        "matched_rows_seen",
        "applied",
        "crosswalk_matched",
        "request_count",
        "enriched_matches",
        "matrix_matches",
        "targets_selected",
        "contexts_matched",
        "rows_touched",
        "duplicate_rows_removed",
        "semantic_unique_matches",
        "semantic_duplicate_rows",
        "pool_keys",
        "targets_with_pool_id",
        "event_ids_hydrated",
        "contexts_added",
        "odds_hints_added",
        "matches_after",
        "rows_collected",
        "selected_from_collected",
        "target_shortfall",
    ):
        if key in result:
            out[key] = result.get(key)
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    for key in (
        "canonical_matches",
        "canonical_with_2plus_primary_sources",
        "canonical_with_all_3_primary_sources",
        "matched",
        "match_rate_pct",
    ):
        if key in summary:
            out[key] = summary.get(key)
    totals = result.get("totals") if isinstance(result.get("totals"), dict) else {}
    for key in (
        "matches",
        "fixture_2plus_sources",
        "odds_2plus_sources",
        "context_2plus_sources",
        "ready_for_model",
        "ready_for_publish",
    ):
        if key in totals:
            out[key] = totals.get(key)
    return out


def run_step_sync(name: str, fn: Callable[[], Any], *, required: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
        return {
            "name": name,
            "status": "ok",
            "duration_seconds": round(time.perf_counter() - started, 2),
            "result_summary": summarize_result(result),
        }
    except Exception as exc:
        if required:
            raise
        print(f"runbot discovery-first prepare step failed: {name}: {type(exc).__name__}: {exc}", flush=True)
        return {
            "name": name,
            "status": "error_ignored",
            "duration_seconds": round(time.perf_counter() - started, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_step_async(name: str, fn: Callable[[], Any], *, required: bool = False) -> dict[str, Any]:
    return run_step_sync(name, lambda: asyncio.run(fn()), required=required)


def skipped_step(name: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "skipped",
        "duration_seconds": 0.0,
        "reason": reason,
        "result_summary": {},
    }


def _run_script(path: str) -> Any:
    old_argv = sys.argv[:]
    try:
        sys.argv = [path]
        return runpy.run_path(path, run_name="__main__")
    finally:
        sys.argv = old_argv


def build_base_inventory_if_needed() -> dict[str, Any]:
    before = inventory_matches()
    target = env_int("DAY_INVENTORY_TARGET_SIZE", env_int("DAY_INVENTORY_MAX_MATCHES", 300))
    force = truthy("RUNBOT_DISCOVERY_FIRST_FORCE_BUILD_INVENTORY", False)
    min_rebuild = max(1, env_int("RUNBOT_DISCOVERY_FIRST_MIN_INVENTORY_ROWS", min(260, target)))
    if before >= min_rebuild and not force:
        return {
            "status": "skipped_sufficient_inventory",
            "before_matches": before,
            "min_rebuild": min_rebuild,
            "target": target,
        }

    steps: list[dict[str, Any]] = []
    for path in ("scripts/build_day_inventory_core_v3.py", "scripts/build_day_inventory.py"):
        started = time.perf_counter()
        try:
            _run_script(path)
            status = "ok"
            error = ""
        except SystemExit as exc:
            status = "ok" if int(exc.code or 0) == 0 else "non_zero"
            error = "" if status == "ok" else f"SystemExit:{exc.code}"
        except Exception as exc:
            status = "error_ignored"
            error = f"{type(exc).__name__}: {exc}"
        after_step = inventory_matches()
        steps.append(
            {
                "script": path,
                "status": status,
                "after_matches": after_step,
                "duration_seconds": round(time.perf_counter() - started, 2),
                "error": error,
            }
        )
        if after_step >= min_rebuild:
            break
    return {
        "status": "rebuilt_below_target",
        "before_matches": before,
        "after_matches": inventory_matches(),
        "target": target,
        "min_rebuild": min_rebuild,
        "force": force,
        "steps": steps,
    }


def build_source_aware_matrix() -> dict[str, Any]:
    from scripts import provider_smoke_coverage_matrix as base_matrix
    from scripts import provider_smoke_coverage_matrix_v3
    from scripts.provider_smoke_coverage_matrix_v5 import (
        _ORIG_SOURCE_COUNT,
        _patched_source_count,
    )

    base_matrix._source_count = _patched_source_count
    try:
        provider_smoke_coverage_matrix_v3.main()
    finally:
        base_matrix._source_count = _ORIG_SOURCE_COUNT
    return load(Path(".data/exports/provider-smoke-coverage-matrix.json"))


def _parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _target_date() -> str:
    return env("DAY_INVENTORY_TARGET_DATE")[:10]


def previous_full_prepare(now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    previous = load(LATEST_JSON_OUT)
    created = _parse_utc(previous.get("created_at_utc"))
    mode = str(previous.get("mode") or "")
    status = str(previous.get("status") or "")
    target = str(previous.get("target_date") or "")[:10]
    if not target and created is not None:
        target = created.date().isoformat()
    age_minutes = None if created is None else max(0.0, (current - created).total_seconds() / 60.0)
    is_full = bool(mode and "incremental" not in mode and status.startswith("ok"))
    same_target = not _target_date() or target == _target_date()
    fresh_for = max(1, env_int("RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES", 360))
    reusable = bool(
        is_full
        and same_target
        and age_minutes is not None
        and age_minutes <= fresh_for
        and inventory_matches() >= env_int("DAY_INVENTORY_TARGET_SIZE", 300)
    )
    return {
        "reusable": reusable,
        "created_at_utc": created.isoformat() if created is not None else None,
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "mode": mode,
        "status": status,
        "target_date": target,
        "refresh_interval_minutes": fresh_for,
    }


def _budget_seconds() -> float:
    return max(60.0, env_float("RUNBOT_DISCOVERY_FIRST_MAX_SECONDS", 240.0))


def _budget_allows(started: float, estimate_seconds: float) -> bool:
    elapsed = time.perf_counter() - started
    reserve = max(5.0, env_float("RUNBOT_DISCOVERY_FIRST_FINAL_RESERVE_SECONDS", 15.0))
    return elapsed + max(0.0, estimate_seconds) + reserve <= _budget_seconds()


def _maybe_expand(name: str, expand_fn: Callable[[], Any]) -> dict[str, Any]:
    target = env_int("DAY_INVENTORY_TARGET_SIZE", env_int("DAY_INVENTORY_MAX_MATCHES", 300))
    if inventory_matches() >= target:
        return skipped_step(name, f"inventory_already_at_or_above_target:{inventory_matches()}/{target}")
    return run_step_sync(name, expand_fn)


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# run-bot discovery-first prepare",
        f"status: {payload.get('status')}",
        f"mode: {payload.get('mode')}",
        f"created_at_utc: {payload.get('created_at_utc')}",
        f"duration_seconds: {payload.get('duration_seconds')}",
        f"budget_seconds: {payload.get('budget_seconds')}",
        "",
        "## Steps",
    ]
    for step in payload.get("steps") or []:
        lines.append(
            f"- {step.get('name')}: {step.get('status')} "
            f"{json.dumps(step.get('result_summary') or {}, ensure_ascii=False)}"
        )
        if step.get("reason"):
            lines.append(f"  - reason: {step.get('reason')}")
        if step.get("error"):
            lines.append(f"  - error: {step.get('error')}")
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    if final:
        lines += ["", "## Final"]
        for key, value in final.items():
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    now = datetime.now(UTC)
    if not truthy("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED", True):
        payload = {
            "created_at_utc": now.isoformat(),
            "target_date": _target_date(),
            "status": "disabled",
            "duration_seconds": 0,
            "steps": [],
        }
        write(JSON_OUT, payload)
        write(LATEST_JSON_OUT, payload)
        text = render(payload)
        TXT_OUT.write_text(text, encoding="utf-8")
        LATEST_TXT_OUT.write_text(text, encoding="utf-8")
        print(text)
        return 0

    from scripts import (
        apply_provider_day_discovery_to_inventory,
        apply_sstats_crosswalk_to_inventory,
        apply_sstats_deep_inventory_enrichment_v4,
        build_inventory_provider_gap_audit,
        deduplicate_day_inventory_semantic,
        enrich_inventory_bzzoiro_v2_targets,
        expand_day_inventory_to_target,
        provider_day_discovery_canonical_pool_v2,
        sstats_crosswalk_probe_v2,
    )

    previous = previous_full_prepare(now)
    force_full = truthy("RUNBOT_DISCOVERY_FIRST_FORCE_FULL_REFRESH", False)
    incremental = bool(previous.get("reusable")) and not force_full
    mode = (
        "runbot_discovery_first_prepare_v5_incremental_reuse"
        if incremental
        else "runbot_discovery_first_prepare_v5_full_bounded"
    )

    steps: list[dict[str, Any]] = []
    steps.append(run_step_sync("build_base_inventory_if_needed", build_base_inventory_if_needed))
    steps.append(run_step_sync("semantic_inventory_dedupe_pre", deduplicate_day_inventory_semantic.main))
    steps.append(_maybe_expand("target_expand_pre_discovery", expand_day_inventory_to_target.main))

    budget_limited = False
    if incremental:
        steps.append(skipped_step("provider_day_discovery_canonical_pool_v2", "fresh_same_day_full_prepare_reused"))
        steps.append(skipped_step("merge_discovery_pool_into_inventory", "fresh_same_day_full_prepare_reused"))
        steps.append(run_step_async("apply_cached_sstats_crosswalk_ids", apply_sstats_crosswalk_to_inventory.run))
        if truthy("RUNBOT_INCREMENTAL_DEEP_ENRICHMENT_ENABLED", False) and _budget_allows(started, 55.0):
            steps.append(
                run_step_async("apply_sstats_deep_inventory_enrichment_v4", apply_sstats_deep_inventory_enrichment_v4.run)
            )
        else:
            steps.append(skipped_step("apply_sstats_deep_inventory_enrichment_v4", "incremental_mode_cached_context_reuse"))
        if truthy("RUNBOT_INCREMENTAL_BZZOIRO_GAP_ENRICHMENT_ENABLED", False) and _budget_allows(started, 35.0):
            steps.append(run_step_sync("target_bzzoiro_v2_inventory_gaps", enrich_inventory_bzzoiro_v2_targets.main))
        else:
            steps.append(skipped_step("target_bzzoiro_v2_inventory_gaps", "incremental_mode_runner_provider_refresh"))
    else:
        if _budget_allows(started, 120.0):
            discovery = run_step_async(
                "provider_day_discovery_canonical_pool_v2",
                provider_day_discovery_canonical_pool_v2.run,
            )
            steps.append(discovery)
        else:
            budget_limited = True
            discovery = skipped_step("provider_day_discovery_canonical_pool_v2", "discovery_budget_reserve")
            steps.append(discovery)

        if discovery.get("status") == "ok" and _budget_allows(started, 75.0):
            steps.append(
                run_step_async("merge_discovery_pool_into_inventory", apply_provider_day_discovery_to_inventory.run)
            )
            steps.append(run_step_sync("semantic_inventory_dedupe_post_merge", deduplicate_day_inventory_semantic.main))
        else:
            budget_limited = True
            steps.append(skipped_step("merge_discovery_pool_into_inventory", "discovery_missing_or_budget_reserve"))

        if _budget_allows(started, 45.0):
            steps.append(run_step_async("post_merge_sstats_crosswalk_v2", sstats_crosswalk_probe_v2.run))
        else:
            budget_limited = True
            steps.append(skipped_step("post_merge_sstats_crosswalk_v2", "discovery_budget_reserve"))
        steps.append(run_step_async("apply_sstats_crosswalk_ids_to_inventory", apply_sstats_crosswalk_to_inventory.run))

        if _budget_allows(started, 55.0):
            steps.append(
                run_step_async("apply_sstats_deep_inventory_enrichment_v4", apply_sstats_deep_inventory_enrichment_v4.run)
            )
        else:
            budget_limited = True
            steps.append(skipped_step("apply_sstats_deep_inventory_enrichment_v4", "discovery_budget_reserve"))

        if _budget_allows(started, 35.0):
            steps.append(run_step_sync("target_bzzoiro_v2_inventory_gaps", enrich_inventory_bzzoiro_v2_targets.main))
        else:
            budget_limited = True
            steps.append(skipped_step("target_bzzoiro_v2_inventory_gaps", "discovery_budget_reserve"))

    steps.append(run_step_sync("semantic_inventory_dedupe_final", deduplicate_day_inventory_semantic.main))
    steps.append(_maybe_expand("target_expand_final", expand_day_inventory_to_target.main))
    steps.append(run_step_sync("build_source_aware_coverage_matrix", build_source_aware_matrix))
    steps.append(run_step_sync("inventory_provider_gap_audit", build_inventory_provider_gap_audit.main))

    matrix = load(Path(".data/exports/provider-smoke-coverage-matrix.json"))
    totals = matrix.get("totals") if isinstance(matrix.get("totals"), dict) else {}
    audit = load(Path(".data/exports/latest-inventory-provider-gap-audit.json"))
    final = {
        "inventory_matches": inventory_matches(),
        "semantic_unique_matches": audit.get("semantic_unique_matches"),
        "semantic_duplicate_rows": audit.get("semantic_duplicate_rows"),
        "matrix_matches": matrix.get("matrix_matches"),
        "fixture_2plus_sources": totals.get("fixture_2plus_sources"),
        "odds_2plus_sources": totals.get("odds_2plus_sources"),
        "context_2plus_sources": totals.get("context_2plus_sources"),
        "ready_for_model": totals.get("ready_for_model"),
        "ready_for_publish": totals.get("ready_for_publish"),
    }
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "target_date": _target_date(),
        "mode": mode,
        "status": "ok_budget_limited" if budget_limited else "ok",
        "duration_seconds": round(time.perf_counter() - started, 2),
        "budget_seconds": _budget_seconds(),
        "budget_limited": budget_limited,
        "previous_full_prepare": previous,
        "steps": steps,
        "final": final,
    }
    write(JSON_OUT, payload)
    write(LATEST_JSON_OUT, payload)
    text = render(payload)
    TXT_OUT.write_text(text, encoding="utf-8")
    LATEST_TXT_OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
