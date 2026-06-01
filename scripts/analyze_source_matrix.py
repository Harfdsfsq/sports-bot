from __future__ import annotations

"""Build a compact 2+/2+ source matrix report from HARIZON artifacts.

Usage:
  python scripts/analyze_source_matrix.py

The script is intentionally tolerant because run artifacts have changed across
patches. It never calls providers; it only reads cached JSON reports.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

EXPORT_DIR = Path(".data/exports")
OUT_JSON = EXPORT_DIR / "latest-source-matrix-analysis.json"
OUT_MD = EXPORT_DIR / "latest-source-matrix-analysis.md"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("matches", "rows", "items", "coverage", "data", "gap_sample"):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            return [x for x in val.values() if isinstance(x, dict)]
    return []


def _count_sources(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len({str(x).strip().lower() for x in value if str(x).strip()})
    if isinstance(value, dict):
        return len({str(k).strip().lower() for k in value if str(k).strip()})
    try:
        return int(float(value))
    except Exception:
        return 0


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        cur: Any = row
        ok = True
        for part in name.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def _match_key(row: dict[str, Any]) -> str:
    return str(_field(row, "match_key", "key", "canonical_match_id", "match.match_key") or "").strip()


def _source_counts(row: dict[str, Any]) -> tuple[int, int]:
    odds = _field(
        row,
        "odds_source_count", "odds_sources_count", "independent_odds_source_count",
        "coverage.odds_source_count", "coverage.odds_sources_count",
        "progressive_coverage.odds_source_count", "progressive_coverage.odds_sources_count",
        "source_counts.odds", "source_counts.odds_sources",
    )
    ctx = _field(
        row,
        "context_source_count", "context_sources_count",
        "coverage.context_source_count", "coverage.context_sources_count",
        "progressive_coverage.context_source_count", "progressive_coverage.context_sources_count",
        "source_counts.context", "source_counts.context_sources",
    )
    if odds is None:
        odds = _field(row, "coverage.odds_sources", "progressive_coverage.odds_sources", "odds_sources")
    if ctx is None:
        ctx = _field(row, "coverage.context_sources", "progressive_coverage.context_sources", "context_sources")
    return _count_sources(odds), _count_sources(ctx)


def _load_match_rows() -> list[dict[str, Any]]:
    candidates: list[Path] = [
        EXPORT_DIR / "latest-day-inventory-coverage-truth.json",
        EXPORT_DIR / "latest-progressive-coverage-state.json",
        Path(".data/day_inventory/progressive_coverage_state.json"),
    ]
    for pattern in (".data/day_inventory/current/latest/**/*.json", ".data/day_inventory/current/**/*.json"):
        candidates.extend(Path().glob(pattern))
    best: list[dict[str, Any]] = []
    for path in candidates:
        payload = _read_json(path)
        rows = _rows(payload)
        keyed = [row for row in rows if _match_key(row)]
        if len(keyed) > len(best):
            best = keyed
    return best


def main() -> int:
    rows = _load_match_rows()
    matrix = Counter()
    gap_samples: list[dict[str, Any]] = []
    for row in rows:
        odds, ctx = _source_counts(row)
        matrix[(min(odds, 2), min(ctx, 2))] += 1
        if odds < 2 or ctx < 2:
            gap_samples.append({
                "match_key": _match_key(row),
                "home": _field(row, "home_team", "home", "match.home_team"),
                "away": _field(row, "away_team", "away", "match.away_team"),
                "league": _field(row, "league_name", "league", "match.league_name"),
                "commence_time": _field(row, "commence_time", "start_time", "event_date", "match.commence_time"),
                "odds_source_count": odds,
                "context_source_count": ctx,
                "odds_needed": max(0, 2 - odds),
                "context_needed": max(0, 2 - ctx),
            })
    gap_samples.sort(key=lambda x: (x["context_needed"], x["odds_needed"]), reverse=True)
    total = len(rows)
    payload = {
        "total_matches": total,
        "two_plus_odds_and_two_plus_context": matrix[(2, 2)],
        "two_plus_odds": sum(v for (o, _c), v in matrix.items() if o >= 2),
        "two_plus_context": sum(v for (_o, c), v in matrix.items() if c >= 2),
        "matrix": {f"odds_{o}_context_{c}": v for (o, c), v in sorted(matrix.items())},
        "top_gaps": gap_samples[:80],
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        "# HARIZON source matrix analysis",
        "",
        f"Total matches: **{total}**",
        f"2+ odds and 2+ context: **{payload['two_plus_odds_and_two_plus_context']}**",
        f"2+ odds: **{payload['two_plus_odds']}**",
        f"2+ context: **{payload['two_plus_context']}**",
        "",
        "## Matrix",
    ]
    for key, value in payload["matrix"].items():
        md.append(f"- {key}: {value}")
    md.extend(["", "## Top gaps"])
    for row in gap_samples[:30]:
        md.append(
            f"- {row['match_key']}: odds {row['odds_source_count']}/2, "
            f"context {row['context_source_count']}/2"
        )
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
