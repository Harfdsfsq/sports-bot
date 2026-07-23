"""Ensure the strict 300 contains 300 unique semantic match keys.

Multiple discovery aliases can survive candidate merging and later normalize to the
same ``row_key``.  The strict selector previously counted those rows separately,
which wasted inventory and provider-assignment slots.  This patch removes duplicate
keys before the existing strict/partial ranking and rotation policy runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import row_key

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-strict-unique-cohort-patch.json"
_INSTALLED = False
_ORIGINAL_SELECT = None


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _unique_ranked(
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> tuple[list[tuple[tuple[Any, ...], dict[str, Any]]], list[str]]:
    unique: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    seen: set[str] = set()
    duplicate_keys: list[str] = []
    for index, pair in enumerate(ranked):
        _score, row = pair
        key = str(row_key(row) or "").strip()
        marker = key or f"__unkeyed_row_{index}"
        if marker in seen:
            if key:
                duplicate_keys.append(key)
            continue
        seen.add(marker)
        unique.append(pair)
    return unique, duplicate_keys


def _select(
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    assert callable(_ORIGINAL_SELECT)
    unique, duplicate_keys = _unique_ranked(ranked)
    selected, offset = _ORIGINAL_SELECT(unique)
    selected_keys = [str(row_key(row) or "").strip() for row in selected]
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "input_rows": len(ranked),
        "unique_rows_before_selection": len(unique),
        "duplicate_rows_removed": len(duplicate_keys),
        "duplicate_key_sample": sorted(set(duplicate_keys))[:20],
        "selected_rows": len(selected),
        "selected_unique_keys": len({key for key in selected_keys if key}),
        "rotation_offset": offset,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return selected, offset


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SELECT
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.services import strict_coverage_inventory_sync as sync_module

    current = sync_module._select
    if getattr(current, "_harizon_unique_strict_cohort", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_SELECT = current
    _select._harizon_unique_strict_cohort = True  # type: ignore[attr-defined]
    sync_module._select = _select
    _INSTALLED = True
    result = {
        "status": "installed",
        "policy": "dedupe_by_final_semantic_row_key_before_strict_selection",
        "publication_contract_relaxed": False,
    }
    _write(result)
    return result


__all__ = ["_unique_ranked", "install"]
