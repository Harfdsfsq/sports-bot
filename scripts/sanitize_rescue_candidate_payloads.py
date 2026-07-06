from __future__ import annotations

"""Normalize fallback/rescue candidates before guarded publication.

This is a safety repair, not a publisher.  It fixes malformed candidate payloads
created by promotion layers, especially totals points like 25.0 that really mean
2.5 and pseudo-bookmakers such as under_2.5/over_2.5 that came from market labels.
The guarded fallback still applies all value, xG, line, duplicate and daily-cap
checks afterwards.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
OUT = EXPORT / "latest-rescue-candidate-sanitizer.json"
TARGETS = [
    EXPORT / "latest-rescue-candidates.json",
    EXPORT / "latest-a-cover-value-promotion.json",
    EXPORT / "latest-b-cover-value-promotion.json",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-я.]+", "_", str(value or "").strip().lower()).strip("_")


def invalid_bookmaker(value: Any) -> bool:
    text = norm(value)
    if not text:
        return True
    if text in {"over", "under", "больше", "меньше", "home", "away", "draw"}:
        return True
    if re.match(r"^(over|under|больше|меньше|tb|tm|тб|тм)_?\d", text):
        return True
    if re.match(r"^(over|under)_\d+(?:\.\d+)?$", text):
        return True
    return False


def normalize_point(value: Any) -> tuple[Any, bool]:
    try:
        f = float(str(value).replace(",", "."))
    except Exception:
        return value, False
    original = f
    # Promotion artifacts occasionally store soccer totals as 25/30/35 instead
    # of 2.5/3.0/3.5.  Values above 10 are not valid public soccer totals here.
    if 10 <= abs(f) <= 100 and abs(f) % 5 == 0:
        f = f / 10.0
    if f != original:
        return (int(f) if f.is_integer() else f), True
    return value, False


def rows_from(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None
    if isinstance(payload, dict):
        for key in ("candidates", "rows", "items", "sample", "selected_all"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)], key
    return [], None


def replace_rows(payload: Any, rows: list[dict[str, Any]], key: str | None) -> Any:
    if isinstance(payload, list):
        return rows
    if isinstance(payload, dict) and key:
        clone = dict(payload)
        clone[key] = rows
        return clone
    return payload


def candidate_bookmaker(row: dict[str, Any]) -> str:
    summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
    for source in (
        summary.get("selected_bookmaker"),
        summary.get("bookmaker"),
        row.get("selected_bookmaker"),
        row.get("bookmaker"),
    ):
        if not invalid_bookmaker(source):
            return str(source)
    books = summary.get("books") if isinstance(summary.get("books"), list) else []
    for book in books:
        if not invalid_bookmaker(book):
            return str(book)
    for offer in summary.get("raw_bucket_offers") or row.get("raw_bucket_offers") or []:
        if isinstance(offer, dict) and not invalid_bookmaker(offer.get("bookmaker")):
            return str(offer.get("bookmaker"))
    return ""


def sanitize_row(row: dict[str, Any]) -> int:
    changed = 0
    point, point_changed = normalize_point(row.get("point") or row.get("line") or row.get("handicap"))
    if point_changed:
        row["point"] = point
        changed += 1
        summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
        for offer in summary.get("raw_bucket_offers") or row.get("raw_bucket_offers") or []:
            if isinstance(offer, dict):
                offer_point, offer_changed = normalize_point(offer.get("point"))
                if offer_changed:
                    offer["point"] = offer_point

    if invalid_bookmaker(row.get("bookmaker")):
        replacement = candidate_bookmaker(row)
        if replacement:
            row["bookmaker"] = replacement
            changed += 1
    summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
    if summary:
        if invalid_bookmaker(summary.get("selected_bookmaker")) or invalid_bookmaker(summary.get("bookmaker")):
            replacement = candidate_bookmaker(row)
            if replacement:
                summary["selected_bookmaker"] = replacement
                summary["bookmaker"] = replacement
                changed += 1
        row["source_summary"] = summary
    if changed:
        diag = row.setdefault("diagnostics", {})
        if isinstance(diag, dict):
            diag["rescue_candidate_sanitizer"] = {"updated_at_utc": datetime.now(UTC).isoformat(), "point_normalized": point_changed}
    return changed


def main() -> int:
    files = []
    total_rows = total_changed = 0
    for path in TARGETS:
        payload = load_json(path, None)
        if payload is None:
            continue
        rows, key = rows_from(payload)
        changed = 0
        for row in rows:
            changed += sanitize_row(row)
        if changed:
            write_json(path, replace_rows(payload, rows, key))
        files.append({"path": str(path), "rows": len(rows), "changed": changed})
        total_rows += len(rows)
        total_changed += changed
    report = {"status": "ok", "updated_at_utc": datetime.now(UTC).isoformat(), "files": files, "total_rows": total_rows, "total_changed": total_changed}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
