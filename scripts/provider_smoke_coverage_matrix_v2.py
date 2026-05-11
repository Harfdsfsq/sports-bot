from __future__ import annotations

"""Provider-smoke coverage matrix v2.

v2 keeps the original matrix logic, then rewrites the artifacts with two fixes:
- provider endpoint status reads nested diagnostics/raw_smoke_payload/results;
- next enrichment queue prioritizes future matches before already-started rows.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import provider_smoke_coverage_matrix as base

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-smoke-coverage-matrix.json"
TXT_OUT = OUT_DIR / "provider-smoke-coverage-matrix.txt"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _collect_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("results", "checks"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([item for item in value if isinstance(item, dict)])
        for key in ("raw_smoke_payload", "api_full_data_enrichment"):
            value = payload.get(key)
            if isinstance(value, dict):
                rows.extend(_collect_rows(value))
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, dict):
            for item in diagnostics.get("providers") or []:
                if isinstance(item, dict):
                    rows.append({
                        "provider": item.get("provider"),
                        "group": item.get("group"),
                        "status": item.get("integration_status"),
                        "rows_count": item.get("max_rows"),
                        "reason": item.get("primary_weakness"),
                    })
    return rows


def provider_status_summary() -> dict[str, Any]:
    payload = _load(OUT_DIR / "latest-provider-smoke-diagnostics.json")
    if not payload:
        payload = _load(OUT_DIR / "latest-provider-smoke-fast.json") or _load(OUT_DIR / "latest-provider-smoke.json")
    full = _load(OUT_DIR / "latest-api-full-data-enrichment.json")
    rows = _collect_rows(payload) + _collect_rows(full)
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    by_provider: dict[str, dict[str, int]] = {}
    not_ok: list[dict[str, Any]] = []
    for row in rows:
        provider = str(row.get("provider") or "unknown")
        status = str(row.get("status") or "unknown")
        by_provider.setdefault(provider, {})[status] = by_provider.setdefault(provider, {}).get(status, 0) + 1
        good = status.lower() in {"ok", "ready", "skipped_preserve_runtime_quota"}
        if not good:
            not_ok.append({
                "provider": row.get("provider"),
                "group": row.get("group") or row.get("role"),
                "command": row.get("command"),
                "status": row.get("status"),
                "http_status": row.get("http_status"),
                "rows_count": row.get("rows_count") or row.get("item_count"),
                "reason": row.get("reason") or row.get("error") or row.get("note") or row.get("body_preview"),
            })
    return {"total_rows": len(rows), "by_status": dict(statuses), "by_provider": by_provider, "not_ok_top": not_ok[:30]}


def _queue_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    bucket = str(item.get("bucket") or "unknown")
    order = {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}
    missing = item.get("missing") if isinstance(item.get("missing"), list) else []
    return (order.get(bucket, 5), str(item.get("kickoff_utc") or ""), -len(missing), int(item.get("odds_sources") or 0) + int(item.get("context_sources") or 0))


def render(payload: dict[str, Any]) -> str:
    text = base._render(payload)
    return text.replace("# Provider smoke 300-match coverage matrix", "# Provider smoke 300-match coverage matrix v2")


def main() -> int:
    status = base.main()
    payload = _load(JSON_OUT)
    if not payload:
        return status
    payload["matrix_version"] = "v2_nested_status_future_queue"
    payload["provider_status_summary"] = provider_status_summary()
    queue = payload.get("next_enrichment_queue") if isinstance(payload.get("next_enrichment_queue"), list) else []
    payload["next_enrichment_queue"] = sorted([item for item in queue if isinstance(item, dict)], key=_queue_key)
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    notes.append("v2: provider status reads nested raw smoke/full-data artifacts; queue puts future matches before started rows.")
    payload["notes"] = notes
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
