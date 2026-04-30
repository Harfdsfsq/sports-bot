from __future__ import annotations

"""Patch controlled fallback to use independent context confirmations.

Before this patch controlled fallback used `sources_count` from the odds
candidate.  That usually equals 1 (`odds_api_io`) even when the match has
context from SStats/Bzzoiro/Weather/football-data.  This patch enriches metrics
with `confirmation_sources_count` from `.data/exports/latest-context-source-index.json`.
"""

from pathlib import Path

TARGET = Path(__file__).resolve().with_name("publish_controlled_fallback.py")

HELPER = r'''

_CONTEXT_SOURCE_INDEX_CACHE: dict[str, Any] | None = None

def load_context_source_index() -> dict[str, Any]:
    global _CONTEXT_SOURCE_INDEX_CACHE
    if _CONTEXT_SOURCE_INDEX_CACHE is not None:
        return _CONTEXT_SOURCE_INDEX_CACHE
    paths = [
        Path(".data/exports/latest-context-source-index.json"),
        Path(".data/provider_cache/context-source-index/latest.json"),
    ]
    # Build on demand if the index is absent.  This runs after run-once in the
    # controlled fallback process, so .logs/debug-last-run.json is available.
    if not any(path.exists() for path in paths):
        try:
            import importlib.util
            builder_path = Path("scripts/build_context_source_index.py")
            if not builder_path.exists():
                builder_path = Path(__file__).resolve().with_name("build_context_source_index.py")
            if builder_path.exists():
                spec = importlib.util.spec_from_file_location("harizon_context_source_index_builder", builder_path)
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    build_main = getattr(module, "main", None)
                    if callable(build_main):
                        build_main()
        except Exception:
            pass
    for path in paths:
        payload = load_json(path, {})
        if isinstance(payload, dict) and isinstance(payload.get("by_match"), dict):
            _CONTEXT_SOURCE_INDEX_CACHE = payload
            return payload
    _CONTEXT_SOURCE_INDEX_CACHE = {}
    return {}


def normalize_confirmation_source(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    aliases = {
        "sstats": "sstats",
        "bzzoiro": "bzzoiro",
        "weather": "weather",
        "weatherapi": "weather",
        "openweathermap": "weather",
        "openmeteo": "weather",
        "meteostat": "weather",
        "football_data": "football_data",
        "football_data_org": "football_data",
        "thesportsdb": "thesportsdb",
        "espn": "espn",
        "futrixmetrics": "futrixmetrics",
        "gnews": "gnews",
        "newsapi": "newsapi",
        "currents": "newsapi",
        "sportlogic": "sportlogic",
        "scorebat": "scorebat",
        "openfootball": "openfootball",
        "clubelo": "clubelo",
        "wikidata": "wikidata",
        "guardian": "guardian",
        "highlightly": "highlightly",
    }
    if text in aliases:
        return aliases[text]
    for needle, canonical in aliases.items():
        if needle in text:
            return canonical
    return None


def candidate_confirmation_sources(candidate: dict[str, Any]) -> list[str]:
    sources: set[str] = set()
    match_key = str(candidate.get("match_key") or "").strip().lower()
    index = load_context_source_index()
    by_match = index.get("by_match") if isinstance(index, dict) else {}
    if match_key and isinstance(by_match, dict):
        indexed = by_match.get(match_key) or []
        if isinstance(indexed, list):
            for item in indexed:
                src = normalize_confirmation_source(item)
                if src:
                    sources.add(src)
    for field in (
        "confirmation_sources",
        "context_sources",
        "context_source_names",
        "merged_context_sources",
        "providers",
        "provider_names",
    ):
        value = candidate.get(field)
        if isinstance(value, list):
            iterable = value
        elif isinstance(value, str):
            iterable = re.split(r"[,+;/|\s]+", value)
        else:
            iterable = []
        for item in iterable:
            src = normalize_confirmation_source(item)
            if src:
                sources.add(src)
    source_summary = candidate.get("source_summary") or {}
    if isinstance(source_summary, dict):
        for field in ("context_sources", "providers", "confirmation_sources"):
            value = source_summary.get(field)
            if isinstance(value, list):
                iterable = value
            elif isinstance(value, str):
                iterable = re.split(r"[,+;/|\s]+", value)
            else:
                iterable = []
            for item in iterable:
                src = normalize_confirmation_source(item)
                if src:
                    sources.add(src)
    # Odds source is not an independent confirmation source.
    sources.discard("odds_api_io")
    sources.discard("market")
    return sorted(sources)
'''


def patch_text(src: str) -> str:
    original = src
    if "def load_context_source_index()" not in src:
        marker = "def as_float(value: Any, default: float = 0.0) -> float:\n"
        if marker in src:
            src = src.replace(marker, HELPER + "\n" + marker, 1)

    old = "    sources = as_int(candidate.get(\"sources_count\"), 0)\n"
    new = (
        "    odds_sources = as_int(candidate.get(\"odds_sources_count\"), as_int(candidate.get(\"sources_count\"), 0))\n"
        "    raw_sources = as_int(candidate.get(\"sources_count\"), 0)\n"
        "    confirmation_sources = candidate_confirmation_sources(candidate) if env_bool(\"CONTROLLED_FALLBACK_USE_CONTEXT_SOURCE_INDEX\", True) else []\n"
        "    confirmation_sources_count = max(raw_sources, len(confirmation_sources))\n"
        "    sources = confirmation_sources_count\n"
    )
    if old in src and "confirmation_sources_count = max(raw_sources" not in src:
        src = src.replace(old, new, 1)

    old_return = "        \"books_count\": books,\n        \"sources_count\": sources,\n"
    new_return = (
        "        \"books_count\": books,\n"
        "        \"odds_sources_count\": odds_sources,\n"
        "        \"sources_count\": sources,\n"
        "        \"confirmation_sources_count\": confirmation_sources_count,\n"
        "        \"confirmation_sources\": confirmation_sources,\n"
    )
    if old_return in src and "\"confirmation_sources_count\": confirmation_sources_count" not in src:
        src = src.replace(old_return, new_return, 1)

    # Rename reject reason text to make reports explicit while keeping previous translations usable.
    src = src.replace(
        'controlled_fallback_sources_below_min:{int(metrics.get("sources_count") or 0)}/{min_sources}',
        'controlled_fallback_confirmation_sources_below_min:{int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0)}/{min_sources}',
    )
    src = src.replace(
        'if int(metrics.get("sources_count") or 0) < min_sources:',
        'if int(metrics.get("confirmation_sources_count", metrics.get("sources_count") or 0) or 0) < min_sources:',
    )
    return src


def main() -> int:
    if not TARGET.exists():
        print(f"skip: {TARGET} not found")
        return 0
    src = TARGET.read_text(encoding="utf-8")
    updated = patch_text(src)
    if updated != src:
        TARGET.write_text(updated, encoding="utf-8")
        print(f"patched: {TARGET}")
    else:
        print(f"already patched or no changes: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
