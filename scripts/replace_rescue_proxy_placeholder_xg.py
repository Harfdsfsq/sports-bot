from __future__ import annotations

"""Replace 1.00:1.00 proxy placeholder xG on rescue candidates.

The proxy-default xG guard is correct: A/B publication must not treat a synthetic
1:1 pair as hard xG evidence.  But when a totals candidate has a current
same-side market probability and a 2+ bookmaker quorum, we can install a neutral
market-implied total xG anchor before controlled fallback evaluation.  This does
not create value, does not add an odds source, and does not mark the row as hard
provider xG; it only prevents the candidate from being rejected specifically for
the fake 1:1 placeholder.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.enrich_rescue_candidates_xg_confirmation import (
    fnum,
    install_xg,
    market_implied_xg,
    rows_from_payload,
    write_json,
)

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
OUT = EXPORT / "latest-rescue-proxy-placeholder-xg-replacement.json"
CANDIDATE_PATHS = [
    EXPORT / "latest-rescue-candidates.json",
    ROOT / "artifacts" / "run-bot" / "latest-rescue-candidates.json",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _nested_dict(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _is_default_pair(row: dict[str, Any]) -> bool:
    ss = _nested_dict(row, "source_summary")
    diag = _nested_dict(row, "diagnostics")
    xg = _nested_dict(ss, "xg") or _nested_dict(ss, "model_xg") or _nested_dict(diag, "xg_enrichment")
    home = fnum(row.get("expected_home") or row.get("home_xg") or xg.get("home") or xg.get("expected_home"))
    away = fnum(row.get("expected_away") or row.get("away_xg") or xg.get("away") or xg.get("expected_away"))
    if home is not None and away is not None:
        return abs(home - 1.0) < 1e-6 and abs(away - 1.0) < 1e-6
    total = fnum(row.get("total_xg") or xg.get("total_xg"))
    return total is not None and abs(total - 2.0) < 1e-6


def _proxy_marker(row: dict[str, Any]) -> bool:
    text = json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    return any(
        token in text
        for token in (
            "proxy_default_1_1_xg_placeholder",
            "b_cover_market_promotion",
            "a_cover_market_promotion",
            "conservative_median_market_anchor",
        )
    )


def _hard_xg_marker(row: dict[str, Any]) -> bool:
    text = json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    return any(
        token in text
        for token in (
            "bzzoiro_stats",
            "sstats_xg",
            "actual_home_xg",
            "actual_away_xg",
            "pre_match_home_xg",
            "pre_match_away_xg",
            "context_home_away",
            "context_total_split",
        )
    )


def _container_key(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("rows", "candidates", "rescue_candidates", "items", "data"):
        if isinstance(payload.get(key), list):
            return key
    return None


def save(path: Path, payload: Any, rows: list[dict[str, Any]], key: str | None) -> None:
    if isinstance(payload, dict) and key:
        payload[key] = rows
        write_json(path, payload)
    elif isinstance(payload, list):
        write_json(path, rows)


def main() -> int:
    checked = 0
    replaced = 0
    skipped = {"not_placeholder": 0, "hard_xg": 0, "no_market_implied": 0}
    examples: list[dict[str, Any]] = []
    touched: list[str] = []

    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        rows = rows_from_payload(payload)
        if not rows:
            continue
        changed = False
        key = _container_key(payload)
        for row in rows:
            checked += 1
            if not (_is_default_pair(row) and _proxy_marker(row)):
                skipped["not_placeholder"] += 1
                continue
            if _hard_xg_marker(row):
                skipped["hard_xg"] += 1
                continue
            xg = market_implied_xg(row)
            if not xg:
                skipped["no_market_implied"] += 1
                continue
            install_xg(row, xg, "market_probability_from_candidate", "market_implied_replaces_proxy_placeholder")
            row["proxy_default_xg_replaced"] = True
            row["proxy_default_xg_replacement_source"] = "market_implied_total_xg"
            ss = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
            ss["proxy_default_xg_replaced"] = True
            row["source_summary"] = ss
            replaced += 1
            changed = True
            if len(examples) < 10:
                examples.append({
                    "home_team": row.get("home_team") or row.get("home"),
                    "away_team": row.get("away_team") or row.get("away"),
                    "selection": row.get("selection") or row.get("selection_key"),
                    "point": row.get("point") or row.get("line"),
                    "expected_home": row.get("expected_home"),
                    "expected_away": row.get("expected_away"),
                    "total_xg": (row.get("source_summary") or {}).get("xg", {}).get("total_xg") if isinstance(row.get("source_summary"), dict) else None,
                })
        if changed:
            save(path, payload, rows, key)
            touched.append(str(path))

    report = {
        "status": "ok",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "checked": checked,
        "replaced": replaced,
        "skipped": skipped,
        "examples": examples,
        "touched_files": touched,
        "note": "Market-implied xG replaces only proxy 1:1 placeholders; it is not hard provider xG and does not bypass line/value/source guards.",
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
