from __future__ import annotations

"""SStats deep inventory enrichment v4.

This pass spends SStats detail budget on real inventory gaps.  Earlier versions
trusted inflated *_sources_count fields and context_sources such as dayinventory,
openligadb, weather or odds_api_io; that made the queue enrich already-rich rows
while the final coverage-truth still had only ~85/300 with 2+ real context
sources.  This version counts only actual provider evidence and prioritizes
rows with fewer than two real context providers.

Everything touching xG or prices is written from the run 11:49 probe artifact,
not from guessed field names:

  /Games/glicko/{id}      -> data.glicko.homeXg / data.glicko.awayXg
  /Games/last-games-stats -> home.avgScore / home.avgConceded (+ away mirror,
                             gamesCount for the sample size)
  /Odds/{id}              -> data[].bookmakerName, odds[].marketId 5
                             "Goals Over/Under", odds[].name "Over 2.5",
                             odds[].value

Three defects that probe exposed, and that this version fixes:

1. xG was never missing, it was never read.  The old extractor built candidate
   keys such as "homexG" and looked for xg, expectedGoals, goalsFor and
   conceded, none of which exist in these payloads.  Hence attempted 64,
   resolved_real 0, and hence every candidate falling back to market-implied xG
   that the quality gates then reject as not-hard confirmation.
2. Prices were fetched and discarded.  Only the row count was kept, so SStats
   could never be the second independent price source A-tier requires, while
   Bet365 and Unibet both arrive through the same odds_api_io vendor.
3. The queue wrote onto the wrong matches.  by_key kept the empty string as a
   real key, so every queue item without a match_key resolved to the same
   arbitrary row: five of six probe samples show one row absorbing the payloads
   of five unrelated fixtures.  Empty keys are skipped now, and every row is
   verified against the team names SStats reports for that game id before more
   budget is spent on it.

avgOddsXg and avgOddsXgConceded are deliberately not used.  They are xG
reconstructed from bookmaker odds, so using them would rebuild the very
market-implied circularity this pass exists to remove.
"""

import asyncio
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import apply_sstats_deep_inventory_enrichment_v2 as v2
from scripts import sstats_crosswalk_probe

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.json"
TXT_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.txt"
XG_PROBE_OUT = OUT_DIR / "latest-sstats-xg-extraction-probe.json"
ODDS_OFFERS_OUT = OUT_DIR / "latest-sstats-odds-offers.json"
CONTEXT_PROVIDERS = {"sstats", "bzzoiro", "thesportsdb", "football_data", "api_football", "sportlogic", "allsportsapi", "highlightly"}
ODDS_PROVIDERS = {"odds_api_io", "bzzoiro", "sstats", "sportlogic"}

XG_PROBE_LIMIT = 6
ODDS_PROBE_LIMIT = 4
MISMATCH_SAMPLE_LIMIT = 12
FORM_MIN_GAMES = 5
MAX_OFFERS_PER_ROW = 60
REAL_XG_SOURCES = {"glicko_xg", "last_games_form_goals"}
TOTALS_MARKET_IDS = {5}
TOTALS_MARKET_NAMES = {"goals over/under", "goals over under", "over/under", "total goals"}
TEAM_STOPWORDS = {"fc", "cf", "sc", "ac", "afc", "cd", "fk", "sk", "club", "team", "the", "and", "sports", "sporting", "academy", "reserves", "women"}

_XG_PROBE: list[dict[str, Any]] = []
_ODDS_PROBE: list[dict[str, Any]] = []
_MISMATCHES: list[dict[str, Any]] = []
_ALL_OFFERS: list[dict[str, Any]] = []
_XG_STATS: dict[str, Any] = {"attempted": 0, "resolved_real": 0, "kept_existing": 0, "missing": 0, "placeholder_rejected": 0, "source_counts": {}}
_ODDS_STATS: dict[str, Any] = {"rows_attempted": 0, "payload_ok": 0, "offers_parsed": 0, "rows_with_offers": 0, "no_totals_market": 0, "book_counts": {}}
_ID_STATS: dict[str, Any] = {"verified": 0, "unverified": 0, "mismatch": 0, "skipped_empty_key": 0, "skipped_no_row": 0}


def _inc(stats: dict[str, Any], key: str, amount: int = 1) -> None:
    stats[key] = v2.as_int(stats.get(key)) + amount


def _inc_map(stats: dict[str, Any], map_key: str, item: str, amount: int = 1) -> None:
    bucket = stats.setdefault(map_key, {})
    if isinstance(bucket, dict):
        bucket[str(item)] = v2.as_int(bucket.get(str(item))) + amount


def target_date_msk() -> str:
    raw = v2.env("DAY_INVENTORY_TARGET_DATE") or v2.env("PROVIDER_SMOKE_TARGET_DATE")
    if raw:
        return raw[:10]
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def inventory_aliases(primary: Path) -> list[Path]:
    paths = [primary, Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json"), Path(".data/day_inventory") / f"{target_date_msk()}.json"]
    out: list[Path] = []
    for path in paths:
        if path not in out:
            out.append(path)
    return out


def clean_sources(row: dict[str, Any], key: str, allowed: set[str]) -> list[str]:
    out: list[str] = []
    for value in v2.src_list(row, key):
        text = str(value or "").strip().lower()
        if text in allowed and text not in out:
            out.append(text)
    return out


def bool_cov(row: dict[str, Any], key: str) -> bool:
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    if bool(cov.get(key)) or bool(row.get(key)):
        return True
    if key == "context" and (bool(row.get("bzzoiro_context")) or bool(row.get("has_context"))):
        return True
    if key == "odds" and (bool(row.get("has_odds")) or bool(row.get("odds"))):
        return True
    return False


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "") or isinstance(value, (dict, list, bool)):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _valid_xg_pair(home: Any, away: Any) -> tuple[float | None, float | None]:
    h = _float_or_none(home)
    a = _float_or_none(away)
    if h is None or a is None:
        return None, None
    if h < 0 or a < 0 or h + a < 0.25:
        return None, None
    return round(max(0.15, min(4.5, h)), 3), round(max(0.15, min(4.5, a)), 3)


def is_proxy_placeholder(home: Any, away: Any) -> bool:
    """True for the 1.0/1.0 default that carries no information.

    It used to satisfy has_valid_xg, which told this queue those rows already
    had xG coverage and pushed them to the back of the priority list, so the
    rows that most need real xG were the ones never enriched.
    """
    h = _float_or_none(home)
    a = _float_or_none(away)
    if h is None or a is None:
        return False
    return abs(h - 1.0) < 1e-6 and abs(a - 1.0) < 1e-6


def has_valid_xg(row: dict[str, Any]) -> bool:
    if is_proxy_placeholder(row.get("expected_home"), row.get("expected_away")):
        return False
    h, a = _valid_xg_pair(row.get("expected_home"), row.get("expected_away"))
    return h is not None and a is not None


def count_family(row: dict[str, Any], family: str) -> int:
    if family == "context":
        sources = clean_sources(row, "context_sources", CONTEXT_PROVIDERS)
        if sources:
            return len(sources)
        return 1 if bool_cov(row, "context") else 0
    if family == "odds":
        sources = clean_sources(row, "odds_sources", ODDS_PROVIDERS)
        if sources:
            return len(sources)
        return 1 if bool_cov(row, "odds") else 0
    if family == "xg":
        return max(len(clean_sources(row, "xg_sources", CONTEXT_PROVIDERS)), 1 if bool_cov(row, "xg") or has_valid_xg(row) else 0)
    if family == "form":
        return max(len(clean_sources(row, "form_sources", CONTEXT_PROVIDERS)), 1 if bool_cov(row, "form") else 0)
    return 0


def _side_blocks(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """home/away blocks of /Games/last-games-stats.

    The probe shows a bare {"home": {...}, "away": {...}} object with no
    status/data wrapper; the wrapped form is accepted too so a later API change
    cannot silently zero this out again.
    """
    holders = [payload, payload.get("data") if isinstance(payload, dict) else None]
    for holder in holders:
        if isinstance(holder, dict):
            home = holder.get("home")
            away = holder.get("away")
            if isinstance(home, dict) and isinstance(away, dict):
                return home, away
    return None, None


def form_lambdas(payload: Any) -> tuple[float | None, float | None, int]:
    """Goal expectation from actual scoring rates, not from prices.

    lambda_home = (home scored + away conceded) / 2 and the mirror for away.
    avgOddsXg lives in the same payload and is not used on purpose: it is
    reconstructed from bookmaker odds, so it would reintroduce the market
    circularity.
    """
    home, away = _side_blocks(payload)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None, None, 0
    games = min(v2.as_int(home.get("gamesCount")), v2.as_int(away.get("gamesCount")))
    home_scored = _float_or_none(home.get("avgScore"))
    home_conceded = _float_or_none(home.get("avgConceded"))
    away_scored = _float_or_none(away.get("avgScore"))
    away_conceded = _float_or_none(away.get("avgConceded"))
    if games < FORM_MIN_GAMES or None in (home_scored, home_conceded, away_scored, away_conceded):
        return None, None, games
    lam_home = (float(home_scored) + float(away_conceded)) / 2.0
    lam_away = (float(away_scored) + float(home_conceded)) / 2.0
    return round(lam_home, 4), round(lam_away, 4), games


def glicko_xg(payload: Any) -> tuple[float | None, float | None]:
    """data.glicko.homeXg / data.glicko.awayXg, exactly as the probe shows.

    These come from the provider's rating model rather than from a price, and
    they are side-specific (1.502 / 1.065 on the first sample), unlike the
    symmetric market-implied placeholder the quality gates keep rejecting.
    """
    if not isinstance(payload, dict):
        return None, None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for block in (data.get("glicko"), payload.get("glicko")):
        if isinstance(block, dict):
            h, a = _valid_xg_pair(block.get("homeXg"), block.get("awayXg"))
            if h is not None and a is not None:
                return h, a
    return None, None


def resolve_expected_goals(glicko_payload: Any, last_stats_payload: Any) -> dict[str, Any]:
    """Resolve provider xG plus the form lambdas behind it.

    glicko xG wins when present because it is fixture-specific; the form blend
    is the fallback and is also kept alongside it so the totals model can
    cross-check the two instead of trusting either blindly.
    """
    lam_home, lam_away, games = form_lambdas(last_stats_payload)
    g_home, g_away = glicko_xg(glicko_payload)
    out: dict[str, Any] = {"lambda_home": lam_home, "lambda_away": lam_away, "form_games": games, "glicko_home": g_home, "glicko_away": g_away}
    if g_home is not None and g_away is not None:
        out.update({"home": g_home, "away": g_away, "source": "glicko_xg"})
        return out
    if lam_home is not None and lam_away is not None:
        h, a = _valid_xg_pair(lam_home, lam_away)
        if h is not None and a is not None:
            out.update({"home": h, "away": a, "source": "last_games_form_goals"})
            return out
    out.update({"home": None, "away": None, "source": "missing"})
    return out


def _fold(text: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "").lower())
    stripped = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in stripped)


def team_tokens(*values: Any) -> set[str]:
    out: set[str] = set()
    for value in values:
        for token in _fold(value).split():
            if len(token) > 2 and token not in TEAM_STOPWORDS:
                out.add(token)
    return out


def row_team_tokens(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    home = team_tokens(row.get("home_team"))
    away = team_tokens(row.get("away_team"))
    if home or away:
        return home, away
    parts = str(row.get("match_key") or row.get("canonical_match_id") or "").split("|")
    if len(parts) >= 3:
        return team_tokens(parts[1]), team_tokens(parts[2])
    return set(), set()


def fixture_team_names(*payloads: Any) -> tuple[str, str]:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        for holder_key in ("fixture", "game"):
            holder = data.get(holder_key) if isinstance(data, dict) else None
            if not isinstance(holder, dict):
                continue
            home = holder.get("homeTeam") if isinstance(holder.get("homeTeam"), dict) else {}
            away = holder.get("awayTeam") if isinstance(holder.get("awayTeam"), dict) else {}
            if home.get("name") or away.get("name"):
                return str(home.get("name") or ""), str(away.get("name") or "")
    return "", ""


def _tokens_overlap(left: set[str], right: set[str]) -> bool:
    for a in left:
        for b in right:
            if a == b or (len(a) >= 4 and b.startswith(a)) or (len(b) >= 4 and a.startswith(b)):
                return True
    return False


def identity_status(row: dict[str, Any], *payloads: Any) -> tuple[str, str, str]:
    """Check that the SStats game id really is this inventory row's match.

    The probe caught one row receiving the payloads of five unconnected
    fixtures, so a wrong crosswalk id was silently writing another match's xG
    and prices onto it.  Rather than trust the crosswalk, compare the row's team
    names with the ones SStats itself reports for that game id.  Unconfirmable
    is not treated as wrong: some rows carry neither team names nor a match key.
    """
    home_name, away_name = fixture_team_names(*payloads)
    if not home_name and not away_name:
        return "unverified", home_name, away_name
    row_home, row_away = row_team_tokens(row)
    if not row_home and not row_away:
        return "unverified", home_name, away_name
    fixture_home = team_tokens(home_name)
    fixture_away = team_tokens(away_name)
    direct = _tokens_overlap(row_home, fixture_home) or _tokens_overlap(row_away, fixture_away)
    swapped = _tokens_overlap(row_home, fixture_away) or _tokens_overlap(row_away, fixture_home)
    if direct or swapped:
        return "verified", home_name, away_name
    return "mismatch", home_name, away_name


def _parse_total_selection(name: Any) -> tuple[str | None, float | None]:
    text = _fold(name).strip()
    for prefix, selection in (("over", "over"), ("under", "under")):
        if text.startswith(prefix):
            point = _float_or_none(text[len(prefix):].strip().replace(" ", "."))
            if point is not None and 0.5 <= point <= 7.5:
                return selection, round(float(point), 2)
    return None, None


def parse_sstats_totals_offers(payload: Any, row: dict[str, Any], game_id: str) -> list[dict[str, Any]]:
    """Turn /Odds/{id} into full-time totals offers.

    Shape from the probe:
        data[] = {bookmakerId, bookmakerName, odds: [{marketId, marketName,
                  odds: [{name: "Over 2.5", value: 1.75}]}]}

    Only marketId 5 "Goals Over/Under" is accepted.  marketId 6 and 26 carry the
    same "Over 1.5" labels for first and second half, and merging those into the
    full-time bucket would manufacture fake price disagreement, which is exactly
    what the value edge would then be measured against.  data can be null.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    books = payload.get("data")
    if not isinstance(books, list):
        return out
    match_key = str(row.get("match_key") or row.get("canonical_match_id") or "")
    seen: set[tuple[str, str, float, float]] = set()
    totals_market_seen = False
    for bookmaker in books:
        if not isinstance(bookmaker, dict):
            continue
        book_name = str(bookmaker.get("bookmakerName") or "").strip()
        markets = bookmaker.get("odds")
        if not book_name or not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            market_id = v2.as_int(market.get("marketId"), -1)
            market_name = str(market.get("marketName") or "").strip().lower()
            if market_id not in TOTALS_MARKET_IDS and market_name not in TOTALS_MARKET_NAMES:
                continue
            totals_market_seen = True
            offers = market.get("odds")
            if not isinstance(offers, list):
                continue
            for offer in offers:
                if not isinstance(offer, dict) or len(out) >= MAX_OFFERS_PER_ROW:
                    continue
                selection, point = _parse_total_selection(offer.get("name"))
                price = _float_or_none(offer.get("value"))
                if selection is None or point is None or price is None:
                    continue
                if price < 1.01 or price > 30.0:
                    continue
                signature = (book_name.lower(), selection, float(point), round(float(price), 3))
                if signature in seen:
                    continue
                seen.add(signature)
                _inc_map(_ODDS_STATS, "book_counts", book_name.lower())
                out.append({
                    "bookmaker": book_name.lower(),
                    "bookmaker_name": book_name,
                    "family": "totals",
                    "market_family": "totals",
                    "market": "totals",
                    "selection": selection,
                    "selection_key": selection,
                    "point": float(point),
                    "line": float(point),
                    "price": round(float(price), 3),
                    "odds": round(float(price), 3),
                    "source": "sstats",
                    "provider": "sstats",
                    "match_key": match_key,
                    "canonical_match_id": str(row.get("canonical_match_id") or match_key),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "commence_time": row.get("commence_time") or row.get("kickoff_utc"),
                    "sstats_game_id": str(game_id),
                })
    if not totals_market_seen:
        _inc(_ODDS_STATS, "no_totals_market")
    return out


def describe_shape(payload: Any, depth: int = 0) -> Any:
    """Describe a payload's structure without dumping the whole thing."""
    if depth >= 4:
        return "..."
    if isinstance(payload, dict):
        return {str(key): describe_shape(value, depth + 1) for key, value in list(payload.items())[:40]}
    if isinstance(payload, list):
        if not payload:
            return []
        return [describe_shape(payload[0], depth + 1), f"...+{max(0, len(payload) - 1)} more items"]
    if isinstance(payload, str):
        return f"str:{payload[:60]}"
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    return type(payload).__name__


def raw_preview(payload: Any, limit: int = 900) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated, total {len(text)} chars]"


def record_xg_probe(row: dict[str, Any], game_id: str, *, glicko_payload: Any = None, last_stats_payload: Any = None) -> None:
    if len(_XG_PROBE) >= XG_PROBE_LIMIT:
        return
    _XG_PROBE.append({
        "match_key": str(row.get("match_key") or row.get("canonical_match_id") or ""),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "game_id": str(game_id),
        "existing_expected_home": row.get("expected_home"),
        "existing_expected_away": row.get("expected_away"),
        "glicko_shape": describe_shape(glicko_payload),
        "last_games_stats_shape": describe_shape(last_stats_payload),
        "glicko_raw_preview": raw_preview(glicko_payload),
        "last_games_stats_raw_preview": raw_preview(last_stats_payload, 1200),
    })


def record_odds_probe(row: dict[str, Any], game_id: str, payload: Any, offers: list[dict[str, Any]]) -> None:
    if len(_ODDS_PROBE) >= ODDS_PROBE_LIMIT or payload in (None, ""):
        return
    _ODDS_PROBE.append({
        "match_key": str(row.get("match_key") or row.get("canonical_match_id") or ""),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "game_id": str(game_id),
        "parsed_offer_count": len(offers),
        "parsed_offer_sample": offers[:6],
        "odds_shape": describe_shape(payload),
    })


def set_count_from_sources(row: dict[str, Any], key: str, list_key: str, allowed: set[str]) -> int:
    value = len(clean_sources(row, list_key, allowed))
    row[key] = value
    cov = row.setdefault("coverage", {})
    if isinstance(cov, dict):
        cov[key] = value
    return value


def mark(row: dict[str, Any], game_id: str, deep_ok: bool, detail_ok: bool, odds_ok: bool, before_context: int, before_odds: int, *, glicko_payload: Any = None, last_stats_payload: Any = None, detail_payload: Any = None, offers: list[dict[str, Any]] | None = None) -> None:
    row.setdefault("source_ids", {})["sstats"] = str(game_id)
    row.setdefault("provider_source_ids", {})["sstats"] = str(game_id)
    v2.add_src(row, "sources_seen", "sstats")
    row["sstats_game_id"] = str(game_id)
    row["sstats_deep_enriched"] = deep_ok
    row["sstats_detail_enriched"] = detail_ok
    row["sstats_odds_rescue_enriched"] = odds_ok
    cov = row.setdefault("coverage", {})
    if not isinstance(cov, dict):
        cov = {}
        row["coverage"] = cov
    existing_home, existing_away = _valid_xg_pair(row.get("expected_home"), row.get("expected_away"))
    if is_proxy_placeholder(row.get("expected_home"), row.get("expected_away")):
        existing_home, existing_away = None, None
        _inc(_XG_STATS, "placeholder_rejected")
    resolved = resolve_expected_goals(glicko_payload, last_stats_payload)
    xg_home = resolved.get("home")
    xg_away = resolved.get("away")
    xg_source = str(resolved.get("source") or "missing")
    _inc(_XG_STATS, "attempted")
    if xg_home is not None and xg_away is not None:
        _inc(_XG_STATS, "resolved_real")
    else:
        record_xg_probe(row, game_id, glicko_payload=glicko_payload, last_stats_payload=last_stats_payload)
        xg_home, xg_away, xg_source = existing_home, existing_away, "existing_inventory"
        if xg_home is not None and xg_away is not None:
            _inc(_XG_STATS, "kept_existing")
        else:
            _inc(_XG_STATS, "missing")
            xg_source = "missing"
    _inc_map(_XG_STATS, "source_counts", xg_source)
    has_xg_pair = xg_home is not None and xg_away is not None
    real_xg = has_xg_pair and xg_source in REAL_XG_SOURCES
    if has_xg_pair:
        row["expected_home"] = xg_home
        row["expected_away"] = xg_away
        row["sstats_expected_home"] = xg_home
        row["sstats_expected_away"] = xg_away
        row["sstats_xg_source"] = xg_source
    lam_home = resolved.get("lambda_home")
    lam_away = resolved.get("lambda_away")
    if lam_home is not None and lam_away is not None:
        row["sstats_lambda_home"] = lam_home
        row["sstats_lambda_away"] = lam_away
        row["sstats_form_games"] = resolved.get("form_games")
    if deep_ok:
        v2.add_src(row, "context_sources", "sstats")
        v2.add_src(row, "form_sources", "sstats")
        set_count_from_sources(row, "context_sources_count", "context_sources", CONTEXT_PROVIDERS)
        if real_xg:
            v2.add_src(row, "xg_sources", "sstats")
            set_count_from_sources(row, "xg_sources_count", "xg_sources", CONTEXT_PROVIDERS)
        set_count_from_sources(row, "form_sources_count", "form_sources", CONTEXT_PROVIDERS)
        row["latest_context_sources_max"] = max(v2.as_int(row.get("latest_context_sources_max")), v2.as_int(row.get("context_sources_count")))
        row["latest_confirmation_sources_max"] = max(v2.as_int(row.get("latest_confirmation_sources_max")), v2.as_int(row.get("context_sources_count")))
        cov.update({"context": True, "form": True})
        cov["xg"] = has_xg_pair
    if detail_ok:
        cov.update({"lineups": True, "venue_referee": True})
    if offers:
        row["sstats_offers"] = offers
        row["sstats_offer_count"] = len(offers)
        row["sstats_offer_books"] = sorted({str(offer.get("bookmaker")) for offer in offers})
    if odds_ok:
        v2.add_src(row, "odds_sources", "sstats")
        set_count_from_sources(row, "odds_sources_count", "odds_sources", ODDS_PROVIDERS)
        row["price_confirmation_sources_count"] = max(v2.as_int(row.get("price_confirmation_sources_count")), v2.as_int(row.get("odds_sources_count")))
        row["latest_odds_sources_max"] = max(v2.as_int(row.get("latest_odds_sources_max")), v2.as_int(row.get("odds_sources_count")))
        cov.update({"odds": True, "odds_sources_count": v2.as_int(row.get("odds_sources_count"))})


def bucket_rank(bucket: str) -> int:
    return {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}.get(str(bucket or "unknown"), 5)


def priority(item: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> tuple[int, int, str, str]:
    key = str(item.get("match_key") or "").strip()
    row = by_key.get(key)
    if not key or not isinstance(row, dict):
        return (9, 9, "", key)
    context = count_family(row, "context")
    odds = count_family(row, "odds")
    has_xg = has_valid_xg(row)
    ctx_sources = set(clean_sources(row, "context_sources", CONTEXT_PROVIDERS))
    if context < 2 and "sstats" not in ctx_sources:
        group = 0
    elif not has_xg:
        group = 1
    elif odds < 2:
        group = 2
    else:
        group = 3
    return (group, bucket_rank(str(item.get("bucket") or "unknown")), str(item.get("kickoff_utc") or ""), key)


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cross = v2.load(OUT_DIR / "latest-sstats-crosswalk.json", {})
    if not isinstance(cross.get("summary"), dict):
        cross = await sstats_crosswalk_probe.run()
    primary_path = v2.inv_path(cross)
    inventory = v2.load(primary_path, {})
    matches = inventory.get("matches") if isinstance(inventory, dict) and isinstance(inventory.get("matches"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        for alias in (str(match.get("match_key") or "").strip(), str(match.get("canonical_match_id") or "").strip()):
            if alias:
                by_key.setdefault(alias, match)
    raw_queue = [q for q in (cross.get("enrichment_queue") or []) if isinstance(q, dict)]
    queue = sorted(raw_queue, key=lambda item: priority(item, by_key))
    max_req = max(0, v2.as_int(v2.env("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN"), 100))
    detail_left = max(0, v2.as_int(v2.env("SSTATS_GAME_DETAIL_LIMIT_PER_RUN"), 12))
    odds_left = max(0, v2.as_int(v2.env("SSTATS_ODDS_RESCUE_LIMIT_PER_RUN"), 30))
    threshold = max(1, v2.as_int(v2.env("SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT"), 2))
    req = 0
    enriched: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    group_counts: dict[str, int] = {}
    timeout = float(v2.env("SSTATS_DEEP_ENRICHMENT_TIMEOUT_SECONDS", "16"))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers={"User-Agent": "HARIZON-sstats-deep-v4"}) as client:
        for item in queue:
            if req + 2 > max_req:
                break
            key = str(item.get("match_key") or "").strip()
            game_id = str(item.get("sstats_game_id") or "").strip()
            if not key:
                _inc(_ID_STATS, "skipped_empty_key")
                continue
            row = by_key.get(key)
            if not game_id or not isinstance(row, dict):
                _inc(_ID_STATS, "skipped_no_row")
                continue
            before_context = count_family(row, "context")
            before_odds = count_family(row, "odds")
            group = f"context{before_context}_odds{before_odds}_xg{int(has_valid_xg(row))}"
            group_counts[group] = group_counts.get(group, 0) + 1
            g = await v2.call(client, "glicko", f"/Games/glicko/{game_id}", {}, include_payload=True)
            req += 1
            status, fixture_home, fixture_away = identity_status(row, g.get("payload"))
            if status == "mismatch":
                _inc(_ID_STATS, "mismatch")
                if len(_MISMATCHES) < MISMATCH_SAMPLE_LIMIT:
                    _MISMATCHES.append({"match_key": key, "game_id": game_id, "row_home": row.get("home_team"), "row_away": row.get("away_team"), "sstats_home": fixture_home, "sstats_away": fixture_away})
                statuses.append({k: v for k, v in g.items() if k != "payload"})
                continue
            _inc(_ID_STATS, "verified" if status == "verified" else "unverified")
            l = await v2.call(client, "last_games_stats", "/Games/last-games-stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"}, include_payload=True)
            req += 1
            d = {"status": "SKIPPED", "rows": 0}
            o = {"status": "SKIPPED", "rows": 0}
            offers: list[dict[str, Any]] = []
            if before_odds < threshold and odds_left and req < max_req:
                o = await v2.call(client, "odds", f"/Odds/{game_id}", {"opening": "false"}, include_payload=True)
                odds_left -= 1
                req += 1
                _inc(_ODDS_STATS, "rows_attempted")
                if o.get("status") == "OK":
                    _inc(_ODDS_STATS, "payload_ok")
                    offers = parse_sstats_totals_offers(o.get("payload"), row, game_id)
                    _inc(_ODDS_STATS, "offers_parsed", len(offers))
                    if offers:
                        _inc(_ODDS_STATS, "rows_with_offers")
                        _ALL_OFFERS.extend(offers)
                    record_odds_probe(row, game_id, o.get("payload"), offers)
            if detail_left and req < max_req:
                d = await v2.call(client, "game_detail", f"/Games/{game_id}", {}, include_payload=True)
                detail_left -= 1
                req += 1
            statuses.extend([{k: v for k, v in response.items() if k != "payload"} for response in (g, l, d, o)])
            deep_ok = g.get("status") == "OK" or l.get("status") == "OK"
            detail_ok = d.get("status") == "OK"
            odds_ok = bool(offers)
            mark(row, game_id, deep_ok, detail_ok, odds_ok, before_context, before_odds, glicko_payload=g.get("payload"), last_stats_payload=l.get("payload"), detail_payload=d.get("payload"), offers=offers)
            if deep_ok or detail_ok or odds_ok:
                enriched.append({"match_key": key, "game_id": game_id, "identity": status, "home_team": row.get("home_team"), "away_team": row.get("away_team"), "deep_ok": deep_ok, "detail_ok": detail_ok, "odds_ok": odds_ok, "offer_count": len(offers), "before_context": before_context, "after_context": row.get("context_sources_count"), "before_odds": before_odds, "after_odds": row.get("odds_sources_count"), "expected_home": row.get("expected_home"), "expected_away": row.get("expected_away"), "xg_source": row.get("sstats_xg_source"), "lambda_home": row.get("sstats_lambda_home"), "lambda_away": row.get("sstats_lambda_away")})
    if isinstance(inventory, dict):
        meta = inventory.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["sstats_deep_inventory_enrichment"] = {"created_at_utc": datetime.now(UTC).isoformat(), "request_count": req, "enriched_matches": len(enriched), "sstats_offers": len(_ALL_OFFERS), "version": "v5_real_xg_and_second_price_source"}
    for path in inventory_aliases(primary_path):
        v2.write(path, inventory)
    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s.get("status"))] = counts.get(str(s.get("status")), 0) + 1
    xg_extraction = dict(_XG_STATS)
    odds_extraction = dict(_ODDS_STATS)
    identity = dict(_ID_STATS)
    v2.write(XG_PROBE_OUT, {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_xg_extraction_probe_v2", "status": "ok", "xg_extraction": xg_extraction, "odds_extraction": odds_extraction, "identity": identity, "samples": _XG_PROBE, "odds_samples": _ODDS_PROBE, "identity_mismatch_sample": _MISMATCHES})
    v2.write(ODDS_OFFERS_OUT, {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_totals_offers_v1", "status": "ok", "source": "sstats", "market_family": "totals", "offer_count": len(_ALL_OFFERS), "book_counts": odds_extraction.get("book_counts"), "offers": _ALL_OFFERS})
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_deep_inventory_enrichment_v5_real_xg_and_second_price_source", "status": "ok", "inventory_path": str(primary_path), "inventory_aliases_written": [str(p) for p in inventory_aliases(primary_path)], "crosswalk_matched": (cross.get("summary") or {}).get("matched"), "queue_seen": len(raw_queue), "request_count": req, "enriched_matches": len(enriched), "priority_group_counts": group_counts, "command_status_counts": counts, "xg_extraction": xg_extraction, "odds_extraction": odds_extraction, "identity": identity, "sstats_offers": len(_ALL_OFFERS), "xg_probe_samples": len(_XG_PROBE), "identity_mismatch_sample": _MISMATCHES, "enriched_sample": enriched[:50], "command_sample": statuses[:20]}
    v2.write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = ["# SStats deep inventory enrichment v4", f"status: {payload.get('status')}", f"inventory_path: {payload.get('inventory_path')}", f"aliases_written: {', '.join(payload.get('inventory_aliases_written') or [])}", f"crosswalk_matched: {payload.get('crosswalk_matched')}", f"queue_seen: {payload.get('queue_seen')}", f"request_count: {payload.get('request_count')}", f"enriched_matches: {payload.get('enriched_matches')}", f"sstats_offers: {payload.get('sstats_offers')}", f"priority_group_counts: {json.dumps(payload.get('priority_group_counts') or {}, ensure_ascii=False)}", f"command_status_counts: {json.dumps(payload.get('command_status_counts') or {}, ensure_ascii=False)}", f"xg_extraction: {json.dumps(payload.get('xg_extraction') or {}, ensure_ascii=False)}", f"odds_extraction: {json.dumps(payload.get('odds_extraction') or {}, ensure_ascii=False)}", f"identity: {json.dumps(payload.get('identity') or {}, ensure_ascii=False)}", "", "## Enriched sample"]
    for item in payload.get("enriched_sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} id={item.get('identity')} deep={item.get('deep_ok')} detail={item.get('detail_ok')} offers={item.get('offer_count')} xg={item.get('xg_source')} context:{item.get('before_context')}→{item.get('after_context')} odds:{item.get('before_odds')}→{item.get('after_odds')}")
    if payload.get("identity_mismatch_sample"):
        lines.append("")
        lines.append("## Identity mismatches (crosswalk id pointed at another fixture)")
        for item in payload.get("identity_mismatch_sample") or []:
            lines.append(f"- gameId={item.get('game_id')} row={item.get('row_home')} vs {item.get('row_away')} | sstats={item.get('sstats_home')} vs {item.get('sstats_away')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
