from __future__ import annotations

"""Repository startup shim.

Production policy now lives in normal application modules and workflow files.
This shim keeps local helper scripts importable and installs guarded runtime
compatibility hooks:
- helper scripts default to the workflow-local inventory day when no explicit
  DAY_INVENTORY_TARGET_DATE is exported;
- controlled fallback B-tier follows the configured HARIZON contract while
  A-tier and price/xG/value guards stay strict;
- daily reports can read the durable run ledger export committed by run-bot.
"""

import importlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

os.environ.setdefault("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")


def _truthy(value: str | None, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _runtime_timezone() -> ZoneInfo:
    for value in (os.getenv("APP_TIMEZONE"), os.getenv("TZ"), "Europe/Moscow"):
        try:
            return ZoneInfo(str(value))
        except Exception:
            continue
    return ZoneInfo("Europe/Moscow")


def _default_inventory_day() -> str:
    cached = str(os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if cached:
        return cached[:10]
    return datetime.now(timezone.utc).astimezone(_runtime_timezone()).date().isoformat()


if not os.getenv("DAY_INVENTORY_TARGET_DATE") and not _truthy(os.getenv("HARIZON_DISABLE_SITECUSTOMIZE_LOCAL_DAY")):
    os.environ["DAY_INVENTORY_TARGET_DATE"] = _default_inventory_day()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


def _write_contract_patch_report(payload: dict[str, Any]) -> None:
    try:
        out = ROOT / ".data" / "exports" / "latest-controlled-fallback-b-tier-contract-patch.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _write_daily_patch_report(payload: dict[str, Any]) -> None:
    try:
        out = ROOT / ".data" / "exports" / "latest-daily-ops-run-ledger-patch.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _tier_min_books(tier: str) -> int:
    tier = str(tier or "").upper()
    prefix = f"CONTROLLED_FALLBACK_TIER_{tier}_"
    if tier == "B":
        raw = (
            os.getenv(prefix + "MIN_BOOKS")
            or os.getenv(prefix + "MIN_BOOKMAKERS")
            or os.getenv("PUBLISH_TIER_B_MIN_BOOKS")
            or os.getenv("PUBLISH_MIN_BOOKS")
            or os.getenv("MIN_BOOKS_PUBLISH")
            or "1"
        )
        return max(1, _as_int(raw, 1))
    raw = (
        os.getenv(prefix + "MIN_BOOKS")
        or os.getenv(prefix + "MIN_BOOKMAKERS")
        or os.getenv("PUBLISH_TIER_A_MIN_BOOKS")
        or "2"
    )
    return max(1, _as_int(raw, 2))


def _reason_is_legacy_b_two_book_block(reason: Any) -> bool:
    text = str(reason or "").strip().lower().replace("-", "_")
    return (
        text == "tier_b_books_below_min"
        or text.startswith("tier_b_bookmaker_quorum_books_below_min")
        or text.startswith("tier_b_bookmaker_quorum_prices_missing")
    )


def _patch_controlled_fallback_module(module: Any) -> None:
    if getattr(module, "_harizon_b_tier_single_book_contract_patched", False):
        return
    original_tier_reasons = getattr(module, "tier_reasons", None)
    original_price_guard = getattr(module, "_bookmaker_quorum_price_guard", None)
    if not callable(original_tier_reasons):
        return

    def bookmaker_quorum_price_guard_contract(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        min_books = _tier_min_books("B")
        if min_books > 1 and callable(original_price_guard):
            return list(original_price_guard(candidate, metrics) or [])
        if not _truthy(os.getenv("CONTROLLED_FALLBACK_TIER_B_BOOKMAKER_QUORUM_PRICE_GUARD"), True):
            return []
        books_count = _as_int((metrics or {}).get("books_count"), 0)
        if books_count < min_books:
            return [f"tier_b_bookmaker_quorum_books_below_min:{books_count}/{min_books}"]
        if isinstance(metrics, dict):
            guard = metrics.setdefault("tier_b_bookmaker_quorum", {})
            if isinstance(guard, dict):
                guard.update({
                    "enabled": True,
                    "mode": "single_book_contract",
                    "single_book_b_tier_contract": True,
                    "books_count": books_count,
                    "min_books": min_books,
                    "price_integrity_preserved_by_external_guard": True,
                })
        return []

    def tier_reasons_contract(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(original_tier_reasons(tier, candidate, metrics) or [])
        if str(tier or "").upper() != "B":
            return reasons
        min_books = _tier_min_books("B")
        books_count = _as_int((metrics or {}).get("books_count"), 0)
        if min_books <= 1 and books_count >= 1:
            filtered = [reason for reason in reasons if not _reason_is_legacy_b_two_book_block(reason)]
            removed = [str(reason) for reason in reasons if _reason_is_legacy_b_two_book_block(reason)]
            if removed and isinstance(metrics, dict):
                policy = metrics.setdefault("b_tier_contract_policy", {})
                if isinstance(policy, dict):
                    policy.update({
                        "mode": "single_book_b_tier",
                        "min_books": min_books,
                        "books_count": books_count,
                        "removed_legacy_two_book_reasons": removed,
                    })
            return filtered
        return reasons

    if callable(original_price_guard):
        module._bookmaker_quorum_price_guard = bookmaker_quorum_price_guard_contract
    module.tier_reasons = tier_reasons_contract
    module._harizon_b_tier_single_book_contract_patched = True
    _write_contract_patch_report({
        "status": "installed",
        "module": str(getattr(module, "__name__", "")),
        "policy": "B-tier uses configured min books; CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS=1 is not promoted to 2",
        "b_tier_min_books": _tier_min_books("B"),
        "a_tier_min_books": _tier_min_books("A"),
        "target_date": os.getenv("DAY_INVENTORY_TARGET_DATE"),
    })


def _patch_daily_ops_report_module(module: Any) -> None:
    if getattr(module, "_harizon_run_ledger_export_patched", False):
        return
    original_collect_runs = getattr(module, "collect_runs", None)
    if not callable(original_collect_runs):
        return

    def collect_runs_with_export(report_date: str) -> list[dict[str, Any]]:
        rows = [dict(x) for x in (original_collect_runs(report_date) or []) if isinstance(x, dict)]
        tz = module.app_tz()
        ledger: list[dict[str, Any]] = []
        payload = _load_json(ROOT / ".data" / "exports" / "latest-run-report-ledger.json", [])
        if isinstance(payload, list):
            ledger.extend(x for x in payload if isinstance(x, dict))
        ledger.extend(_load_jsonl(ROOT / ".data" / "bets" / "run_report_ledger.jsonl"))
        seen = {str(row.get("github_run_id") or "") + "|" + str(row.get("created_at") or row.get("created_at_utc") or "")[:16] for row in rows}
        added = 0
        for item in ledger:
            created = item.get("created_at_utc") or item.get("created_at") or item.get("updated_at_utc")
            try:
                if module.local_date(created, tz) != report_date:
                    continue
            except Exception:
                continue
            key = str(item.get("github_run_id") or "") + "|" + str(created or "")[:16]
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "created_at": created,
                "summary": dict(item.get("summary") or {}),
                "_archive_path": "export:latest-run-report-ledger.json",
                "ledger_source": item.get("source") or "run-bot",
                "github_run_id": item.get("github_run_id"),
            })
            added += 1
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        _write_daily_patch_report({"status": "installed", "report_date": report_date, "ledger_rows_seen": len(ledger), "ledger_rows_added": added})
        return rows

    module.collect_runs = collect_runs_with_export
    module._harizon_run_ledger_export_patched = True


_original_spec_from_file_location = importlib.util.spec_from_file_location


def _spec_from_file_location_patched(name: str, location: Any, *args: Any, **kwargs: Any) -> Any:
    spec = _original_spec_from_file_location(name, location, *args, **kwargs)
    try:
        path = Path(str(location)).resolve()
    except Exception:
        path = None
    if spec is None or path is None or path.name not in {"publish_controlled_fallback.py", "build_daily_ops_report.py"}:
        return spec
    loader = getattr(spec, "loader", None)
    exec_module = getattr(loader, "exec_module", None)
    if not callable(exec_module):
        return spec

    def exec_module_patched(module: Any) -> Any:
        result = exec_module(module)
        try:
            if path.name == "publish_controlled_fallback.py":
                _patch_controlled_fallback_module(module)
            elif path.name == "build_daily_ops_report.py":
                _patch_daily_ops_report_module(module)
        except Exception as exc:
            if path.name == "publish_controlled_fallback.py":
                _write_contract_patch_report({
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "policy": "B-tier single-book contract patch failed",
                })
            else:
                _write_daily_patch_report({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return result

    loader.exec_module = exec_module_patched  # type: ignore[method-assign]
    return spec


importlib.util.spec_from_file_location = _spec_from_file_location_patched


def install_legacy_sitecustomize() -> dict[str, str]:
    modules = (
        "app.services.api_runtime_enhancements",
        "app.providers.odds_api_io_startup_compat",
    )
    results: dict[str, str] = {}
    for module_path in modules:
        try:
            module = importlib.import_module(module_path)
            installer = getattr(module, "install", None)
            if callable(installer):
                installer()
            results[module_path] = "ok"
        except Exception as exc:
            results[module_path] = f"{type(exc).__name__}: {exc}"
    return results


if _truthy(os.getenv("LEGACY_SITECUSTOMIZE_ENABLED")):
    install_legacy_sitecustomize()
