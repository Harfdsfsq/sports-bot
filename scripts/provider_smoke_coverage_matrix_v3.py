from __future__ import annotations

"""Provider smoke coverage matrix v3.

v3 keeps the base 300-match matrix and adds an SStats-deep projection from
latest-sstats-crosswalk.json. It does not mutate inventory yet; it tells us how
much coverage can be lifted once provider_source_ids.sstats is persisted and
SStats deep endpoints are wired into runtime enrichment.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import provider_smoke_coverage_matrix as base

OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-smoke-coverage-matrix.json"
TXT_OUT = OUT_DIR / "provider-smoke-coverage-matrix.txt"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def collect_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("results", "checks"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([item for item in value if isinstance(item, dict)])
        for key in ("raw_smoke_payload", "api_full_data_enrichment"):
            value = payload.get(key)
            if isinstance(value, dict):
                rows.extend(collect_rows(value))
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
    payload = load(OUT_DIR / "latest-provider-smoke-diagnostics.json")
    if not payload:
        payload = load(OUT_DIR / "latest-provider-smoke-fast.json") or load(OUT_DIR / "latest-provider-smoke.json")
    full = load(OUT_DIR / "latest-api-full-data-enrichment.json")
    rows = collect_rows(payload) + collect_rows(full)
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    by_provider: dict[str, dict[str, int]] = {}
    not_ok: list[dict[str, Any]] = []
    for row in rows:
        provider = str(row.get("provider") or "unknown")
        status = str(row.get("status") or "unknown")
        by_provider.setdefault(provider, {})[status] = by_provider.setdefault(provider, {}).get(status, 0) + 1
        if status.lower() not in {"ok", "ready", "skipped_preserve_runtime_quota"}:
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


def queue_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    order = {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}
    bucket = str(item.get("bucket") or "unknown")
    missing = item.get("missing") if isinstance(item.get("missing"), list) else []
    return (order.get(bucket, 5), str(item.get("kickoff_utc") or ""), -len(missing), int(item.get("odds_sources") or 0) + int(item.get("context_sources") or 0))


def sstats_projection(payload: dict[str, Any]) -> dict[str, Any]:
    crosswalk = load(OUT_DIR / "latest-sstats-crosswalk.json")
    summary = crosswalk.get("summary") if isinstance(crosswalk.get("summary"), dict) else {}
    by_bucket = crosswalk.get("by_bucket") if isinstance(crosswalk.get("by_bucket"), dict) else {}
    totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    total_matches = int(payload.get("matrix_matches") or totals.get("matches") or 0)
    matched = int(summary.get("matched") or 0)
    missing_context = sum(int(row.get("missing_context") or 0) for row in by_bucket.values() if isinstance(row, dict))
    missing_xg = sum(int(row.get("missing_xg") or 0) for row in by_bucket.values() if isinstance(row, dict))
    missing_form = sum(int(row.get("missing_form") or 0) for row in by_bucket.values() if isinstance(row, dict))
    odds_rescue = int(summary.get("potential_odds_rescue") or sum(int(row.get("odds_rescue") or 0) for row in by_bucket.values() if isinstance(row, dict)))
    return {
        "status": crosswalk.get("status") or ("ok" if summary else "missing"),
        "inventory_matches_checked": crosswalk.get("inventory_matches_checked") or 0,
        "sstats_events_seen": crosswalk.get("sstats_events_seen") or 0,
        "matched": matched,
        "unmatched": summary.get("unmatched") or 0,
        "match_rate_pct": summary.get("match_rate_pct") or 0,
        "by_bucket": by_bucket,
        "potential_add": {
            "context_deep": matched,
            "xg_or_rating": matched,
            "form": matched,
            "odds_rescue": odds_rescue,
            "missing_context_among_matched": missing_context,
            "missing_xg_among_matched": missing_xg,
            "missing_form_among_matched": missing_form,
        },
        "projected_upper_bound_after_sstats_deep": {
            "context_any": min(total_matches, int(totals.get("context_any") or 0) + missing_context),
            "context_2plus_sources": min(total_matches, int(totals.get("context_2plus_sources") or 0) + matched),
            "xg": min(total_matches, int(totals.get("xg") or 0) + missing_xg),
            "form": min(total_matches, int(totals.get("form") or 0) + missing_form),
            "odds_2plus_sources": min(total_matches, int(totals.get("odds_2plus_sources") or 0) + odds_rescue),
        },
        "deep_enrichment_queue_top": (crosswalk.get("enrichment_queue") if isinstance(crosswalk.get("enrichment_queue"), list) else [])[:40],
    }


def render(payload: dict[str, Any]) -> str:
    text = base._render(payload).replace("# Provider smoke 300-match coverage matrix", "# Provider smoke 300-match coverage matrix v3")
    proj = payload.get("sstats_crosswalk_projection") if isinstance(payload.get("sstats_crosswalk_projection"), dict) else {}
    if not proj:
        return text
    upper = proj.get("projected_upper_bound_after_sstats_deep") or {}
    add = proj.get("potential_add") or {}
    block = [
        "",
        "## SStats deep projection",
        f"- crosswalk: matched={proj.get('matched', 0)} / checked={proj.get('inventory_matches_checked', 0)} | rate={proj.get('match_rate_pct', 0)}% | SStats events={proj.get('sstats_events_seen', 0)}",
        f"- potential_add: context_deep={add.get('context_deep', 0)} xg_or_rating={add.get('xg_or_rating', 0)} form={add.get('form', 0)} odds_rescue={add.get('odds_rescue', 0)}",
        f"- projected_upper_bound: context_2plus={upper.get('context_2plus_sources', 0)} xg={upper.get('xg', 0)} form={upper.get('form', 0)} odds_2plus={upper.get('odds_2plus_sources', 0)}",
        "- next action: persist provider_source_ids.sstats=gameId, then call /Games/glicko/{id} + /Games/last-games-stats for the queue.",
    ]
    return text + "\n" + "\n".join(block) + "\n"


def main() -> int:
    status = base.main()
    payload = load(JSON_OUT)
    if not payload:
        return status
    payload["matrix_version"] = "v3_sstats_crosswalk_projection"
    payload["provider_status_summary"] = provider_status_summary()
    queue = payload.get("next_enrichment_queue") if isinstance(payload.get("next_enrichment_queue"), list) else []
    payload["next_enrichment_queue"] = sorted([item for item in queue if isinstance(item, dict)], key=queue_key)
    payload["sstats_crosswalk_projection"] = sstats_projection(payload)
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    notes.append("v3: adds SStats crosswalk projection from latest-sstats-crosswalk.json; does not mutate inventory yet.")
    payload["notes"] = notes
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
