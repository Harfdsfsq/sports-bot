from __future__ import annotations

"""Repair day-inventory odds/context coverage from runtime artifacts.

The runner can parse raw provider rows correctly while the frozen inventory still
shows too few normalized bookmakers/context sources.  This script is deliberately
post-processing only: it copies observed coverage into inventory metadata and
never creates or publishes a pick.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from app.utils import canonicalize_team_name, normalize_bookmaker_name, team_similarity
except Exception:  # pragma: no cover - keeps manual script usable outside app env
    def canonicalize_team_name(value: str) -> str:  # type: ignore[no-redef]
        text = str(value or "").lower().replace("ё", "е")
        text = re.sub(r"[^a-z0-9а-я]+", " ", text)
        return " ".join(text.split())

    def normalize_bookmaker_name(value: str) -> str:  # type: ignore[no-redef]
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def team_similarity(a: str, b: str) -> float:  # type: ignore[no-redef]
        return 1.0 if canonicalize_team_name(a) == canonicalize_team_name(b) else 0.0

UTC = timezone.utc
ROOT = Path(".").resolve()
DAY_DIR = ROOT / ".data" / "day_inventory"
CACHE_DAY_DIR = ROOT / ".data" / "cache" / "day_inventory"
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT_PATH = EXPORT_DIR / "latest-inventory-bookmaker-backfill.json"

PRICE_KEYS = (
    "price", "odds", "decimal", "decimal_odds", "selected_odds", "best_price", "value", "odd", "option_price"
)
BOOK_KEYS = (
    "bookmaker", "bookmaker_slug", "bookmaker_name", "selected_bookmaker", "selected_bookmaker_slug",
    "book", "sportsbook", "provider_bookmaker", "bookmakerKey", "bookmaker_key"
)
HOME_KEYS = ("home_team", "home", "home_name", "team_home", "homeTeam", "home_team_name")
AWAY_KEYS = ("away_team", "away", "away_name", "team_away", "awayTeam", "away_team_name")
DATE_KEYS = ("commence_time", "kickoff_utc", "start_time", "kickoff", "date", "event_date")
ID_KEYS = (
    "canonical_match_id", "match_key", "event_key", "source_event_id", "id", "event_id", "game_id",
    "fixture_id", "match_id", "sportlogic_event_id", "bzzoiro_event_id", "odds_api_io_id", "sstats_game_id"
)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-я]+", "", norm(value))


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


def target_date() -> str:
    return (os.getenv("DAY_INVENTORY_TARGET_DATE") or datetime.now(UTC).date().isoformat())[:10]


def first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def row_date(row: dict[str, Any]) -> str:
    for key in DATE_KEYS:
        value = row.get(key)
        if not value:
            continue
        if re.match(r"^20\d{2}-\d{2}-\d{2}$", str(value)[:10]):
            return str(value)[:10]
        dt = parse_dt(value)
        if dt:
            return dt.date().isoformat()
    for key in ID_KEYS:
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(row.get(key) or ""))
        if match:
            return match.group(1)
    return ""


def team_pair(row: dict[str, Any]) -> tuple[str, str]:
    home = str(first_value(row, HOME_KEYS) or "")
    away = str(first_value(row, AWAY_KEYS) or "")
    return canonicalize_team_name(home), canonicalize_team_name(away)


def source_name(row: dict[str, Any], fallback_path: Path | None = None) -> str:
    for key in ("source", "provider", "provider_name", "bookmaker_source"):
        value = compact(row.get(key))
        if value:
            return value
    if fallback_path is not None:
        name = fallback_path.name.lower()
        for candidate in ("odds_api_io", "bzzoiro", "sstats", "sportlogic", "bookies", "oddspapi", "allsportsapi"):
            if candidate.replace("_", "-") in name or candidate in name:
                return candidate
    return "unknown"


def id_values(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ID_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            out.add(value)
    for container_key in ("source_ids", "metadata", "details", "coverage", "refresh"):
        value = row.get(container_key)
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if any(token in str(sub_key).lower() for token in ("id", "key")) and sub_value not in (None, ""):
                    out.add(str(sub_value))
    return {x for x in out if x and x.lower() not in {"none", "null"}}


def key_variants(row: dict[str, Any], default_day: str = "") -> set[str]:
    out: set[str] = set()
    for value in id_values(row):
        out.add("id:" + norm(value))
        out.add("idc:" + compact(value))
    home, away = team_pair(row)
    day = row_date(row) or default_day
    if home and away and day:
        out.update({
            f"teams:{day}|{home}|{away}",
            f"teams:{day}|{away}|{home}",
            f"loose:{home}|{away}",
            f"loose:{away}|{home}",
        })
    return {x for x in out if x and not x.endswith("|")}


def as_price(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "."))
        return parsed if parsed > 1.0 else None
    except Exception:
        return None


def price_of(row: dict[str, Any]) -> float | None:
    for key in PRICE_KEYS:
        price = as_price(row.get(key))
        if price is not None:
            return price
    return None


def bookmaker_of(row: dict[str, Any]) -> str:
    for key in BOOK_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            book = normalize_bookmaker_name(str(value))
            if book:
                return book
    return ""


def point_of(row: dict[str, Any]) -> str:
    value = row.get("point") or row.get("line") or row.get("handicap") or row.get("total") or row.get("points")
    if value in (None, ""):
        text = str(row.get("selection") or row.get("outcome") or row.get("name") or row.get("market") or "").replace(",", ".")
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        value = match.group(1) if match else ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return norm(value)


def family_of(row: dict[str, Any]) -> str:
    text = norm(row.get("family") or row.get("market_family") or row.get("market") or row.get("market_key") or row.get("market_name"))
    if "total" in text or "over under" in text or "overunder" in text or text in {"ou", "goals"}:
        return "totals"
    if "spread" in text or "handicap" in text or "asian" in text:
        return "spreads"
    if "btts" in text or "both teams" in text:
        return "btts"
    if "h2h" in text or "match winner" in text or "1x2" in text or "winner" in text:
        return "h2h"
    return text


def selection_of(row: dict[str, Any]) -> str:
    text = norm(row.get("selection_key") or row.get("selection") or row.get("outcome") or row.get("name") or row.get("label"))
    if "under" in text or text in {"u", "tm", "меньше"}:
        return "under"
    if "over" in text or text in {"o", "tb", "больше"}:
        return "over"
    if text in {"home", "1"}:
        return "home"
    if text in {"away", "2"}:
        return "away"
    if text in {"draw", "x"}:
        return "draw"
    return text


def side_key(row: dict[str, Any]) -> str:
    return "|".join([family_of(row), selection_of(row), point_of(row), norm(row.get("team_side") or row.get("side"))]).strip("|")


def iter_dicts(value: Any, depth: int = 0):
    if depth > 6:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child, depth + 1)


def offer_like(row: dict[str, Any], day: str) -> bool:
    if price_of(row) is None or not bookmaker_of(row):
        return False
    date = row_date(row)
    if date and date != day:
        return False
    return bool(key_variants(row, day) or all(team_pair(row)))


def context_like(row: dict[str, Any], day: str) -> bool:
    date = row_date(row)
    if date and date != day:
        return False
    if not (key_variants(row, day) or all(team_pair(row))):
        return False
    keys = {str(k).lower() for k in row.keys()}
    if keys & {"expected_home", "expected_away", "expected_home_goals", "expected_away_goals", "home_win_probability", "away_win_probability"}:
        return True
    details = row.get("details")
    return isinstance(details, dict) and any("sstats" in str(k).lower() or "bzzoiro" in str(k).lower() or "sportlogic" in str(k).lower() for k in details)


def source_paths() -> list[Path]:
    names = [
        "latest-odds-api-io-offer-snapshot.json",
        "latest-odds-api-io-offers.json",
        "latest-bookmaker-quorum-normalizer.json",
        "latest-rescue-candidates.json",
        "latest-controlled-fallback-report.json",
        "latest-run-summary.json",
        "latest-matches.json",
        "latest-provider-smoke.json",
        "latest-bzzoiro-v2-source-matrix-runtime.json",
    ]
    paths = [EXPORT_DIR / name for name in names]
    paths.append(ROOT / ".logs" / "debug-last-run.json")
    for root in (EXPORT_DIR, ROOT / "artifacts" / "run-bot"):
        if root.exists():
            for pattern in ("*odds*.json", "*offer*.json", "*candidate*.json", "*context*.json", "*coverage*.json", "*matches*.json", "*/*.json"):
                paths.extend(sorted(root.glob(pattern))[:250])
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if resolved not in seen and path.exists():
            seen.add(resolved)
            out.append(path)
    return out


def collect_groups(day: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    scanned = accepted_offers = accepted_contexts = 0
    for path in source_paths():
        payload = load_json(path, None)
        if payload is None:
            continue
        path_offers = path_contexts = 0
        for row in iter_dicts(payload):
            if not isinstance(row, dict):
                continue
            scanned += 1
            keys = key_variants(row, day)
            home, away = team_pair(row)
            src = source_name(row, path)
            if offer_like(row, day):
                book = bookmaker_of(row)
                price = price_of(row)
                if not keys and home and away:
                    keys = {f"teams:{day}|{home}|{away}", f"teams:{day}|{away}|{home}"}
                for key in keys:
                    group = groups.setdefault(key, {"books": set(), "sources": set(), "context_sources": set(), "prices": [], "same_side": defaultdict(set), "teams": set(), "sample": []})
                    group["books"].add(book)
                    group["sources"].add(src)
                    group["prices"].append(price)
                    group["same_side"][side_key(row)].add(book)
                    if home and away:
                        group["teams"].add((home, away, row_date(row) or day))
                    if len(group["sample"]) < 5:
                        group["sample"].append({"source": src, "bookmaker": book, "price": price, "family": family_of(row), "selection": selection_of(row), "point": point_of(row)})
                accepted_offers += 1
                path_offers += 1
            if context_like(row, day):
                if not keys and home and away:
                    keys = {f"teams:{day}|{home}|{away}", f"teams:{day}|{away}|{home}"}
                for key in keys:
                    group = groups.setdefault(key, {"books": set(), "sources": set(), "context_sources": set(), "prices": [], "same_side": defaultdict(set), "teams": set(), "sample": []})
                    group["context_sources"].add(src)
                    if home and away:
                        group["teams"].add((home, away, row_date(row) or day))
                accepted_contexts += 1
                path_contexts += 1
        if path_offers or path_contexts:
            source_counts[str(path)] = path_offers + path_contexts
    normalized: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        same_side_counts = {side: len(books) for side, books in group["same_side"].items() if side}
        normalized[key] = {
            "books": sorted(group["books"]),
            "books_count": len(group["books"]),
            "sources": sorted(group["sources"]),
            "odds_sources_count": len(group["sources"]),
            "context_sources": sorted(group["context_sources"]),
            "context_sources_count": len(group["context_sources"]),
            "prices_count": len(group["prices"]),
            "same_side_2plus": sum(1 for count in same_side_counts.values() if count >= 2),
            "same_side_max_books": max(same_side_counts.values() or [0]),
            "teams": sorted([list(item) for item in group["teams"]]),
            "sample": group["sample"],
        }
    diagnostics = {"source_counts": source_counts, "scanned_dicts": scanned, "accepted_offer_rows": accepted_offers, "accepted_context_rows": accepted_contexts}
    return normalized, diagnostics


def load_inventory(day: str) -> tuple[dict[str, Any], Path]:
    paths = [DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json", CACHE_DAY_DIR / f"{day}.json", CACHE_DAY_DIR / "today.json"]
    best: dict[str, Any] = {"date_local": day, "matches": [], "counts": {}}
    best_path = DAY_DIR / f"{day}.json"
    best_count = -1
    for path in paths:
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        if len(rows) > best_count:
            best = payload
            best_path = path
            best_count = len(rows)
    best.setdefault("matches", [])
    best.setdefault("counts", {})
    return best, best_path


def fuzzy_group(row: dict[str, Any], groups: dict[str, dict[str, Any]], day: str) -> dict[str, Any] | None:
    home, away = team_pair(row)
    row_day = row_date(row) or day
    if not home or not away:
        return None
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for group in groups.values():
        for team in group.get("teams") or []:
            try:
                gh, ga, gd = str(team[0]), str(team[1]), str(team[2])
            except Exception:
                continue
            if gd and gd != row_day:
                continue
            score = max(
                min(team_similarity(home, gh), team_similarity(away, ga)),
                min(team_similarity(home, ga), team_similarity(away, gh)),
            )
            if score > best[0]:
                best = (score, group)
    return best[1] if best[0] >= 0.92 else None


def main() -> int:
    day = target_date()
    inventory, inventory_path = load_inventory(day)
    rows = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    groups, diagnostics = collect_groups(day)
    changed = matched = fuzzy_matched = 0
    examples: list[dict[str, Any]] = []
    raw_2plus = sum(1 for group in groups.values() if int(group.get("same_side_max_books") or group.get("books_count") or 0) >= 2)
    normalized_2plus_before = normalized_2plus_after = 0
    context_2plus_after = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        cov = row.setdefault("coverage", {}) if isinstance(row.get("coverage"), dict) else {}
        row["coverage"] = cov
        md = row.setdefault("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        row["metadata"] = md
        before_books = max(int(cov.get("books_count") or 0), int(md.get("books_count") or 0), len(cov.get("books") or []) if isinstance(cov.get("books"), list) else 0)
        if before_books >= 2:
            normalized_2plus_before += 1
        group = None
        for key in key_variants(row, day):
            if key in groups:
                group = groups[key]
                break
        if group is None:
            group = fuzzy_group(row, groups, day)
            if group is not None:
                fuzzy_matched += 1
        if group is None:
            continue
        matched += 1
        books = [str(x) for x in (group.get("books") or []) if str(x)]
        sources = [str(x) for x in (group.get("sources") or []) if str(x)]
        context_sources = [str(x) for x in (group.get("context_sources") or []) if str(x)]
        same_side_max = int(group.get("same_side_max_books") or 0)
        books_count = max(len(books), same_side_max, before_books)
        before_context_sources = max(int(cov.get("context_sources_count") or 0), int(md.get("context_sources_count") or 0))
        context_sources_count = max(before_context_sources, len(context_sources))
        before_odds_sources = max(int(cov.get("odds_sources_count") or 0), int(cov.get("price_sources_count") or 0))
        odds_sources_count = max(before_odds_sources, len(sources), 1 if books_count > 0 else 0)
        row_changed = False
        if books_count > before_books:
            cov["books_count"] = books_count
            cov["price_confirmations"] = max(int(cov.get("price_confirmations") or 0), books_count)
            cov["price_sources_count"] = max(int(cov.get("price_sources_count") or 0), books_count)
            cov["books"] = sorted(set([str(x) for x in (cov.get("books") if isinstance(cov.get("books"), list) else [])] + books))
            cov["odds"] = True
            md["books_count"] = books_count
            md["latest_books_max"] = max(int(md.get("latest_books_max") or 0), books_count)
            row_changed = True
        if odds_sources_count > before_odds_sources:
            cov["odds_sources_count"] = odds_sources_count
            cov["odds_sources"] = sorted(set([str(x) for x in (cov.get("odds_sources") if isinstance(cov.get("odds_sources"), list) else [])] + sources))
            row_changed = True
        if context_sources_count > before_context_sources:
            cov["context"] = True
            cov["context_sources_count"] = context_sources_count
            cov["context_sources"] = sorted(set([str(x) for x in (cov.get("context_sources") if isinstance(cov.get("context_sources"), list) else [])] + context_sources))
            md["context_sources_count"] = context_sources_count
            row_changed = True
        if row_changed:
            changed += 1
            md["bookmaker_backfill_source"] = "runtime_offer_context_artifacts_v2"
            md["bookmaker_backfill_updated_at_utc"] = datetime.now(UTC).isoformat()
            if len(examples) < 12:
                examples.append({
                    "match_key": row.get("match_key") or row.get("canonical_match_id"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "before_books": before_books,
                    "after_books": books_count,
                    "odds_sources_count": odds_sources_count,
                    "context_sources_count": context_sources_count,
                    "sample": group.get("sample"),
                })
        if max(books_count, before_books) >= 2:
            normalized_2plus_after += 1
        if context_sources_count >= 2:
            context_2plus_after += 1

    inventory["matches"] = rows
    counts = inventory.setdefault("counts", {})
    counts["bookmaker_backfill_raw_2plus_matches"] = raw_2plus
    counts["bookmaker_backfill_normalized_2plus_before"] = normalized_2plus_before
    counts["bookmaker_backfill_normalized_2plus_after"] = normalized_2plus_after
    counts["bookmaker_backfill_mapping_gap_after"] = max(0, raw_2plus - normalized_2plus_after)
    counts["matches_with_2plus_price_confirmations"] = max(int(counts.get("matches_with_2plus_price_confirmations") or 0), normalized_2plus_after)
    counts["matches_with_2plus_context_sources"] = max(int(counts.get("matches_with_2plus_context_sources") or 0), context_2plus_after)
    inventory["bookmaker_backfill_updated_at_utc"] = datetime.now(UTC).isoformat()

    for path in (DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json", CACHE_DAY_DIR / f"{day}.json", CACHE_DAY_DIR / "today.json", CACHE_DAY_DIR / "current.json", CACHE_DAY_DIR / "latest.json"):
        write_json(path, inventory)

    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "target_date": day,
        "inventory_path_used": str(inventory_path),
        "inventory_rows": len(rows),
        "raw_groups": len(groups),
        "raw_2plus_matches": raw_2plus,
        "matched_inventory_rows": matched,
        "fuzzy_matched_inventory_rows": fuzzy_matched,
        "changed_inventory_rows": changed,
        "normalized_2plus_before": normalized_2plus_before,
        "normalized_2plus_after": normalized_2plus_after,
        "context_2plus_after": context_2plus_after,
        "mapping_gap_after": max(0, raw_2plus - normalized_2plus_after),
        "examples": examples,
        **diagnostics,
    }
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
