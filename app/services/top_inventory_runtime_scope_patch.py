from __future__ import annotations

"""Restrict 2-hour runtime processing to the current top day-inventory scope.

The daily inventory is deliberately capped at 300 top matches.  During broad API
runs some providers can still return many more fixtures for the same local date;
if those are allowed into the main PredictionRunner state, progressive coverage
and Telegram diagnostics start tracking 800+ active matches even though the
publishable day inventory is 300.  This patch keeps discovery broad enough for
the 00:00 build, but makes run-once use only matches that are already in the
current day-inventory top scope.
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
_MARKER = "_harizon_top_inventory_runtime_scope_v1"


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


def _key_variants_from_values(home: Any, away: Any, date: str, direct: Any = "") -> set[str]:
    variants: set[str] = set()
    direct_text = str(direct or "").strip()
    if direct_text:
        variants.add(direct_text)
    home_c = _compact(home)
    away_c = _compact(away)
    if home_c and away_c and date:
        variants.add(f"soccer|{home_c}|{away_c}|{date}")
        variants.add(f"soccer|{away_c}|{home_c}|{date}")
        first, second = sorted([home_c, away_c])
        variants.add(f"soccer|{first}|{second}|{date}")
        variants.add(f"{home_c}|{away_c}|{date}")
        variants.add(f"{first}|{second}|{date}")
    return {v for v in variants if v}


def _match_variants(match: Any) -> set[str]:
    direct = str(getattr(match, "match_key", "") or "")
    home = getattr(match, "home_team", "")
    away = getattr(match, "away_team", "")
    kickoff = getattr(match, "commence_time", None)
    date = _local_date_from_any(kickoff)
    if isinstance(match, dict):
        direct = str(match.get("match_key") or match.get("canonical_match_id") or direct)
        home = match.get("home_team") or match.get("home") or home
        away = match.get("away_team") or match.get("away") or away
        date = _row_date(match) or date
    return _key_variants_from_values(home, away, date, direct)


def _inventory_allowlist() -> tuple[set[str], dict[str, Any]]:
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
        # The inventory file should already be top-cut.  Still hard-cap here so a
        # stale overgrown file cannot poison run-once after midnight.
        rows = rows[:max_matches]
        allowed: set[str] = set()
        for row in rows:
            direct = row.get("match_key") or row.get("canonical_match_id")
            allowed |= _key_variants_from_values(row.get("home_team") or row.get("home"), row.get("away_team") or row.get("away"), _row_date(row) or date, direct)
        return allowed, {"date_local": date, "inventory_path": str(path), "inventory_rows": len(rows), "allowlist_keys": len(allowed), "max_matches": max_matches}
    return set(), {"date_local": date, "inventory_path": None, "inventory_rows": 0, "allowlist_keys": 0, "max_matches": max_matches}


def _filter_matches(matches: list[Any], allowed: set[str]) -> list[Any]:
    if not allowed:
        return matches
    out: list[Any] = []
    for match in matches:
        if _match_variants(match) & allowed:
            out.append(match)
    return out


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("TOP_INVENTORY_RUNTIME_SCOPE_ENABLED"), True):
        return {"status": "disabled"}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "error", "error": f"import_runner:{type(exc).__name__}: {exc}"}

    original = getattr(PredictionRunner, "_fetch_matches", None)
    if not callable(original):
        return {"status": "missing_fetch_matches"}
    if getattr(original, _MARKER, False):
        return {"status": "already_wrapped"}

    async def fetch_matches_top_inventory_scoped(self: Any):  # type: ignore[no-untyped-def]
        result = await original(self)
        if not isinstance(result, tuple) or len(result) < 1:
            return result
        matches = list(result[0] or [])
        meta = result[1] if len(result) > 1 and isinstance(result[1], dict) else {}
        allowed, info = _inventory_allowlist()
        filtered = _filter_matches(matches, allowed)
        # Fail-open when keys clearly do not match; otherwise a schema drift could
        # accidentally produce a zero-match run.  If there is at least one match,
        # use the scoped list.
        fail_open_min = max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_FAIL_OPEN_MIN_MATCHES") or 20, 20))
        used_fail_open = bool(allowed and matches and len(filtered) < fail_open_min)
        final_matches = matches if used_fail_open else filtered
        scoped_meta = dict(meta)
        scoped_meta["top_inventory_runtime_scope"] = {
            **info,
            "enabled": True,
            "input_matches": len(matches),
            "output_matches": len(final_matches),
            "filtered_out": max(0, len(matches) - len(final_matches)),
            "fail_open": used_fail_open,
            "fail_open_min_matches": fail_open_min,
        }
        _write_json(REPORT_PATH, scoped_meta["top_inventory_runtime_scope"])
        if len(result) == 1:
            return (final_matches,)
        return (final_matches, scoped_meta, *result[2:])

    setattr(fetch_matches_top_inventory_scoped, _MARKER, True)
    setattr(fetch_matches_top_inventory_scoped, "_harizon_original_fetch_matches", original)
    PredictionRunner._fetch_matches = fetch_matches_top_inventory_scoped  # type: ignore[assignment]
    allowed, info = _inventory_allowlist()
    _write_json(REPORT_PATH, {**info, "enabled": True, "installed": True})
    return {"status": "installed", **info}
