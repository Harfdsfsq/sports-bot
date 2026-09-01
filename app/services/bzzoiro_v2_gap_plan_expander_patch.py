from __future__ import annotations

"""Expand live Bzzoiro v2 context fetches with 2+/2+ gap-plan rows.

The runner may pass only the current 2h scan window into Bzzoiro even when the
progressive coverage plan contains many day-inventory rows that still miss a
second line/context source.  This patch wraps the already patched v2 provider and
adds bounded synthetic Match objects from latest-progressive-coverage-plan.json so
Bzzoiro can try to match/fetch context and odds-comparison details for the actual
coverage gaps.

It does not change publication guards.  Rows still need normal matching, value,
line movement, xG and price-integrity checks before Telegram publication.
"""

import json
import os
import re
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any

from app.schemas import Match

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_DIR = ROOT / ".data" / "day_inventory"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
OUT = EXPORT_DIR / "latest-bzzoiro-v2-gap-plan-expander.json"
UTC = timezone.utc
_INSTALLED = False
_ORIGINAL_FETCH = None


def _truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("core_gap_sample", "gap_sample", "matches", "rows", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.extend(x for x in value.values() if isinstance(x, dict))
    return out


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def _source_count(row: dict[str, Any], *keys: str) -> int:
    values: list[str] = []
    for container in (row, row.get("coverage") if isinstance(row.get("coverage"), dict) else {}, row.get("metadata") if isinstance(row.get("metadata"), dict) else {}):
        if not isinstance(container, dict):
            continue
        for key in keys:
            values.extend(_items(container.get(key)))
    normalized = {_norm(x) for x in values if _norm(x)}
    return max(len(normalized), _to_int(next((row.get(k) for k in keys if row.get(k) not in (None, "", [], {})), 0), 0))


def _needs_gap(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    odds_needed = _to_int(row.get("core_odds_needed") or row.get("odds_needed"), 0)
    ctx_needed = _to_int(row.get("core_context_needed") or row.get("context_needed"), 0)
    odds_count = _source_count(row, "core_odds_sources", "odds_sources", "line_sources", "independent_odds_sources", "odds_sources_count")
    ctx_count = _source_count(row, "core_context_sources", "context_sources", "context_confirmations", "confirmation_sources", "context_sources_count")
    return odds_needed > 0 or ctx_needed > 0 or odds_count < 2 or ctx_count < 2


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
            return datetime.combine(datetime.fromisoformat(text).date(), time(12, 0), tzinfo=UTC)
        if "T" in text and "+" not in text and not text.endswith("Z"):
            text += "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _identity_from_key(key: str) -> dict[str, str]:
    parts = [p for p in str(key or "").split("|") if p]
    date = ""
    teams: list[str] = []
    for part in parts:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", part):
            date = part
        elif part.lower() not in {"soccer", "football", "teams"}:
            teams.append(part)
    return {
        "home_team": " ".join(str(teams[0] if len(teams) > 0 else "").replace("_", " ").split()).title(),
        "away_team": " ".join(str(teams[1] if len(teams) > 1 else "").replace("_", " ").split()).title(),
        "kickoff_utc": f"{date}T12:00:00+00:00" if date else "",
    }


def _match_key(row: dict[str, Any]) -> str:
    return str(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key") or "").strip()


def _match_from_row(row: dict[str, Any]) -> Match | None:
    key = _match_key(row)
    fallback = _identity_from_key(key)
    home = str(row.get("home_team") or row.get("home") or fallback.get("home_team") or "").strip()
    away = str(row.get("away_team") or row.get("away") or fallback.get("away_team") or "").strip()
    kickoff = _parse_datetime(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("event_date") or fallback.get("kickoff_utc"))
    if not home or not away or kickoff is None:
        return None
    now = datetime.now(UTC)
    if kickoff < now - timedelta(minutes=20) or kickoff > now + timedelta(days=2, hours=6):
        return None
    try:
        return Match(
            source="bzzoiro_gap_plan",
            source_event_id=str(row.get("source_event_id") or key or f"{home}-{away}-{kickoff.date().isoformat()}"),
            sport_key="soccer",
            league_name=str(row.get("league_name") or row.get("league") or ""),
            home_team=home,
            away_team=away,
            commence_time=kickoff,
            home_team_norm=_norm(home),
            away_team_norm=_norm(away),
            league_key=_norm(row.get("league_name") or row.get("league") or ""),
            tier=str(row.get("tier") or "mid"),
            metadata={"gap_plan_row": True, "original_match_key": key},
        )
    except Exception:
        return None


def _gap_matches(existing: list[Match], limit: int) -> tuple[list[Match], dict[str, Any]]:
    seen = {getattr(m, "match_key", "") for m in existing if getattr(m, "match_key", "")}
    rows: list[dict[str, Any]] = []
    rows.extend(_rows(_load_json(PLAN_PATH, {})))
    if not rows:
        for path in (DAY_DIR / "today.json", DAY_DIR / "current.json", DAY_DIR / "latest.json"):
            rows.extend(_rows(_load_json(path, {})))
    added: list[Match] = []
    skipped = {"not_needed": 0, "bad_match": 0, "duplicate": 0}
    for row in rows:
        if not _needs_gap(row):
            skipped["not_needed"] += 1
            continue
        match = _match_from_row(row)
        if match is None:
            skipped["bad_match"] += 1
            continue
        if match.match_key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(match.match_key)
        added.append(match)
        if len(added) >= limit:
            break
    return added, {
        "plan_rows_seen": len(rows),
        "added": len(added),
        "limit": limit,
        "skipped": skipped,
        "sample": [m.match_key for m in added[:30]],
    }


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FETCH
    if _INSTALLED:
        return {"installed": True, "already_installed": True}
    if not _truthy(os.getenv("HARIZON_BZZOIRO_V2_GAP_PLAN_EXPANDER_ENABLED"), True):
        return {"installed": False, "reason": "disabled"}
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider
    except Exception as exc:
        return {"installed": False, "error": f"import:{type(exc).__name__}: {exc}"}
    current = getattr(BzzoiroContextProvider, "fetch_context", None)
    if not callable(current):
        return {"installed": False, "reason": "fetch_context_missing"}
    if getattr(current, "_harizon_gap_plan_expander", False):
        _INSTALLED = True
        return {"installed": True, "already_patched": True}
    _ORIGINAL_FETCH = current

    async def wrapped_fetch_context(self, matches):  # type: ignore[no-untyped-def]
        base = list(matches or [])
        limit = max(0, _to_int(os.getenv("BZZOIRO_V2_GAP_PLAN_EXPAND_LIMIT") or os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT") or 300, 300))
        added, report = _gap_matches(base, max(0, limit - len(base)) if limit > len(base) else 0)
        expanded = base + added
        _write({
            "status": "installed_and_ran",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "input_matches": len(base),
            "output_matches": len(expanded),
            **report,
            "note": "Bzzoiro v2 context fetch expanded with progressive 2+/2+ gap-plan rows before provider matching.",
        })
        return await _ORIGINAL_FETCH(self, expanded)

    wrapped_fetch_context._harizon_gap_plan_expander = True  # type: ignore[attr-defined]
    BzzoiroContextProvider.fetch_context = wrapped_fetch_context  # type: ignore[assignment]
    _INSTALLED = True
    payload = {"installed": True, "created_at_utc": datetime.now(UTC).isoformat(), "wrapper": "bzzoiro_v2_gap_plan_expander"}
    _write({"status": "installed", **payload})
    return payload
