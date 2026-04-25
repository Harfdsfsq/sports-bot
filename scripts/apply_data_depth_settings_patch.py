from __future__ import annotations

from pathlib import Path

CONFIG_PATH = Path("app/config.py")

FIELDS = [
    (
        "max_matches_for_odds_fetch",
        '    max_matches_for_odds_fetch: int = Field(default=70, validation_alias=AliasChoices("MAX_MATCHES_FOR_ODDS_FETCH"))\n',
    ),
    (
        "max_rescue_candidates_per_match",
        '    max_rescue_candidates_per_match: int = Field(default=22, validation_alias=AliasChoices("MAX_RESCUE_CANDIDATES_PER_MATCH"))\n',
    ),
    (
        "max_rescue_prefilter_candidates_export",
        '    max_rescue_prefilter_candidates_export: int = Field(default=1000, validation_alias=AliasChoices("MAX_RESCUE_PREFILTER_CANDIDATES_EXPORT"))\n',
    ),
]


def insert_after_marker(src: str, marker: str, lines: list[str]) -> str:
    if marker not in src:
        return src
    block = marker + "".join(lines)
    return src.replace(marker, block, 1)


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"skip: {CONFIG_PATH} not found")
        return 0

    src = CONFIG_PATH.read_text(encoding="utf-8")
    original = src
    missing = [(name, line) for name, line in FIELDS if f"{name}:" not in src]
    if not missing:
        print("already patched: data-depth settings fields exist")
        return 0

    # Prefer placing these near existing analysis/candidate limits.
    marker = '    analysis_match_cap_per_run: int = Field(default=260, validation_alias=AliasChoices("ANALYSIS_MATCH_CAP_PER_RUN", "DAILY_ANALYSIS_MATCH_LIMIT"))\n'
    if marker in src:
        src = insert_after_marker(src, marker, [line for _, line in missing])
    else:
        fallback = '    max_internal_candidates_per_run: int = Field(default=8, validation_alias=AliasChoices("MAX_INTERNAL_CANDIDATES_PER_RUN"))\n'
        if fallback in src:
            src = insert_after_marker(src, fallback, [line for _, line in missing])
        else:
            print("warn: no stable insertion marker found in app/config.py")
            return 0

    if src != original:
        CONFIG_PATH.write_text(src, encoding="utf-8")
        print("patched: app/config.py data-depth settings fields")
        for name, _ in missing:
            print(f"  + {name}")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
