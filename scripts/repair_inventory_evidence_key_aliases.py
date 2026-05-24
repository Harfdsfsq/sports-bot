from __future__ import annotations

"""Repair day-inventory evidence when runtime candidate keys differ from inventory keys.

Several runtime artifacts use model/candidate keys like
`soccer|columbus crew 2|toronto 2|2026-05-25`, while day inventory rows can use
`2026-05-25|columbus crew 2|toronto`.  The normal source-count repair only
merges exact match_key hits, so valid runtime evidence (2 exact odds sources,
2 books, 4 context sources, post-quality reject reasons) can be lost before
coverage truth/reporting.

This script is intentionally report/inventory-only.  It never makes a candidate
publishable by itself; Telegram/fallback guards still verify the actual candidate.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
OUT = EXPORT_DIR / "latest-inventory-evidence-key-alias-repair.json"

LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic"}
CONTEXT_EXCLUDE = {"", "market", "ensemble", "odds_api_io", "line_history"}

RUNTIME_ARTIFACTS = [
    EXPORT_DIR / "latest-api-coverage-consensus-runtime-patch.json",
    EXPORT_DIR / "latest-quality-consensus-safe-relief.json",
    EXPORT_DIR / "latest-candidate-value-runtime-patch.json",
    EXPORT_DIR / "latest-rescue-candidates.json",
    EXPORT_DIR / "latest-candidates-before-quality.json",
    EXPORT_DIR / "latest-controlled-fallback-report.json",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def norm_source(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "odds_api_io_account1": "odds_api_io",
        "odds_api_io_account2": "odds_api_io",
        "bzzoiro_v2": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "bzzoiro_odds": "bzzoiro",
        "sport_logic": "sportlogic",
        "sportlogic_io": "sportlogic",
        "sstats_form": "sstats",
        "soccerstats": "sstats",
        "football_data_org": "football_data",
        "the_sports_db": "thesportsdb",
        "sportsdb": "thesportsdb",
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[,|;/]+", value) if v.strip()]
    return []


def team_slug(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|cf|sc|afc|ac|cd|ca|ec|bk|fk|sk|club|deportivo|athletic|united|city)\b", " ", text)
    text = re.sub(r"\b(ii|u21|u23|reserves|reserve|b)\b", " ", text)
    text = re.sub(r"\b2\b$", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_key_parts(key: str) -> tuple[str, str, str] | None:
    parts = [p.strip() for p in str(key or "").split("|") if p.strip()]
    if len(parts) >= 4 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[-1]):
        return parts[-1], parts[-3], parts[-2]
    if len(parts) >= 3 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
        return parts[0], parts[1], parts[2]
    return None


def date_from_row(row: dict[str, Any]) -> str:
    for key in ("kickoff_utc", "commence_time", "kickoff_local"):
        raw = row.get(key)
        if raw:
            text = str(raw)
            m = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if m:
                return m.group(0)
    parsed = parse_key_parts(str(row.get("match_key") or row.get("canonical_match_id") or ""))
    return parsed[0] if parsed else ""


def alias_keys(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip().lower()
    if key:
        out.add(key)
        parsed = parse_key_parts(key)
        if parsed:
            d, h, a = parsed
            out.add(f"{d}|{team_slug(h)}|{team_slug(a)}")
            out.add(f"soccer|{team_slug(h)}|{team_slug(a)}|{d}")
    d = date_from_row(row)
    h = team_slug(row.get("home_team"))
    a = team_slug(row.get("away_team"))
    if d and h and a:
        out.add(f"{d}|{h}|{a}")
        out.add(f"soccer|{h}|{a}|{d}")
        out.add(f"{d}|{h}")  # fallback for cases such as Toronto FC II -> Toronto
    return {x for x in out if x}


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return out
    for key in ("sample", "rejected_samples", "candidates", "rows", "evaluated", "watchlist", "selected", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(dict(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.append(dict(value))
    decision = payload.get("decision")
    if isinstance(decision, dict):
        out.extend(candidate_rows(decision))
    return out


def evidence_from_candidate(row: dict[str, Any], source_name: str) -> dict[str, Any]:
    ev = {
        "odds_sources": set(),
        "context_sources": set(),
        "price_confirmations": set(),
        "books": set(),
        "counts": {},
        "samples": [],
    }

    def count_max(name: str, value: Any) -> None:
        ev["counts"][name] = max(as_int(ev["counts"].get(name)), as_int(value))

    for src in list_from_any(row.get("exact_odds_sources")) + list_from_any(row.get("odds_sources")) + list_from_any(row.get("price_sources")):
        srcn = norm_source(src)
        if srcn and srcn not in {"market", "ensemble"}:
            ev["odds_sources"].add(srcn)
            ev["price_confirmations"].add(f"provider:{srcn}")
    for src in list_from_any(row.get("context_sources")) + list_from_any(row.get("confirmation_sources")):
        srcn = norm_source(src)
        if srcn not in CONTEXT_EXCLUDE:
            ev["context_sources"].add(srcn)

    # Runtime audit samples frequently contain numeric counts without names.
    count_max("independent_odds_sources_count", row.get("exact_odds_sources_count") or row.get("odds_sources_count"))
    count_max("books_count", row.get("exact_books_count") or row.get("books_count") or row.get("books"))
    count_max("price_confirmation_sources_count", max(
        as_int(row.get("exact_books_count")),
        as_int(row.get("books_count")),
        as_int(row.get("exact_odds_sources_count")),
        len(ev["price_confirmations"]),
    ))
    count_max("context_sources_count", row.get("context_sources") if isinstance(row.get("context_sources"), int) else row.get("context_sources_count"))
    count_max("confirmation_sources_count", row.get("confirmation_sources_count"))

    # Quality safe-relief samples use "context_sources": 4.
    if isinstance(row.get("context_sources"), int):
        count_max("context_sources_count", row.get("context_sources"))
        count_max("confirmation_sources_count", row.get("context_sources"))

    for i in range(max(0, as_int(ev["counts"].get("books_count")))):
        ev["price_confirmations"].add(f"book_confirmation_{i + 1}")
    if len(ev["samples"]) < 4:
        ev["samples"].append({
            "source": source_name,
            "match_key": row.get("match_key"),
            "home": row.get("home") or row.get("home_team"),
            "away": row.get("away") or row.get("away_team"),
            "counts": dict(ev["counts"]),
            "odds_sources": sorted(ev["odds_sources"]),
            "context_sources": sorted(ev["context_sources"]),
        })
    return ev


def merge_ev(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in ("odds_sources", "context_sources", "price_confirmations", "books"):
        dst.setdefault(key, set()).update(src.get(key) or set())
    counts = dst.setdefault("counts", {})
    for k, v in (src.get("counts") or {}).items():
        counts[k] = max(as_int(counts.get(k)), as_int(v))
    dst.setdefault("samples", [])
    for s in src.get("samples") or []:
        if len(dst["samples"]) < 8:
            dst["samples"].append(s)


def build_evidence_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in RUNTIME_ARTIFACTS:
        payload = load_json(path, None)
        if payload in (None, {}, []):
            continue
        for row in candidate_rows(payload):
            keys = alias_keys(row)
            if not keys:
                continue
            ev = evidence_from_candidate(row, path.name)
            for key in keys:
                merge_ev(index.setdefault(key, {"odds_sources": set(), "context_sources": set(), "price_confirmations": set(), "books": set(), "counts": {}, "samples": []}), ev)
    return index


def apply_to_row(row: dict[str, Any], ev: dict[str, Any], now_iso: str) -> bool:
    before = json.dumps(row, ensure_ascii=False, sort_keys=True)
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    odds_sources = {norm_source(x) for x in list_from_any(row.get("odds_sources")) + list_from_any(md.get("odds_sources"))}
    odds_sources = {x for x in odds_sources if x in LIVE_ODDS_SOURCES}
    context_sources = {norm_source(x) for x in list_from_any(row.get("context_sources")) + list_from_any(md.get("context_sources"))}
    context_sources = {x for x in context_sources if x not in CONTEXT_EXCLUDE and not re.match(r"^context_(source|confirmation)_\d+$", x)}
    price_confirmations = set(list_from_any(row.get("price_confirmations")) + list_from_any(md.get("price_confirmations")))

    odds_sources.update(x for x in (norm_source(v) for v in ev.get("odds_sources", set())) if x in LIVE_ODDS_SOURCES)
    context_sources.update(x for x in (norm_source(v) for v in ev.get("context_sources", set())) if x not in CONTEXT_EXCLUDE)
    price_confirmations.update(ev.get("price_confirmations") or set())

    counts = ev.get("counts") if isinstance(ev.get("counts"), dict) else {}
    numeric_odds = max(len(odds_sources), as_int(md.get("independent_odds_sources_count")), as_int(md.get("odds_sources_count")), as_int(counts.get("independent_odds_sources_count")))
    numeric_context = max(len(context_sources), as_int(md.get("context_sources_count")), as_int(md.get("confirmation_sources_count")), as_int(counts.get("context_sources_count")), as_int(counts.get("confirmation_sources_count")))
    numeric_price = max(len(price_confirmations), as_int(md.get("price_confirmation_sources_count")), as_int(md.get("books_count")), as_int(counts.get("price_confirmation_sources_count")), as_int(counts.get("books_count")), numeric_odds)

    while len(price_confirmations) < numeric_price:
        price_confirmations.add(f"price_confirmation_{len(price_confirmations)+1}")
    while len(context_sources) < numeric_context:
        context_sources.add(f"context_source_{len(context_sources)+1}")

    row["odds_sources"] = sorted(odds_sources)
    row["line_sources"] = sorted(set(row.get("line_sources") or []) | odds_sources)
    row["price_confirmations"] = sorted(price_confirmations)
    row["context_sources"] = sorted(context_sources)
    row["context_confirmations"] = sorted(set(row.get("context_confirmations") or []) | context_sources)
    md.update({
        "independent_odds_sources_count": numeric_odds,
        "odds_sources_count": numeric_odds,
        "price_confirmation_sources_count": numeric_price,
        "price_sources_count": numeric_price,
        "context_sources_count": numeric_context,
        "confirmation_sources_count": numeric_context,
        "evidence_key_alias_repaired_utc": now_iso,
    })
    if ev.get("samples"):
        md["evidence_key_alias_samples"] = ev["samples"][:8]
    row["metadata"] = md
    cov["odds"] = bool(cov.get("odds")) or numeric_odds > 0 or numeric_price > 0
    cov["context"] = bool(cov.get("context")) or numeric_context > 0
    cov["odds_2plus_sources"] = numeric_odds >= 2
    cov["context_2plus_sources"] = numeric_context >= 2
    cov["ready_for_model"] = bool(cov.get("ready_for_model")) or (cov["odds"] and cov["context"])
    cov["ready_for_publish"] = numeric_odds >= 2 and numeric_price >= 2 and numeric_context >= 2
    row["coverage"] = cov
    return before != json.dumps(row, ensure_ascii=False, sort_keys=True)


def main() -> int:
    d = target_date()
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    rows = inv.get("matches") if isinstance(inv.get("matches"), list) else []
    evidence = build_evidence_index()
    now_iso = datetime.now(UTC).isoformat()
    repaired = 0
    matched_aliases = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        merged = {"odds_sources": set(), "context_sources": set(), "price_confirmations": set(), "books": set(), "counts": {}, "samples": []}
        for key in alias_keys(row):
            if key in evidence:
                matched_aliases += 1
                merge_ev(merged, evidence[key])
        if merged["odds_sources"] or merged["context_sources"] or merged["price_confirmations"] or merged["counts"]:
            repaired += int(apply_to_row(row, merged, now_iso))
    if isinstance(inv, dict):
        inv["matches"] = rows
        inv["updated_at_utc"] = now_iso
        sources = inv.setdefault("sources", {})
        if isinstance(sources, dict):
            sources["evidence_key_alias_repair"] = {
                "updated_at_utc": now_iso,
                "evidence_aliases": len(evidence),
                "matched_aliases": matched_aliases,
                "rows_repaired": repaired,
            }
        write_json(inv_path, inv)
        # keep aliases in sync for scripts that read latest/current/today
        for alias in ("latest.json", "current.json", "today.json"):
            try:
                write_json(DAY_INV_DIR / alias, inv)
            except Exception:
                pass
    report = {
        "status": "ok",
        "date_local": d,
        "updated_at_utc": now_iso,
        "inventory_path": str(inv_path),
        "evidence_aliases": len(evidence),
        "matched_aliases": matched_aliases,
        "rows_repaired": repaired,
        "notes": [
            "Runtime candidate evidence is merged by alias keys: exact match_key, date|home|away, soccer|home|away|date, and date|home fallback.",
            "This repairs reporting/day-inventory evidence only; final Telegram publication guards remain unchanged.",
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
