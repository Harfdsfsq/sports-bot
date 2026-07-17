from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / ".data"
DAY_DIR = DATA_DIR / "day_inventory"
EXPORT_DIR = DATA_DIR / "exports"
PLAN_PATH = EXPORT_DIR / "latest-daily-coverage-plan.json"
LEDGER_PATH = EXPORT_DIR / "latest-daily-coverage-ledger.json"
REPORT_PATH = EXPORT_DIR / "latest-daily-coverage-report.json"
EVIDENCE_PATH = EXPORT_DIR / "latest-daily-coverage-evidence.json"
TARGET_MATCHES = 300
PHASE_TARGETS = (150, 250, 300)
MIN_ODDS_SOURCES = 2
MIN_CONTEXT_SOURCES = 2

ALIASES = {
    "oddsapiio": "odds_api_io",
    "odds_api": "odds_api_io",
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "odds_api_io_1": "odds_api_io",
    "odds_api_io_2": "odds_api_io",
    "bzzoiro_v2": "bzzoiro",
    "bsd_sports": "bzzoiro",
    "sstats_v1": "sstats",
    "pari": "sstats_pari",
    "pari_ru": "sstats_pari",
    "sstats_pari_odds": "sstats_pari",
    "sport_logic": "sportlogic",
    "football-data": "football_data",
    "the_sports_db": "thesportsdb",
    "club_elo": "clubelo",
}
NON_CONTEXT = {
    "",
    "unknown",
    "day_inventory",
    "provider_day_discovery_canonical_pool",
    "providerdaydiscoverycanonicalpool",
    "merged",
    "self_history",
    "fixture",
    "inventory",
}
ODDS_SOURCE_ALLOWLIST = {
    "odds_api_io",
    "sstats_pari",
    "sportlogic",
    "bzzoiro",
    "bookies_api",
    "oddspapi",
    "allsportsapi",
    "sharpapi",
    "rapidapi_odds",
    "bookies_bootstrap",
}
CONTEXT_SOURCE_ALLOWLIST = {
    "sstats",
    "bzzoiro",
    "clubelo",
    "sportlogic",
    "football_data",
    "thesportsdb",
    "api_football",
    "futrixmetrics",
    "espn",
    "openligadb",
    "openfootball",
    "newsapi",
    "gnews",
    "weather",
}
PROVIDER_TIMEOUTS = {
    "odds_api_io": 95.0,
    "sstats_pari": 130.0,
    "sportlogic": 95.0,
    "sstats": 110.0,
    "bzzoiro": 55.0,
    "clubelo": 35.0,
    "football_data": 45.0,
    "espn": 35.0,
    "openligadb": 35.0,
    "thesportsdb": 35.0,
}


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(str(os.getenv("APP_TIMEZONE") or "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date(now: datetime | None = None) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()[:10]
    if explicit:
        return explicit
    return (now or datetime.now(UTC)).astimezone(app_timezone()).date().isoformat()


def canonical_source(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in raw:
        raw = raw.replace("__", "_")
    if raw.startswith("odds_api_io") or raw.startswith("oddsapiio"):
        return "odds_api_io"
    if raw.startswith("sstats_pari") or raw in {"pari", "pari_ru"}:
        return "sstats_pari"
    if raw.startswith("bzzoiro") or raw.startswith("bsd_sports"):
        return "bzzoiro"
    if raw.startswith("sportlogic") or raw.startswith("sport_logic"):
        return "sportlogic"
    if raw.startswith("clubelo") or raw.startswith("club_elo"):
        return "clubelo"
    return ALIASES.get(raw, raw)


def independent_sources(values: Any, *, role: str) -> list[str]:
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_values = list(values or [])
    else:
        raw_values = []
    allowlist = ODDS_SOURCE_ALLOWLIST if role == "odds" else CONTEXT_SOURCE_ALLOWLIST
    out: set[str] = set()
    for item in raw_values:
        source = canonical_source(item)
        if role == "context" and source in NON_CONTEXT:
            continue
        if source in allowlist:
            out.add(source)
    return sorted(out)


def inventory_paths(date_key: str) -> list[Path]:
    return [
        DAY_DIR / f"{date_key}.json",
        DAY_DIR / "today.json",
        DAY_DIR / "current.json",
        DAY_DIR / "latest.json",
        EXPORT_DIR / "latest-day-inventory.json",
    ]


def select_inventory(date_key: str) -> tuple[Path | None, list[dict[str, Any]]]:
    best: tuple[int, int, Path, list[dict[str, Any]]] | None = None
    for path in inventory_paths(date_key):
        payload = load(path, {})
        if not isinstance(payload, dict):
            continue
        rows = [row for row in payload.get("matches") or [] if isinstance(row, dict)]
        if not rows:
            continue
        score = (int(str(payload.get("date_local") or date_key)[:10] == date_key), len(rows))
        if best is None or score > best[:2]:
            best = (*score, path, rows)
    return (None, []) if best is None else (best[2], best[3])


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("match_key") or row.get("canonical_match_id") or row.get("semantic_match_key") or row.get("semantic_key") or "").strip()


def row_kickoff(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("kickoff_utc") or row.get("kickoff") or row.get("commence_time") or row.get("start_time"))


def ledger_path(date_key: str) -> Path:
    return DAY_DIR / f"daily-coverage-ledger-{date_key}.json"


def state_path(date_key: str) -> Path:
    return DAY_DIR / f"daily-coverage-state-{date_key}.json"


def evidence_path(date_key: str) -> Path:
    return DAY_DIR / f"daily-coverage-evidence-{date_key}.json"
