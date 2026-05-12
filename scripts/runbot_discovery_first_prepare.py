from __future__ import annotations

"""Discovery-first runtime preparation for run-bot.

This is the production-facing version of the provider-smoke repair stack. It
prepares the day inventory before `app.cli run-once` so the model starts from a
canonical 300-match pool with provider source_ids and primary-provider context.

Pipeline:
1. Build base day inventory if needed.
2. Build pre-merge SStats crosswalk for cached SStats discovery.
3. Build provider-day canonical discovery pool from odds-api.io/Bzzoiro/SStats
   and supplemental fixture providers.
4. Merge the full canonical pool into day_inventory.
5. Rebuild SStats crosswalk on the merged inventory.
6. Apply SStats ids to inventory rows.
7. Apply actual SStats deep enrichment.
8. Build source-aware coverage matrix for diagnostics.

The script is fail-soft by default: it should improve the run when providers are
healthy, but it should not block `run-once` if one repair layer fails.
"""

import asyncio
import json
import os
import runpy
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts import apply_provider_day_discovery_to_inventory
from scripts import apply_sstats_crosswalk_to_inventory
from scripts import apply_sstats_deep_inventory_enrichment_v4
from scripts import provider_day_discovery_canonical_pool_v2
from scripts import provider_smoke_coverage_matrix as base_matrix
from scripts import provider_smoke_coverage_matrix_v3
from scripts.provider_smoke_coverage_matrix_v5 import _ORIG_SOURCE_COUNT, _patched_source_count
from scripts import sstats_crosswalk_probe_v2

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "runbot-discovery-first-prepare.json"
TXT_OUT = OUT_DIR / "runbot-discovery-first-prepare.txt"


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def truthy(name: str, default: bool = True) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inventory_matches() -> int:
    for path in (Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")):
        payload = load(path)
        rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        if rows:
            return len(rows)
    return 0


def run_step_sync(name: str, fn: Callable[[], Any], *, required: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
        return {"name": name, "status": "ok", "duration_seconds": round(time.perf_counter() - started, 2), "result_summary": summarize_result(result)}
    except Exception as exc:
        status = "error_required" if required else "error_ignored"
        if required:
            raise
        print(f"runbot discovery-first prepare step failed: {name}: {type(exc).__name__}: {exc}", flush=True)
        return {"name": name, "status": status, "duration_seconds": round(time.perf_counter() - started, 2), "error": f"{type(exc).__name__}: {exc}"}


def run_step_async(name: str, fn: Callable[[], Any], *, required: bool = False) -> dict[str, Any]:
    return run_step_sync(name, lambda: asyncio.run(fn()), required=required)


def summarize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        out: dict[str, Any] = {}
        for key in ("mode", "status", "inventory_matches", "inventory_matches_after", "canonical_rows_seen", "matched_existing", "appended", "matched_rows_seen", "applied", "crosswalk_matched", "request_count", "enriched_matches", "matrix_matches"):
            if key in result:
                out[key] = result.get(key)
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else None
        if summary:
            for key in ("canonical_matches", "canonical_with_2plus_primary_sources", "canonical_with_all_3_primary_sources", "matched", "match_rate_pct"):
                if key in summary:
                    out[key] = summary.get(key)
        totals = result.get("totals") if isinstance(result.get("totals"), dict) else None
        if totals:
            for key in ("matches", "fixture_2plus_sources", "odds_2plus_sources", "context_2plus_sources", "ready_for_model", "ready_for_publish"):
                if key in totals:
                    out[key] = totals.get(key)
        return out
    return {"type": type(result).__name__}


def build_base_inventory_if_needed() -> dict[str, Any]:
    before = inventory_matches()
    force = truthy("RUNBOT_DISCOVERY_FIRST_FORCE_BUILD_INVENTORY", False)
    if before > 0 and not force:
        return {"status": "skipped_existing_inventory", "before_matches": before}
    old_argv = sys.argv[:]
    try:
        sys.argv = ["build_day_inventory.py"]
        runpy.run_path("scripts/build_day_inventory.py", run_name="__main__")
    finally:
        sys.argv = old_argv
    return {"status": "built", "before_matches": before, "after_matches": inventory_matches()}


def build_source_aware_matrix() -> dict[str, Any]:
    base_matrix._source_count = _patched_source_count
    try:
        provider_smoke_coverage_matrix_v3.main()
    finally:
        base_matrix._source_count = _ORIG_SOURCE_COUNT
    return load(Path(".data/exports/provider-smoke-coverage-matrix.json"))


async def async_noop() -> dict[str, str]:
    return {"status": "noop"}


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# run-bot discovery-first prepare",
        f"status: {payload.get('status')}",
        f"created_at_utc: {payload.get('created_at_utc')}",
        f"duration_seconds: {payload.get('duration_seconds')}",
        "",
        "## Steps",
    ]
    for step in payload.get("steps") or []:
        lines.append(f"- {step.get('name')}: {step.get('status')} {json.dumps(step.get('result_summary') or {}, ensure_ascii=False)}")
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
    if not truthy("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED", True):
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "disabled", "duration_seconds": 0, "steps": []}
        write(JSON_OUT, payload)
        TXT_OUT.write_text(render(payload), encoding="utf-8")
        print(render(payload))
        return 0

    steps: list[dict[str, Any]] = []
    steps.append(run_step_sync("build_base_inventory_if_needed", build_base_inventory_if_needed))
    steps.append(run_step_async("pre_merge_sstats_crosswalk_v2", sstats_crosswalk_probe_v2.run))
    steps.append(run_step_async("provider_day_discovery_canonical_pool_v2", provider_day_discovery_canonical_pool_v2.run))
    steps.append(run_step_async("merge_discovery_pool_into_inventory", apply_provider_day_discovery_to_inventory.run))
    steps.append(run_step_async("post_merge_sstats_crosswalk_v2", sstats_crosswalk_probe_v2.run))
    steps.append(run_step_async("apply_sstats_crosswalk_ids_to_inventory", apply_sstats_crosswalk_to_inventory.run))
    steps.append(run_step_async("apply_sstats_deep_inventory_enrichment_v4", apply_sstats_deep_inventory_enrichment_v4.run))
    steps.append(run_step_sync("build_source_aware_coverage_matrix", build_source_aware_matrix))

    matrix = load(Path(".data/exports/provider-smoke-coverage-matrix.json"))
    totals = matrix.get("totals") if isinstance(matrix.get("totals"), dict) else {}
    final = {
        "inventory_matches": inventory_matches(),
        "matrix_matches": matrix.get("matrix_matches"),
        "fixture_2plus_sources": totals.get("fixture_2plus_sources"),
        "odds_2plus_sources": totals.get("odds_2plus_sources"),
        "context_2plus_sources": totals.get("context_2plus_sources"),
        "ready_for_model": totals.get("ready_for_model"),
        "ready_for_publish": totals.get("ready_for_publish"),
    }
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "runbot_discovery_first_prepare_v1", "status": "ok", "duration_seconds": round(time.perf_counter() - started, 2), "steps": steps, "final": final}
    write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
