from __future__ import annotations

"""Expand and preserve HARIZON inventory up to the configured target.

The expander must select 300 real matches, not 300 provider aliases. Rows from
odds-api, SStats and Bzzoiro often carry different match_key/canonical ids for
the same fixture, so de-duplication is done by local date + normalized home/away
before falling back to provider ids. Runtime artifact aliases are also rewritten
so later no-shrink repair cannot restore stale pre-dedupe copies.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
DAY_DIR = ROOT / ".data" / "day_inventory"
CACHE_DAY_DIR = ROOT / ".data" / "cache" / "day_inventory"
ARTIFACT_DAY_DIR = ROOT / "artifacts" / "run-bot" / "day_inventory"
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT_PATH = EXPORT_DIR / "latest-day-inventory-target-expand.json"
HIGHWATER_NAMES = ("best-day-inventory-highwater.json", "highwater.json", "largest.json")
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
GENERIC_TEAM_TOKENS = {"fc", "sc", "cf", "fk", "ac", "cd", "club", "de", "la", "the", "w", "women", "u19", "u20", "u21", "ii", "2"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return max(minimum, int(default))
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, int(default))


def app_time_zone():
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return UTC


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit[:10]
    return datetime.now(app_time_zone()).date().isoformat()


def horizon_days() -> int:
    raw = os.getenv("DAY_INVENTORY_HORIZON_DAYS") or os.getenv("DAY_INVENTORY_TARGET_HORIZON_DAYS") or os.getenv("RUN_DAYS_AHEAD") or "2"
    try:
        return max(1, min(int(float(raw)), 4))
    except Exception:
        return 2


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in CONFLICT_MARKERS):
            return default
        return json.loads(text)
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е").replace("´", "'")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(p for p in text.split() if p not in GENERIC_TEAM_TOKENS)


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def local_date_from_dt(dt: datetime) -> str:
    return dt.astimezone(app_time_zone()).date().isoformat()


def row_date(row: dict[str, Any]) -> str:
    for key in ("kickoff_utc", "commence_time", "start_time", "kickoff", "event_date", "date"):
        value = row.get(key)
        if not value:
            continue
        if key == "date" and re.match(r"^20\d{2}-\d{2}-\d{2}$", str(value)[:10]):
            return str(value)[:10]
        dt = parse_dt(value)
        if dt:
            return local_date_from_dt(dt)
    for key in ("match_key", "canonical_match_id", "canonical_match_key", "event_key"):
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(row.get(key) or ""))
        if match:
            return match.group(1)
    return ""


def team_value(row: dict[str, Any], side: str) -> str:
    keys = ("home_team", "home", "home_name", "team_home", "match_home") if side == "home" else ("away_team", "away", "away_name", "team_away", "match_away")
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def in_horizon(row: dict[str, Any], start_day: str, days: int) -> bool:
    d = row_date(row)
    if not d:
        return True
    try:
        start = datetime.fromisoformat(start_day[:10]).date()
        current = datetime.fromisoformat(d[:10]).date()
    except Exception:
        return d == start_day
    return start <= current < start + timedelta(days=days)


def row_key(row: dict[str, Any]) -> str:
    day = row_date(row)
    home = norm(team_value(row, "home"))
    away = norm(team_value(row, "away"))
    if day and home and away:
        return f"{day}|{home}|{away}"
    raw = str(row.get("canonical_match_key") or "").strip()
    if raw and "|" in raw:
        parts = raw.split("|")
        if len(parts) >= 3:
            return f"{parts[0][:10]}|{norm(parts[1])}|{norm(parts[2])}"
    for key in ("canonical_match_id", "match_key", "event_key", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return norm(value)
    league = norm(row.get("league_name") or row.get("league") or row.get("competition"))
    return "|".join(x for x in (day, league, home, away) if x)


def richness_value(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return min(10, len(value))
    return 1 if value not in (None, "", False) else 0


def kickoff_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    dt = parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
    ts = int(dt.timestamp()) if dt else 9_999_999_999
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    richness = 0
    for container in (row, cov, md):
        if isinstance(container, dict):
            for key in ("odds_sources", "context_sources", "books", "bookmakers", "price_confirmations"):
                richness += richness_value(container.get(key))
    return (ts, -richness, norm(row.get("league_name") or row.get("league")), row_key(row))


def score_row(row: dict[str, Any]) -> int:
    score = 0
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        if container.get("odds") or container.get("has_odds") or container.get("with_odds"):
            score += 20
        if container.get("context") or container.get("has_context") or container.get("with_context"):
            score += 20
        for key in ("books", "bookmakers", "price_confirmations", "odds_sources", "context_sources"):
            score += richness_value(container.get(key))
    for key in ("home_team", "away_team", "commence_time", "kickoff_utc", "league_name"):
        if row.get(key):
            score += 1
    return score


def merge_row(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    base, other = (dict(new), old) if score_row(new) > score_row(old) else (dict(old), new)
    for key, value in other.items():
        if key not in base or base.get(key) in (None, "", [], {}):
            base[key] = value
        elif isinstance(base.get(key), dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(base[key])
            base[key] = merged
        elif isinstance(base.get(key), list) and isinstance(value, list):
            seen = {json.dumps(x, sort_keys=True, ensure_ascii=False, default=str) for x in base[key]}
            for item in value:
                sig = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                if sig not in seen:
                    base[key].append(item)
                    seen.add(sig)
    base["semantic_match_key"] = row_key(base)
    return base


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("matches", "fixtures", "events", "rows", "items", "canonical_matches"):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
    for key in ("by_match", "matches_by_key"):
        value = payload.get(key)
        if isinstance(value, dict):
            for k, row in value.items():
                if isinstance(row, dict):
                    clone = dict(row)
                    clone.setdefault("match_key", k)
                    out.append(clone)
    return out


def highwater_paths(day: str) -> list[Path]:
    return [
        *(DAY_DIR / name for name in HIGHWATER_NAMES),
        *(CACHE_DAY_DIR / name for name in HIGHWATER_NAMES),
        DAY_DIR / f"{day}-highwater.json",
        CACHE_DAY_DIR / f"{day}-highwater.json",
        ROOT / ".data" / "inventory_guard" / "best-day-inventory.json",
    ]


def candidate_paths(day: str) -> list[Path]:
    explicit = [
        DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json",
        CACHE_DAY_DIR / f"{day}.json", CACHE_DAY_DIR / "current.json", CACHE_DAY_DIR / "latest.json", CACHE_DAY_DIR / "today.json",
        *highwater_paths(day),
        EXPORT_DIR / "latest-day-inventory-cumulative-coverage.json",
        EXPORT_DIR / "latest-day-inventory-coverage-truth.json",
        EXPORT_DIR / "latest-run-summary.json",
        ROOT / ".logs" / "debug-last-run.json",
    ]
    for root in (DAY_DIR, CACHE_DAY_DIR, EXPORT_DIR):
        if root.exists():
            explicit.extend(sorted(root.glob("*.json"))[:300])
            explicit.extend(sorted(root.glob("*/*.json"))[:300])
    seen: set[Path] = set()
    out: list[Path] = []
    for path in explicit:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(path)
    return out


def collect_rows(day: str, days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    parse_errors: list[str] = []
    for path in candidate_paths(day):
        payload = load_json(path, None)
        if payload is None:
            if path.exists():
                parse_errors.append(str(path))
            continue
        accepted = 0
        for row in rows_from_payload(payload):
            if not isinstance(row, dict) or not in_horizon(row, day, days):
                continue
            key = row_key(row)
            if not key:
                continue
            clone = dict(row)
            clone.setdefault("semantic_match_key", key)
            by_key[key] = merge_row(by_key[key], clone) if key in by_key else clone
            accepted += 1
        if accepted:
            source_counts[str(path)] = accepted
    return sorted(by_key.values(), key=kickoff_sort_key), {"source_counts": source_counts, "parse_errors": parse_errors[:30]}


def best_existing_payload(day: str, days: int) -> dict[str, Any]:
    best: dict[str, Any] = {"date_local": day, "matches": [], "counts": {}}
    best_count = -1
    for path in candidate_paths(day):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        raw_rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        by_key: dict[str, dict[str, Any]] = {}
        for row in raw_rows:
            if isinstance(row, dict) and in_horizon(row, day, days):
                key = row_key(row)
                if key:
                    by_key[key] = merge_row(by_key[key], row) if key in by_key else dict(row)
        if len(by_key) > best_count:
            best_count = len(by_key)
            best = dict(payload)
            best["matches"] = sorted(by_key.values(), key=kickoff_sort_key)
    if not isinstance(best.get("counts"), dict):
        best["counts"] = {}
    best["date_local"] = day
    return best


def write_aliases(payload: dict[str, Any], day: str) -> list[str]:
    changed: list[str] = []
    for path in (
        DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json",
        CACHE_DAY_DIR / f"{day}.json", CACHE_DAY_DIR / "today.json", CACHE_DAY_DIR / "current.json", CACHE_DAY_DIR / "latest.json",
        ARTIFACT_DAY_DIR / f"{day}.json", ARTIFACT_DAY_DIR / "current.json", ARTIFACT_DAY_DIR / "latest.json", ARTIFACT_DAY_DIR / "today.json",
    ):
        write_json(path, payload)
        changed.append(str(path))
    return changed


def write_highwater(payload: dict[str, Any], day: str) -> list[str]:
    if not isinstance(payload.get("matches"), list) or not payload["matches"]:
        return []
    clone = dict(payload)
    clone["highwater_updated_at_utc"] = datetime.now(UTC).isoformat()
    changed: list[str] = []
    for path in highwater_paths(day):
        write_json(path, clone)
        changed.append(str(path))
    return changed


def _target_status(selected_count: int, collected_count: int, target: int) -> str:
    if selected_count >= target:
        return "ok_target_met"
    if collected_count >= target:
        return "ok_target_met_after_alias_repair"
    if selected_count > 0:
        return "partial_known_rows_only_provider_shortfall"
    return "no_known_rows"


def main() -> int:
    day = target_date()
    days = horizon_days()
    target = env_int("DAY_INVENTORY_TARGET_SIZE", env_int("DAY_INVENTORY_MAX_MATCHES", 300, 1), 1)
    rows, diagnostics = collect_rows(day, days)
    existing_payload = best_existing_payload(day, days)
    existing_rows = existing_payload.get("matches") if isinstance(existing_payload.get("matches"), list) else []
    before = len(existing_rows)
    selected_from_collected = rows[:target] if target > 0 else rows
    if len(selected_from_collected) >= before:
        selected = selected_from_collected
        payload = dict(existing_payload)
        payload["matches"] = selected
    else:
        selected = existing_rows
        payload = dict(existing_payload)
        payload["matches"] = selected
    counts = payload.setdefault("counts", {})
    counts["matches_total"] = len(selected)
    counts["matches_after_target_expand"] = len(selected)
    counts["target_matches"] = target
    counts["target_shortfall"] = max(0, target - len(selected))
    counts["target_expand_rows_collected"] = len(rows)
    counts["target_expand_existing_before"] = before
    counts["target_expand_no_shrink_applied"] = len(selected_from_collected) < before
    counts["target_expand_horizon_days"] = days
    counts["target_expand_semantic_keys"] = True
    counts["target_expand_artifact_aliases_written"] = True
    payload["date_local"] = day
    payload["inventory_horizon_days"] = days
    payload["target_matches"] = target
    payload["target_expand_updated_at_utc"] = datetime.now(UTC).isoformat()
    payload["target_expand_status"] = _target_status(len(selected), len(rows), target)
    changed_paths = write_aliases(payload, day)
    highwater_paths_written = write_highwater(payload, day)
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "horizon_inventory_expand_v5_semantic_artifact_aliases_status_truth",
        "target_date": day,
        "horizon_days": days,
        "target": target,
        "existing_before": before,
        "rows_collected": len(rows),
        "selected_from_collected": len(selected_from_collected),
        "matches_after": len(selected),
        "target_shortfall": max(0, target - len(selected)),
        "target_timezone": str(app_time_zone()),
        "status": payload["target_expand_status"],
        "no_shrink_applied": len(selected_from_collected) < before,
        "semantic_dedupe_key": "date_home_away_first",
        "artifact_aliases_written": True,
        "changed_paths": changed_paths,
        "highwater_paths": highwater_paths_written,
        **diagnostics,
    }
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
