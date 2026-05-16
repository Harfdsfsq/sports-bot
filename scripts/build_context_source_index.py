from __future__ import annotations

"""Build per-match independent context source index.

The runner already merges context from providers, but some downstream scripts
only see `sources_count` from odds candidates.  This exporter walks debug and
inventory artifacts and writes a compact index that controlled fallback can use
as `confirmation_sources_count`.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
OUT_PATHS = [
    ROOT / ".data" / "exports" / "latest-context-source-index.json",
    ROOT / ".data" / "provider_cache" / "context-source-index" / "latest.json",
]
DAY_SUMMARY_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-summary.json"

SOURCE_ALIASES = {
    "sstats": "sstats",
    "bzzoiro": "bzzoiro",
    "weather": "weather",
    "weatherapi": "weather",
    "openweathermap": "weather",
    "openmeteo": "weather",
    "open-meteo": "weather",
    "meteostat": "weather",
    "football_data": "football_data",
    "football-data": "football_data",
    "football_data_org": "football_data",
    "thesportsdb": "thesportsdb",
    "sportsdb": "thesportsdb",
    "espn": "espn",
    "futrixmetrics": "futrixmetrics",
    "gnews": "gnews",
    "newsapi": "newsapi",
    "currents": "newsapi",
    "sportlogic": "sportlogic",
    "scorebat": "scorebat",
    "openfootball": "openfootball",
    "clubelo": "clubelo",
    "football_data_uk": "football_data_uk",
    "wikidata": "wikidata",
    "guardian": "guardian",
    "highlightly": "highlightly",
}

MATCH_KEY_RE = re.compile(r"^soccer\|.+\|.+\|\d{4}-\d{2}-\d{2}$")
SOURCE_FIELDS = {
    "source",
    "provider",
    "provider_name",
    "context_source",
    "context_provider",
    "source_name",
    "origin",
}
SOURCE_LIST_FIELDS = {
    "sources",
    "context_sources",
    "context_source_names",
    "providers",
    "provider_names",
    "merged_context_sources",
}
MATCH_KEY_FIELDS = {
    "match_key",
    "key",
    "fixture_key",
    "event_key",
    "canonical_match_key",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def payload_date(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("date_local")
    if value:
        return str(value)
    matches = payload.get("matches")
    if isinstance(matches, list) and matches:
        first = matches[0]
        if isinstance(first, dict):
            return str(first.get("date_local") or "")[:10]
    return ""


def normalize_source(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in SOURCE_ALIASES:
        return SOURCE_ALIASES[text]
    for needle, canonical in SOURCE_ALIASES.items():
        if needle in text:
            return canonical
    return None


def is_match_key(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(MATCH_KEY_RE.match(text))


def add_source(index: dict[str, set[str]], evidence: dict[str, list[str]], match_key: str, source: str, reason: str) -> None:
    if not match_key or not source:
        return
    index[match_key].add(source)
    if len(evidence[match_key]) < 12:
        evidence[match_key].append(reason)


def sources_from_value(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str):
        # Handles strings like "sstats+bzzoiro+weather".
        for part in re.split(r"[,+;/|\s]+", value):
            src = normalize_source(part)
            if src:
                result.add(src)
        src = normalize_source(value)
        if src:
            result.add(src)
    elif isinstance(value, list):
        for item in value:
            result |= sources_from_value(item)
    elif isinstance(value, dict):
        for field in SOURCE_FIELDS | SOURCE_LIST_FIELDS:
            if field in value:
                result |= sources_from_value(value.get(field))
    return result


def match_keys_from_dict(obj: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in MATCH_KEY_FIELDS:
        value = obj.get(field)
        if is_match_key(value):
            keys.add(str(value).strip().lower())
    return keys


def walk(obj: Any, index: dict[str, set[str]], evidence: dict[str, list[str]], path: str = "root") -> None:
    if isinstance(obj, dict):
        # Mapping form: {"soccer|...": {source/context payload}}
        for key, value in obj.items():
            if is_match_key(key):
                for src in sources_from_value(value):
                    add_source(index, evidence, str(key).strip().lower(), src, f"mapping:{path}.{key}")
                # Provider-keyed payload: {match_key: {sstats: {...}, weather: {...}}}
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        src = normalize_source(sub_key)
                        if src and sub_value not in (None, {}, [], ""):
                            add_source(index, evidence, str(key).strip().lower(), src, f"provider_key:{path}.{key}.{sub_key}")

        keys = match_keys_from_dict(obj)
        sources: set[str] = set()
        for field in SOURCE_FIELDS | SOURCE_LIST_FIELDS:
            if field in obj:
                sources |= sources_from_value(obj.get(field))
        # Provider-keyed object with its own match_key.
        for field, value in obj.items():
            src = normalize_source(field)
            if src and value not in (None, {}, [], ""):
                sources.add(src)
        for match_key in keys:
            for src in sources:
                add_source(index, evidence, match_key, src, f"object:{path}")

        for key, value in obj.items():
            walk(value, index, evidence, f"{path}.{key}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            walk(item, index, evidence, f"{path}[{idx}]")


def build_index() -> dict[str, Any]:
    summary = load_json(DAY_SUMMARY_PATH, {})
    current_date = str(summary.get("date_local") or "").strip() if isinstance(summary, dict) else ""
    raw_paths = [
        ROOT / ".logs" / "debug-last-run.json",
        ROOT / ".data" / "exports" / "latest-run-summary.json",
        ROOT / ".data" / "exports" / "latest-day-inventory-coverage-merge.json",
        ROOT / ".data" / "exports" / "latest-day-inventory-coverage-audit.json",
    ]
    if current_date:
        raw_paths.append(ROOT / ".data" / "day_inventory" / f"{current_date}.json")
    raw_paths.extend([
        ROOT / ".data" / "day_inventory" / "current.json",
        ROOT / ".data" / "day_inventory" / "today.json",
        ROOT / ".data" / "day_inventory" / "latest.json",
    ])
    index: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, list[str]] = defaultdict(list)
    loaded: list[str] = []
    for path in raw_paths:
        payload = load_json(path, None)
        if payload is None:
            continue
        if path.name in {"current.json", "today.json", "latest.json"} and current_date:
            seen_date = payload_date(payload)
            if seen_date and seen_date != current_date:
                continue
        loaded.append(path.as_posix())
        walk(payload, index, evidence, path.as_posix())

    by_match = {
        key: sorted(src for src in sources if src not in {"odds_api_io", "market"})
        for key, sources in sorted(index.items())
    }
    by_match = {key: sources for key, sources in by_match.items() if sources}
    source_counts: dict[str, int] = defaultdict(int)
    for sources in by_match.values():
        for src in sources:
            source_counts[src] += 1
    return {
        "status": "ok",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "loaded_paths": loaded,
        "matches_indexed": len(by_match),
        "source_counts": dict(sorted(source_counts.items())),
        "by_match": by_match,
        "evidence": {key: evidence[key] for key in list(by_match.keys())[:80]},
        "notes": [
            "Sources here are independent context/confirmation providers, not bookmaker counts.",
            "Controlled fallback uses this as confirmation_sources_count when available.",
        ],
    }


def main() -> int:
    payload = build_index()
    for path in OUT_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
