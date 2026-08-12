from __future__ import annotations

"""Project existing inventory/SStats context hints into fallback candidate metrics.

This repairs a common A-tier blocker: SStats raw coverage exists but candidate
metrics do not carry context_sources_count/confirmation_sources.  It does not
invent context; it only projects already present sources from inventory/coverage
artifacts onto matching fallback rows.
"""

import json
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
REPORT = EXPORT / "latest-sstats-context-candidate-projection.json"
FALLBACK = EXPORT / "latest-controlled-fallback-report.json"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "matches", "items", "inventory"):
            if isinstance(payload.get(key), list): return [r for r in payload[key] if isinstance(r, dict)]
    return []


def _key(row: dict[str, Any]) -> str:
    return str(row.get("match_key") or row.get("canonical_match_id") or "").strip().lower()


def _sources(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("context_sources", "confirmation_sources", "all_context_sources", "verified_context_sources", "core_context_sources"):
        val = row.get(key)
        if isinstance(val, list): out |= {str(x).strip().lower() for x in val if str(x).strip()}
        elif isinstance(val, str) and val.strip(): out.add(val.strip().lower())
    for box_key in ("coverage", "metadata", "source_summary", "day_inventory_coverage"):
        box = row.get(box_key) if isinstance(row.get(box_key), dict) else {}
        for key in ("context_sources", "confirmation_sources", "all_context_sources", "verified_context_sources", "core_context_sources"):
            val = box.get(key)
            if isinstance(val, list): out |= {str(x).strip().lower() for x in val if str(x).strip()}
            elif isinstance(val, str) and val.strip(): out.add(val.strip().lower())
    return {s for s in out if s not in {"fixture", "alias", "proxy", "market"}}


def main() -> int:
    truth = _load(EXPORT / "latest-day-inventory-coverage-truth.json", {})
    inv = _load(Path(".data/day_inventory/latest.json"), [])
    fallback = _load(FALLBACK, {})
    idx: dict[str, set[str]] = {}
    for row in _rows(truth.get("rows") if isinstance(truth, dict) else []) + _rows(inv):
        k = _key(row)
        if not k: continue
        src = _sources(row)
        if src: idx.setdefault(k, set()).update(src)
    evaluated = fallback.get("evaluated") if isinstance(fallback, dict) and isinstance(fallback.get("evaluated"), list) else []
    patched = 0
    for row in evaluated:
        if not isinstance(row, dict): continue
        src = idx.get(_key(row), set())
        if not src: continue
        metrics = row.setdefault("metrics", {})
        if not isinstance(metrics, dict): continue
        existing = set()
        val = metrics.get("confirmation_sources") or metrics.get("context_sources")
        if isinstance(val, list): existing = {str(x).strip().lower() for x in val}
        merged = sorted(existing | src)
        metrics["confirmation_sources"] = merged
        metrics["context_sources"] = merged
        metrics["confirmation_sources_count"] = max(int(metrics.get("confirmation_sources_count") or 0), len(merged))
        metrics["context_sources_count"] = max(int(metrics.get("context_sources_count") or 0), len(merged))
        patched += 1
    if patched:
        _write(FALLBACK, fallback)
    report = {"status": "ok", "patched_candidates": patched, "indexed_matches": len(idx), "publication_contract_relaxed": False}
    _write(REPORT, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
