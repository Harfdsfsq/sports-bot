"""Git merge driver for generated HARIZON runtime JSON.

Usage is configured through ``.gitattributes`` and repository-local Git config:
``python scripts/merge_runtime_json.py %O %A %B %L %P``.
The result is always written to ``%A``. Source code is intentionally not covered by
this driver.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _load(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in CONFLICT_MARKERS):
            return None
        return json.loads(text)
    except Exception:
        return None


def _timestamp(payload: Any) -> datetime:
    if isinstance(payload, dict):
        for key in (
            "updated_at_utc",
            "created_at_utc",
            "highwater_updated_at_utc",
            "target_expand_updated_at_utc",
            "blank_rows_repaired_at_utc",
        ):
            raw = payload.get(key)
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except Exception:
                continue
    return datetime.min.replace(tzinfo=UTC)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-я]+", " ", str(value or "").lower()).strip()


def _row_key(row: dict[str, Any]) -> str:
    for key in ("semantic_match_key", "canonical_match_key", "canonical_match_id", "match_key", "event_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    kickoff = str(
        row.get("kickoff_utc")
        or row.get("commence_time")
        or row.get("start_time")
        or row.get("kickoff")
        or row.get("event_date")
        or row.get("date")
        or ""
    )[:16]
    home = _norm(row.get("home_team") or row.get("home") or row.get("home_name"))
    away = _norm(row.get("away_team") or row.get("away") or row.get("away_name"))
    return "|".join(part for part in (kickoff, home, away) if part)


def _row_score(row: dict[str, Any]) -> tuple[int, int, int, str]:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    strict = int(bool(coverage.get("strict_coverage_ready") or row.get("tier_a_coverage_ready")))
    counts = 0
    for container in (row, coverage, metadata):
        if not isinstance(container, dict):
            continue
        for key in (
            "odds_sources_count",
            "context_sources_count",
            "books_count",
            "price_confirmation_sources_count",
        ):
            try:
                counts += min(5, int(float(container.get(key) or 0)))
            except Exception:
                pass
        for key in ("odds_sources", "context_sources", "books", "bookmakers"):
            value = container.get(key)
            if isinstance(value, (list, tuple, set, dict)):
                counts += min(5, len(value))
    richness = len(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
    kickoff = str(row.get("kickoff_utc") or row.get("commence_time") or "")
    return strict, counts, richness, kickoff


def _merge_dict(left: dict[str, Any], right: dict[str, Any], *, prefer_right: bool) -> dict[str, Any]:
    result = dict(left)
    for key, right_value in right.items():
        if key not in result:
            result[key] = right_value
            continue
        left_value = result[key]
        if isinstance(left_value, dict) and isinstance(right_value, dict):
            result[key] = _merge_dict(left_value, right_value, prefer_right=prefer_right)
        elif isinstance(left_value, list) and isinstance(right_value, list):
            if key == "matches":
                result[key] = _merge_matches(left_value, right_value)
            elif all(not isinstance(item, (dict, list)) for item in left_value + right_value):
                result[key] = list(dict.fromkeys([*left_value, *right_value]))
            else:
                result[key] = right_value if prefer_right else left_value
        elif right_value not in (None, "", [], {}) and (prefer_right or left_value in (None, "", [], {})):
            result[key] = right_value
    return result


def _merge_matches(left: list[Any], right: list[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    unkeyed = 0
    for row in [*left, *right]:
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        if not key:
            unkeyed += 1
            key = f"__unkeyed__{unkeyed}:{hash(json.dumps(row, sort_keys=True, default=str))}"
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        prefer_row = _row_score(row) >= _row_score(current)
        merged[key] = _merge_dict(current, row, prefer_right=prefer_row)
    rows = list(merged.values())
    rows.sort(key=_row_score, reverse=True)
    return rows[:300]


def merge_payloads(base: Any, current: Any, other: Any, path_hint: str = "") -> Any:
    valid = [payload for payload in (current, other, base) if payload is not None]
    if not valid:
        return {}
    if len(valid) == 1:
        return valid[0]

    left = current if current is not None else base
    right = other if other is not None else base
    if isinstance(left, dict) and isinstance(right, dict):
        prefer_right = _timestamp(right) >= _timestamp(left)
        merged = _merge_dict(left, right, prefer_right=prefer_right)
        if "matches" in merged and isinstance(merged.get("matches"), list):
            target = 300
            for payload in (left, right, base):
                if not isinstance(payload, dict):
                    continue
                try:
                    target = max(target, int(float(payload.get("target_matches") or 0)))
                except Exception:
                    pass
            merged["matches"] = merged["matches"][:target]
            counts = merged.get("counts") if isinstance(merged.get("counts"), dict) else {}
            counts["matches_total"] = len(merged["matches"])
            counts["runtime_json_merge_driver_applied"] = True
            merged["counts"] = counts
        merged["runtime_json_merge_driver"] = {
            "status": "merged",
            "path": path_hint,
            "current_timestamp": _timestamp(left).isoformat(),
            "other_timestamp": _timestamp(right).isoformat(),
        }
        return merged
    if isinstance(left, list) and isinstance(right, list):
        if all(isinstance(item, dict) for item in left + right):
            return _merge_matches(left, right)
        return list(dict.fromkeys([*left, *right]))
    return right if _timestamp(right) >= _timestamp(left) else left


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 3:
        print("usage: merge_runtime_json.py BASE CURRENT OTHER [MARKER_SIZE] [PATH]", file=sys.stderr)
        return 2
    base_path, current_path, other_path = map(Path, args[:3])
    path_hint = args[4] if len(args) >= 5 else str(current_path)
    merged = merge_payloads(_load(base_path), _load(current_path), _load(other_path), path_hint)
    current_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
