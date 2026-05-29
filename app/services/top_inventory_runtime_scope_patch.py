from __future__ import annotations

"""Restrict run-once processing to the current top day-inventory scope.

The daily inventory is deliberately capped at 300 top matches.  Some runtime
layers can still re-introduce broad provider rows after the first bootstrap
filter (for example when day inventory rows are merged back with slightly
different keys, or when provider-target wrappers receive an expanded match
list).  This patch therefore scopes three boundaries:

* PredictionRunner._fetch_matches output;
* PredictionRunner._merge_day_inventory_matches output;
* PredictionRunner._filter_matches output and provider target inputs.

The allowlist is intentionally strict: exact inventory match keys plus ordered
home/away/date identities.  It no longer adds reversed/sorted key variants,
because those variants were too permissive and let same-day non-top rows leak
into progressive state.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
REPORT_PATH = EXPORT_DIR / "latest-top-inventory-runtime-scope.json"
PROGRESSIVE_STATE_PATH = DAY_INV_DIR / "progressive_coverage_state.json"
PROGRESSIVE_EXPORT_PATH = EXPORT_DIR / "latest-progressive-coverage-state.json"
_MARKER = "_harizon_top_inventory_runtime_scope_v2"


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _append_report(stage: str, payload: dict[str, Any]) -> None:
    current = _read_json(REPORT_PATH, {})
    if not isinstance(current, dict):
        current = {}
    events = current.get("events") if isinstance(current.get("events"), list) else []
    events.append({"stage": stage, **payload})
    merged = {**current, **payload, "stage": stage, "events": events[-20:]}
    _write_json(REPORT_PATH, merged)


def _app_tz() -> ZoneInfo | timezone:
    name = str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit
    payload = _read_json(DAY_INV_DIR / "latest.json", {})
    if isinstance(payload, dict) and payload.get("date_local"):
        return str(payload.get("date_local"))
    return datetime.now(UTC).astimezone(_app_tz()).date().isoformat()


def _parse_dt(value: Any) -> datetime | None:
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


def _norm(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9а-яё]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: Any) -> str:
    return _norm(value).replace(" ", "_")


def _local_date_from_any(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return ""
    try:
        return dt.astimezone(_app_tz()).date().isoformat()
    except Exception:
        return dt.date().isoformat()


def _row_date(row: dict[str, Any]) -> str:
    for key in ("date_local", "kickoff_utc", "commence_time", "start_time", "kickoff"):
        value = row.get(key)
        if not value:
            continue
        if key == "date_local" and re.match(r"^20\d\d-\d\d-\d\d$", str(value)):
            return str(value)
        date = _local_date_from_any(value)
        if date:
            return date
    key = str(row.get("match_key") or row.get("canonical_match_id") or "")
    match = re.search(r"(20\d\d-\d\d-\d\d)", key)
    return match.group(1) if match else ""


def _match_key(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("match_key") or match.get("canonical_match_id") or "").strip()
    return str(getattr(match, "match_key", "") or "").strip()


def _match_home(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("home_team") or match.get("home") or "")
    return str(getattr(match, "home_team", "") or "")


def _match_away(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("away_team") or match.get("away") or "")
    return str(getattr(match, "away_team", "") or "")


def _match_date(match: Any) -> str:
    if isinstance(match, dict):
        return _row_date(match)
    return _local_date_from_any(getattr(match, "commence_time", None))


def _identity(home: Any, away: Any, date: str) -> str:
    home_c = _compact(home)
    away_c = _compact(away)
    if not home_c or not away_c or not date:
        return ""
    # Ordered home/away identity only.  Do not add reversed/sorted variants: those
    # caused non-top rows to pass scope after midnight.
    return f"soccer|{home_c}|{away_c}|{date}"


def _match_identity(match: Any) -> str:
    return _identity(_match_home(match), _match_away(match), _match_date(match))


def _is_day_inventory_match(match: Any) -> bool:
    if isinstance(match, dict):
        meta = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
        return bool(match.get("source") == "day_inventory" or meta.get("day_inventory"))
    meta = getattr(match, "metadata", None)
    return bool(getattr(match, "source", "") == "day_inventory" or (isinstance(meta, dict) and meta.get("day_inventory")))


def _inventory_scope() -> tuple[dict[str, set[str]], dict[str, Any]]:
    date = _target_date()
    paths = [DAY_INV_DIR / f"{date}.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "latest.json"]
    max_matches = max(1, _to_int(os.getenv("DAY_INVENTORY_MAX_MATCHES") or os.getenv("TOP_INVENTORY_RUNTIME_MAX_MATCHES") or 300, 300))
    for path in paths:
        payload = _read_json(path, {})
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            continue
        rows = [r for r in payload.get("matches", []) if isinstance(r, dict) and (_row_date(r) in {"", date})]
        if not rows:
            continue
        rows = rows[:max_matches]
        direct_keys: set[str] = set()
        identities: set[str] = set()
        for row in rows:
            direct = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
            if direct:
                direct_keys.add(direct)
            ident = _identity(row.get("home_team") or row.get("home"), row.get("away_team") or row.get("away"), _row_date(row) or date)
            if ident:
                identities.add(ident)
        scope = {"direct_keys": direct_keys, "identities": identities}
        info = {
            "date_local": date,
            "inventory_path": str(path),
            "inventory_rows": len(rows),
            "direct_keys": len(direct_keys),
            "identity_keys": len(identities),
            "allowlist_keys": len(direct_keys) + len(identities),
            "max_matches": max_matches,
        }
        return scope, info
    return {"direct_keys": set(), "identities": set()}, {"date_local": date, "inventory_path": None, "inventory_rows": 0, "direct_keys": 0, "identity_keys": 0, "allowlist_keys": 0, "max_matches": max_matches}


def _in_scope(match: Any, scope: dict[str, set[str]]) -> bool:
    direct = _match_key(match)
    if direct and direct in scope.get("direct_keys", set()):
        return True
    ident = _match_identity(match)
    return bool(ident and ident in scope.get("identities", set()))


def _dedupe_scoped(matches: list[Any], max_matches: int) -> list[Any]:
    chosen: dict[str, Any] = {}
    order: list[str] = []
    for match in matches:
        ident = _match_identity(match) or _match_key(match)
        if not ident:
            continue
        old = chosen.get(ident)
        if old is None:
            chosen[ident] = match
            order.append(ident)
            continue
        # Prefer the canonical day-inventory row when duplicate provider rows use a
        # different match_key for the same fixture.
        if _is_day_inventory_match(match) and not _is_day_inventory_match(old):
            chosen[ident] = match
    out = [chosen[key] for key in order if key in chosen]
    return out[:max_matches] if max_matches > 0 else out


def _filter_matches(matches: list[Any], scope: dict[str, set[str]], max_matches: int) -> list[Any]:
    if not scope.get("direct_keys") and not scope.get("identities"):
        return matches[:max_matches] if max_matches > 0 else matches
    return _dedupe_scoped([m for m in matches if _in_scope(m, scope)], max_matches)



def _prune_progressive_state_to_scope(scope: dict[str, set[str]], info: dict[str, Any], stage: str) -> dict[str, Any]:
    """Remove same-day non-top rows left by previous broad runs.

    The progressive coverage patch only date-prunes its state.  After a broad
    provider expansion, same-date rows outside top-300 can stay in
    progressive_coverage_state and make reports show 300+ active matches even
    when the current runtime match list is scoped.  Prune those rows using the
    same strict direct-key/ordered-identity scope.
    """
    payload = _read_json(PROGRESSIVE_STATE_PATH, {})
    if not isinstance(payload, dict):
        return {"progressive_pruned": 0, "progressive_kept": 0}
    rows = payload.get("matches") if isinstance(payload.get("matches"), dict) else {}
    if not rows:
        return {"progressive_pruned": 0, "progressive_kept": 0}
    kept: dict[str, Any] = {}
    pruned = 0
    for key, row in rows.items():
        if not isinstance(row, dict):
            pruned += 1
            continue
        row_with_key = {**row, "match_key": row.get("match_key") or key}
        direct = str(row_with_key.get("match_key") or key or "").strip()
        ident = _identity(row_with_key.get("home_team"), row_with_key.get("away_team"), _row_date(row_with_key) or str(info.get("date_local") or ""))
        if (direct and direct in scope.get("direct_keys", set())) or (ident and ident in scope.get("identities", set())):
            kept[str(key)] = row
        else:
            pruned += 1
    if pruned:
        payload["matches"] = kept
        payload["top_inventory_scope_pruned_at_utc"] = datetime.now(UTC).isoformat()
        payload["top_inventory_scope_pruned_stage"] = stage
        payload["top_inventory_scope_pruned_rows"] = int(payload.get("top_inventory_scope_pruned_rows") or 0) + pruned
        _write_json(PROGRESSIVE_STATE_PATH, payload)
        _write_json(PROGRESSIVE_EXPORT_PATH, payload)
    return {"progressive_pruned": pruned, "progressive_kept": len(kept)}


def _scope_result(stage: str, matches: list[Any], *, extra: dict[str, Any] | None = None) -> tuple[list[Any], dict[str, Any]]:
    scope, info = _inventory_scope()
    filtered = _filter_matches(matches, scope, int(info.get("max_matches") or 300))
    fail_open_min = max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_FAIL_OPEN_MIN_MATCHES") or 20, 20))
    used_fail_open = bool((scope.get("direct_keys") or scope.get("identities")) and matches and len(filtered) < fail_open_min)
    final_matches = matches if used_fail_open else filtered
    prune_report = _prune_progressive_state_to_scope(scope, info, stage)
    report = {
        **info,
        "enabled": True,
        "input_matches": len(matches),
        "output_matches": len(final_matches),
        "filtered_out": max(0, len(matches) - len(final_matches)),
        "fail_open": used_fail_open,
        "fail_open_min_matches": fail_open_min,
        **prune_report,
    }
    if extra:
        report.update(extra)
    _append_report(stage, report)
    return final_matches, report


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("TOP_INVENTORY_RUNTIME_SCOPE_ENABLED", "true"), True):
        return {"status": "disabled"}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "error", "error": f"import_runner:{type(exc).__name__}: {exc}"}

    original_fetch_matches = getattr(PredictionRunner, "_fetch_matches", None)
    original_merge_inventory = getattr(PredictionRunner, "_merge_day_inventory_matches", None)
    original_filter_matches = getattr(PredictionRunner, "_filter_matches", None)
    original_fetch_provider = getattr(PredictionRunner, "_fetch_provider", None)
    if not callable(original_fetch_matches):
        return {"status": "missing_fetch_matches"}
    if getattr(original_fetch_matches, _MARKER, False):
        return {"status": "already_wrapped"}

    async def fetch_matches_top_inventory_scoped(self: Any):  # type: ignore[no-untyped-def]
        result = await original_fetch_matches(self)
        if not isinstance(result, tuple) or len(result) < 1:
            return result
        matches, report = _scope_result("fetch_matches", list(result[0] or []))
        meta = result[1] if len(result) > 1 and isinstance(result[1], dict) else {}
        scoped_meta = dict(meta)
        scoped_meta["top_inventory_runtime_scope"] = report
        if len(result) == 1:
            return (matches,)
        return (matches, scoped_meta, *result[2:])

    setattr(fetch_matches_top_inventory_scoped, _MARKER, True)
    setattr(fetch_matches_top_inventory_scoped, "_harizon_original_fetch_matches", original_fetch_matches)
    PredictionRunner._fetch_matches = fetch_matches_top_inventory_scoped  # type: ignore[assignment]

    if callable(original_merge_inventory) and not getattr(original_merge_inventory, _MARKER, False):
        def merge_day_inventory_scoped(self: Any, bootstrap_matches: list[Any], bootstrap_meta: dict[str, Any], now_utc: Any):  # type: ignore[no-untyped-def]
            merged_matches, merged_meta = original_merge_inventory(self, bootstrap_matches, bootstrap_meta, now_utc)
            scoped, report = _scope_result("merge_day_inventory", list(merged_matches or []))
            meta = dict(merged_meta or {})
            meta["top_inventory_runtime_scope_after_merge"] = report
            return scoped, meta

        setattr(merge_day_inventory_scoped, _MARKER, True)
        PredictionRunner._merge_day_inventory_matches = merge_day_inventory_scoped  # type: ignore[assignment]

    if callable(original_filter_matches) and not getattr(original_filter_matches, _MARKER, False):
        def filter_matches_scoped(self: Any, matches: list[Any], now_utc: Any):  # type: ignore[no-untyped-def]
            result = original_filter_matches(self, matches, now_utc)
            if not isinstance(result, tuple) or len(result) < 1:
                return result
            scoped, report = _scope_result("filter_matches", list(result[0] or []))
            filtering = dict(result[1] or {}) if len(result) > 1 and isinstance(result[1], dict) else {}
            filtering["top_inventory_runtime_scope"] = report
            return (scoped, filtering, *result[2:]) if len(result) > 2 else (scoped, filtering)

        setattr(filter_matches_scoped, _MARKER, True)
        PredictionRunner._filter_matches = filter_matches_scoped  # type: ignore[assignment]

    if callable(original_fetch_provider) and not getattr(original_fetch_provider, _MARKER, False):
        async def fetch_provider_scoped(self: Any, provider: Any, method_name: str, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            if method_name in {"fetch_offers", "fetch_context"} and args and isinstance(args[0], list):
                scoped, _report = _scope_result(f"provider_{method_name}", list(args[0] or []), extra={"provider_method": method_name})
                args = (scoped, *args[1:])
            return await original_fetch_provider(self, provider, method_name, *args, **kwargs)

        setattr(fetch_provider_scoped, _MARKER, True)
        PredictionRunner._fetch_provider = fetch_provider_scoped  # type: ignore[assignment]

    scope, info = _inventory_scope()
    prune_report = _prune_progressive_state_to_scope(scope, info, "install")
    _append_report("install", {**info, **prune_report, "enabled": True, "installed": True})
    return {"status": "installed", **info, **prune_report, "wrapped": ["_fetch_matches", "_merge_day_inventory_matches", "_filter_matches", "_fetch_provider"]}
