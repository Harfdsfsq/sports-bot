#!/usr/bin/env python3
"""Count matches in the cached day inventory whose kickoff is still ahead.

The run-bot workflow restores ``.data/day_inventory/*.json`` from the Actions
cache on every run.  Because the files are therefore always present, the old
"rebuild only when the files are missing" condition never fired and the bot kept
analysing a snapshot that consisted entirely of matches that had already kicked
off.

This helper prints a single integer on stdout: the number of matches that are
still upcoming (``kickoff_utc >= now - grace``) in the freshest inventory file.
A JSON diagnostic line is written to stderr.  The workflow uses the stdout value
to decide whether the day inventory has to be rebuilt from scratch.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

INVENTORY_DIR = Path(os.getenv("DAY_INVENTORY_DIR", ".data/day_inventory"))
CANDIDATE_FILES = ("today.json", "current.json", "latest.json")
KICKOFF_KEYS = (
    "kickoff_utc",
    "kickoff",
    "commence_time",
    "start_time",
    "start_utc",
    "date_utc",
)
ROW_KEYS = ("matches", "rows", "items", "inventory", "data", "fixtures")


def _grace_minutes() -> float:
    raw = os.getenv("DAY_INVENTORY_STARTED_GRACE_MINUTES", "15")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return 15.0
    return value if value > 0 else 0.0


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_rows(payload):
    rows = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ROW_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
    for row in rows:
        if isinstance(row, dict):
            yield row


def _kickoff(row):
    for key in KICKOFF_KEYS:
        parsed = _parse_dt(row.get(key))
        if parsed is not None:
            return parsed
    return None


def count_upcoming(path: Path, cutoff: datetime):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return None, 0, f"unreadable: {exc.__class__.__name__}"
    total = 0
    upcoming = 0
    for row in _iter_rows(payload):
        total += 1
        kickoff = _kickoff(row)
        if kickoff is not None and kickoff >= cutoff:
            upcoming += 1
    return upcoming, total, "ok"


def main() -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_grace_minutes())
    best = 0
    files = {}
    for name in CANDIDATE_FILES:
        path = INVENTORY_DIR / name
        if not path.exists() or path.stat().st_size == 0:
            files[name] = {"status": "missing_or_empty"}
            continue
        upcoming, total, status = count_upcoming(path, cutoff)
        files[name] = {"status": status, "total": total, "upcoming": upcoming}
        if upcoming is not None and upcoming > best:
            best = upcoming
    diagnostics = {
        "now_utc": now.isoformat(),
        "cutoff_utc": cutoff.isoformat(),
        "grace_minutes": _grace_minutes(),
        "inventory_dir": str(INVENTORY_DIR),
        "files": files,
        "upcoming": best,
    }
    print(json.dumps(diagnostics, ensure_ascii=False), file=sys.stderr)
    try:
        report = Path(".data/exports/latest-upcoming-inventory-count.json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - the stdout value is what matters
        pass
    print(best)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
