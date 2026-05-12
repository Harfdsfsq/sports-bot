from __future__ import annotations

"""Source-id bridge for Bzzoiro context gap pass.

Provider day discovery can preserve Bzzoiro source ids in day inventory, but
runtime Match objects passed to Bzzoiro gap-pass may not carry those ids in their
metadata. This bridge falls back to `.data/day_inventory/*.json` by match_key and
canonical ids.

Important: only Bzzoiro aliases are accepted as Bzzoiro ids. Odds/SStats ids must
never be used in `/api/v2/events/{id}/`; doing so produces 404s and can make the
whole pass waste requests.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
REPORT_PATH = ROOT / ".data" / "exports" / "latest-bzzoiro-context-gap-source-id-finalizer.json"

_CACHE: dict[str, str] | None = None
BZZ_KEYS = {"bzzoiro", "bzzoiro_v1", "bzzoiro_v2", "bzzoiro_predictions", "bzzoiro_predictions_v2", "bsd", "bsd_v2"}
ID_KEYS = {"id", "event_id", "source_event_id", "api_id", "uuid"}


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clean_id(value: Any) -> str | None:
    if value in (None, "", False, [], {}):
        return None
    if isinstance(value, dict):
        for key in ID_KEYS:
            nested = value.get(key)
            if nested not in (None, "", False, [], {}):
                return _clean_id(nested)
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            nested = _clean_id(item)
            if nested:
                return nested
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return None
    # Prevent accidental stringified dict/list ids like "{'sstats': '1544266'}".
    if text.startswith("{") or text.startswith("[") or ":" in text:
        return None
    return text


def _extract_bzzoiro_id_from_container(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in BZZ_KEYS:
        if key in value:
            nested = _clean_id(value.get(key))
            if nested:
                return nested
    return None


def _row_has_bzzoiro_source(row: dict[str, Any]) -> bool:
    for key in ("sources_seen", "fixture_sources", "context_sources", "xg_sources", "odds_sources"):
        raw = row.get(key)
        parts = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").replace("|", ",").split(",")
        if any(str(item).strip().lower().startswith(("bzzoiro", "bsd")) for item in parts):
            return True
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw = meta.get("sources_seen") or meta.get("fixture_sources") or meta.get("context_sources")
    parts = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").replace("|", ",").split(",")
    return any(str(item).strip().lower().startswith(("bzzoiro", "bsd")) for item in parts)


def _row_bzzoiro_id(row: dict[str, Any]) -> str | None:
    # 1) Explicit Bzzoiro keys in source-id containers.
    for container_key in ("provider_source_ids", "source_ids", "provider_ids"):
        value = row.get(container_key)
        found = _extract_bzzoiro_id_from_container(value) if isinstance(value, dict) else None
        if found:
            return found
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for container_key in ("provider_source_ids", "source_ids", "provider_ids"):
        value = meta.get(container_key)
        found = _extract_bzzoiro_id_from_container(value) if isinstance(value, dict) else None
        if found:
            return found
    # 2) Raw source_event_id only if row itself is explicitly from Bzzoiro.
    raw = row.get("source_event_id")
    if str(row.get("source") or "").lower() in {"bzzoiro", "bzzoiro_v1", "bzzoiro_v2", "bsd", "bsd_v2"} and raw not in (None, ""):
        return _clean_id(raw)
    # 3) Some discovery rows store a generic event id. Accept it only when the
    # row sources explicitly include Bzzoiro; never use generic day_inventory,
    # odds_api_io or sstats ids as Bzzoiro ids.
    if _row_has_bzzoiro_source(row):
        for key in ("bzzoiro_event_id", "event_id", "id"):
            found = _clean_id(row.get(key))
            if found:
                return found
    return None


def _row_keys(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("match_key", "canonical_match_id", "canonical_match_key"):
        value = row.get(key)
        if value not in (None, ""):
            keys.add(str(value))
    return keys


def _inventory_paths() -> list[Path]:
    paths: list[Path] = []
    for name in ("latest.json", "current.json", "today.json"):
        path = DAY_INV_DIR / name
        if path not in paths:
            paths.append(path)
    if DAY_INV_DIR.exists():
        for path in sorted(DAY_INV_DIR.glob("*.json")):
            if path not in paths and path.name not in {"progressive_coverage_state.json"}:
                paths.append(path)
    return paths


def _load_map() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    mapping: dict[str, str] = {}
    rows_seen = 0
    rows_with_id = 0
    skipped_non_bzz_rows = 0
    for path in _inventory_paths():
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rows_seen += 1
            bzz_id = _row_bzzoiro_id(row)
            if not bzz_id:
                skipped_non_bzz_rows += 1
                continue
            rows_with_id += 1
            for key in _row_keys(row):
                mapping.setdefault(key, bzz_id)
    _CACHE = mapping
    _write_json(REPORT_PATH, {
        "status": "indexed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_paths": [str(p) for p in _inventory_paths()],
        "rows_seen": rows_seen,
        "rows_with_bzzoiro_id": rows_with_id,
        "keys_indexed": len(mapping),
        "skipped_without_bzzoiro_id": skipped_non_bzz_rows,
        "note": "only explicit Bzzoiro aliases are accepted; odds_api_io/sstats ids are ignored",
    })
    return mapping


def _match_key(match: Any) -> str:
    try:
        value = getattr(match, "match_key", None)
        if value not in (None, ""):
            return str(value)
    except Exception:
        pass
    if isinstance(match, dict):
        for key in ("match_key", "canonical_match_id", "canonical_match_key"):
            value = match.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _direct_is_safe(value: Any) -> str | None:
    # Direct value from the old gap helper is accepted only when it is a scalar
    # clean id, not a stringified provider dict.
    return _clean_id(value)


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.services import bzzoiro_context_gap_finalizer as gap
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload

    current = getattr(gap, "_bzzoiro_id_from_match", None)
    if getattr(current, "_harizon_inventory_source_id_bridge_v2", False):
        payload.update({"status": "already_installed"})
        _write_json(REPORT_PATH, payload)
        return payload

    def bzzoiro_id_from_match_inventory_bridge(match: Any) -> str | None:
        try:
            direct = current(match) if callable(current) else None
        except Exception:
            direct = None
        safe_direct = _direct_is_safe(direct)
        if safe_direct:
            return safe_direct
        key = _match_key(match)
        if not key:
            return None
        return _load_map().get(key)

    bzzoiro_id_from_match_inventory_bridge._harizon_inventory_source_id_bridge_v2 = True  # type: ignore[attr-defined]
    gap._bzzoiro_id_from_match = bzzoiro_id_from_match_inventory_bridge
    indexed = _load_map()
    payload.update({"status": "installed", "keys_indexed": len(indexed)})
    _write_json(REPORT_PATH, payload)
    return payload
