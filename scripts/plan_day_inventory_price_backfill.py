from __future__ import annotations

"""Plan and optionally execute low-quota price backfill for top-300 inventory.

The planner makes no external API calls itself.  In provider-smoke-minimal-repair
it chains `execute_day_inventory_price_backfill.py` once per workflow.  It also
marks the eventIds selected for the current odds-api.io batch so the next run can
rotate to the next price-thin matches instead of spending the same two requests
again.
"""

import json
import os
import re
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
EXPORT_DIR = ROOT / ".data" / "exports"
OUT_JSON = EXPORT_DIR / "latest-day-inventory-price-backfill-plan.json"
OUT_TXT = EXPORT_DIR / "latest-day-inventory-price-backfill-plan.txt"
EXEC_JSON = EXPORT_DIR / "latest-day-inventory-price-backfill-execution.json"
SUMMARY = EXPORT_DIR / "latest-day-inventory-summary.json"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date(now: datetime) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    return explicit or now.astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "bzzoiro_predictions": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "sstats_form": "sstats",
        "football_data_org": "football_data",
        "sportsdb": "thesportsdb",
        "the_sports_db": "thesportsdb",
    }
    return aliases.get(text, text)


def source_ids(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    for key, value in raw.items():
        src = norm(key)
        val = str(value or "").strip()
        if src and val:
            out[src] = val
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for src in ("odds_api_io", "bzzoiro", "sstats", "sportlogic"):
        for key in (f"{src}_event_id", f"{src}_id", f"{src}_match_id"):
            val = str(metadata.get(key) or "").strip()
            if val and src not in out:
                out[src] = val
    return out


def fixture_sources(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("fixture_sources", "sources_seen"):
        value = row.get(key)
        if isinstance(value, list):
            out.extend(norm(x) for x in value if norm(x))
    out.extend(source_ids(row).keys())
    src = norm(row.get("source"))
    if src:
        out.append(src)
    seen: set[str] = set()
    final: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            final.append(item)
    return final


def price_count(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        as_int(metadata.get("price_confirmation_sources_count")),
        as_int(metadata.get("price_sources_count")),
        len(row.get("price_confirmations") or []),
        len(row.get("books") or []),
    )


def context_count(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        as_int(metadata.get("context_sources_count")),
        as_int(metadata.get("confirmation_sources_count")),
        len(row.get("context_confirmations") or []),
        len(row.get("context_sources") or []),
    )


def backfill_attempts(row: dict[str, Any]) -> int:
    data = row.get("price_backfill") if isinstance(row.get("price_backfill"), dict) else {}
    return as_int(data.get("odds_api_io_attempts"), 0)


def route_for(row: dict[str, Any]) -> list[str]:
    ids = source_ids(row)
    sources = fixture_sources(row)
    routes: list[str] = []
    if ids.get("odds_api_io"):
        routes.append("odds_api_io:odds_multi")
    if ids.get("bzzoiro") or "bzzoiro" in sources:
        routes.append("bzzoiro:current_odds_or_prediction")
    if ids.get("sstats") or "sstats" in sources:
        routes.append("sstats:odds_snapshot_if_present")
    if ids.get("sportlogic"):
        routes.append("sportlogic:odds_detail_if_not_stale")
    if not routes:
        routes.append("needs_provider_match_first")
    return routes


def priority_tuple(row: dict[str, Any], now: datetime, min_price: int, min_context: int) -> tuple[int, float, int, int, str, str]:
    kickoff = parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local"))
    hours = 9999.0 if kickoff is None else (kickoff - now).total_seconds() / 3600.0
    if hours < -2:
        bucket = 8
    elif hours <= 6:
        bucket = 0
    elif hours <= 12:
        bucket = 1
    elif hours <= 24:
        bucket = 2
    else:
        bucket = 3
    context_bonus = 0 if context_count(row) >= min_context else 1
    need = max(0, min_price - price_count(row))
    # Attempt penalty rotates the next run toward untried price-thin matches.
    attempt_penalty = min(9, backfill_attempts(row))
    return (bucket, abs(hours), attempt_penalty, context_bonus + need, str(row.get("league_name") or ""), str(row.get("home_team") or ""))


def batch_request_limit() -> int:
    max_per_request = max(1, min(10, as_int(os.getenv("PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST"), 10)))
    batches = max(1, as_int(os.getenv("PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT"), 1))
    return max_per_request * batches


def render(report: dict[str, Any]) -> str:
    lines = [
        "💸 Day inventory price backfill plan",
        f"• date_local: {report.get('date_local')}",
        f"• matches_total: {report.get('matches_total')}",
        f"• missing 2+ price: {report.get('missing_2plus_price')}",
        f"• context-ready but price-thin: {report.get('context_ready_price_thin')}",
        f"• odds_api_io event ids planned: {len(report.get('odds_api_io_event_ids', []))}",
        f"• odds_api_io event ids selected this run: {len(report.get('odds_api_io_event_ids_selected', []))}",
        f"• previously attempted targets: {report.get('previously_attempted_targets')}",
        f"• execution: {(report.get('execution') or {}).get('status', 'not_run')}",
        "",
        "Top targets:",
    ]
    for item in (report.get("targets") or [])[:12]:
        lines.append(
            f"- {item.get('home_team')} — {item.get('away_team')} | price={item.get('price_confirmations')} "
            f"context={item.get('context_confirmations')} attempts={item.get('odds_api_io_attempts')} | route={','.join(item.get('routes') or [])}"
        )
    return "\n".join(lines) + "\n"


def should_auto_execute() -> bool:
    if str(os.getenv("PRICE_BACKFILL_EXECUTE_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on", "force"}:
        return True
    return str(os.getenv("APP_ENV") or "").strip().lower() == "provider-smoke-minimal-repair"


def maybe_execute_once() -> dict[str, Any]:
    if not should_auto_execute():
        return {"status": "skipped", "reason": "not enabled"}
    existing = load_json(EXEC_JSON, {})
    if isinstance(existing, dict) and existing.get("status") == "ok" and as_int(existing.get("requests_used")) > 0:
        return {"status": "skipped", "reason": "already_executed_this_workspace", "existing_requests_used": existing.get("requests_used")}
    script = ROOT / "scripts" / "execute_day_inventory_price_backfill.py"
    if not script.exists():
        return {"status": "skipped", "reason": "missing_executor", "path": str(script)}
    started = datetime.now(UTC).isoformat()
    old = os.environ.get("PRICE_BACKFILL_EXECUTE_ENABLED")
    os.environ["PRICE_BACKFILL_EXECUTE_ENABLED"] = "true"
    try:
        runpy.run_path(str(script), run_name="__main__")
        return {"status": "ok", "started_at_utc": started, "finished_at_utc": datetime.now(UTC).isoformat(), "path": str(script)}
    except SystemExit as exc:
        code = getattr(exc, "code", 0)
        return {"status": "ok" if code in (0, None) else "error", "started_at_utc": started, "finished_at_utc": datetime.now(UTC).isoformat(), "path": str(script), "code": code}
    except Exception as exc:
        return {"status": "error", "started_at_utc": started, "finished_at_utc": datetime.now(UTC).isoformat(), "path": str(script), "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if old is None:
            os.environ.pop("PRICE_BACKFILL_EXECUTE_ENABLED", None)
        else:
            os.environ["PRICE_BACKFILL_EXECUTE_ENABLED"] = old


def main() -> int:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    d = target_date(now)
    min_price = max(2, as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    target_limit = max(1, as_int(os.getenv("PRICE_BACKFILL_TARGET_LIMIT"), 120))
    odds_id_limit = max(1, as_int(os.getenv("PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT"), 60))
    bzz_limit = max(0, as_int(os.getenv("PRICE_BACKFILL_BZZOIRO_TARGET_LIMIT"), 40))
    sstats_limit = max(0, as_int(os.getenv("PRICE_BACKFILL_SSTATS_TARGET_LIMIT"), 60))
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    matches = [row for row in inv.get("matches", []) if isinstance(row, dict)] if isinstance(inv, dict) else []

    targets: list[dict[str, Any]] = []
    missing_price = 0
    context_ready_price_thin = 0
    previously_attempted = 0
    for row in sorted(matches, key=lambda r: priority_tuple(r, now, min_price, min_context)):
        pc = price_count(row)
        cc = context_count(row)
        if pc >= min_price:
            row.pop("price_backfill", None)
            continue
        missing_price += 1
        if cc >= min_context:
            context_ready_price_thin += 1
        attempts = backfill_attempts(row)
        previously_attempted += int(attempts > 0)
        ids = source_ids(row)
        routes = route_for(row)
        item = {
            "match_key": row.get("match_key") or row.get("canonical_match_id"),
            "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time"),
            "league_name": row.get("league_name"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "price_confirmations": pc,
            "context_confirmations": cc,
            "need_price_confirmations": max(0, min_price - pc),
            "fixture_sources": fixture_sources(row),
            "source_ids": ids,
            "routes": routes,
            "odds_api_io_attempts": attempts,
        }
        if len(targets) < target_limit:
            targets.append(item)
        row["price_backfill"] = {
            "updated_at_utc": now_iso,
            "needed": True,
            "price_confirmations": pc,
            "context_confirmations": cc,
            "need_price_confirmations": max(0, min_price - pc),
            "routes": routes,
            "source_ids": ids,
            "odds_api_io_attempts": attempts,
        }

    odds_ids: list[str] = []
    bzz_targets: list[dict[str, Any]] = []
    sstats_targets: list[dict[str, Any]] = []
    match_first: list[dict[str, Any]] = []
    for item in targets:
        ids = item.get("source_ids") or {}
        if ids.get("odds_api_io") and len(odds_ids) < odds_id_limit:
            odds_ids.append(str(ids["odds_api_io"]))
        if "bzzoiro:current_odds_or_prediction" in (item.get("routes") or []) and len(bzz_targets) < bzz_limit:
            bzz_targets.append(item)
        if "sstats:odds_snapshot_if_present" in (item.get("routes") or []) and len(sstats_targets) < sstats_limit:
            sstats_targets.append(item)
        if item.get("routes") == ["needs_provider_match_first"] and len(match_first) < 40:
            match_first.append(item)
    selected_ids = odds_ids[:batch_request_limit()]
    selected_set = set(selected_ids)

    # Mark selected eventIds before execution. If an API returns no markets for an
    # event, the next persisted run will rotate that event behind untried targets.
    for row in matches:
        eid = source_ids(row).get("odds_api_io")
        if not eid or eid not in selected_set or price_count(row) >= min_price:
            continue
        bf = row.get("price_backfill") if isinstance(row.get("price_backfill"), dict) else {}
        attempts = as_int(bf.get("odds_api_io_attempts"), 0) + 1
        bf.update({
            "odds_api_io_attempts": attempts,
            "last_odds_api_io_attempt_planned_utc": now_iso,
            "last_odds_api_io_attempt_event_id": eid,
            "attempt_rotation_enabled": True,
        })
        row["price_backfill"] = bf

    if isinstance(inv, dict):
        inv["updated_at_utc"] = now_iso
        src_meta = inv.setdefault("sources", {})
        if isinstance(src_meta, dict):
            src_meta["price_backfill_plan"] = {
                "updated_at_utc": now_iso,
                "targets": len(targets),
                "missing_2plus_price": missing_price,
                "context_ready_price_thin": context_ready_price_thin,
                "previously_attempted_targets": previously_attempted,
                "odds_api_io_event_ids": len(odds_ids),
                "odds_api_io_event_ids_selected": len(selected_ids),
                "bzzoiro_targets": len(bzz_targets),
                "sstats_targets": len(sstats_targets),
            }
        for path in [inv_path, DAY_INV_DIR / "latest.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "today.json"]:
            write_json(path, inv)
        summary = load_json(SUMMARY, {})
        if isinstance(summary, dict):
            summary["sources"] = dict(inv.get("sources") or {})
            summary["counts"] = dict(inv.get("counts") or summary.get("counts") or {})
            summary["updated_at_utc"] = now_iso
            write_json(SUMMARY, summary)

    report = {
        "status": "ok",
        "date_local": d,
        "updated_at_utc": now_iso,
        "inventory_path": str(inv_path),
        "matches_total": len(matches),
        "min_price_confirmations": min_price,
        "min_context_sources": min_context,
        "missing_2plus_price": missing_price,
        "context_ready_price_thin": context_ready_price_thin,
        "previously_attempted_targets": previously_attempted,
        "target_limit": target_limit,
        "targets": targets,
        "odds_api_io_event_ids": odds_ids,
        "odds_api_io_event_ids_selected": selected_ids,
        "odds_api_io_event_ids_csv": ",".join(selected_ids),
        "bzzoiro_targets": bzz_targets,
        "sstats_targets": sstats_targets,
        "needs_provider_match_first": match_first,
        "execution": {"status": "not_started"},
        "notes": [
            "Planner itself does not call external APIs.",
            "In provider-smoke-minimal-repair it chains one guarded executor pass: at most two odds-api.io odds/multi requests.",
            "Selected odds_api_io eventIds are attempt-marked before execution, so persisted day_inventory can rotate future runs.",
        ],
    }
    write_json(OUT_JSON, report)
    OUT_TXT.write_text(render(report), encoding="utf-8")
    execution = maybe_execute_once()
    report["execution"] = execution
    write_json(OUT_JSON, report)
    OUT_TXT.write_text(render(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
