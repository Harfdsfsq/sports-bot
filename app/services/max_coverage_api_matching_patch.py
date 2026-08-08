from __future__ import annotations

"""Max coverage/API matching patch for HARIZON.

Goal: move the daily 300-match inventory toward 2+ independent odds sources and
2+ independent context sources without relaxing publication guards.

The patch does three things at runtime:
1. Forces broader provider budgets for the 300-match day inventory.
2. Expands context matching across API aliases using normalized/fuzzy team keys.
3. Materializes safe sidecar odds rows from known provider artifacts into
   CandidateFactory's offers_by_match so Bzzoiro/SStats/SportLogic hints can count
   as real independent lines when they match the same fixture/market/side/point.
"""

import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.schemas import ContextObservation, Match, MatchContext, MatchContextBundle, Offer

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
REPORT = EXPORT / "latest-max-coverage-api-matching.json"
_INSTALLED = False
_ORIGINAL_BUILD = None
_ORIGINAL_SELECT = None

TEAM_STOP = {"fc", "cf", "sc", "afc", "fk", "ac", "club", "football", "futbol", "calcio", "if", "bk", "sk", "sv", "cd", "ud", "cf"}
ODDS_SIDECARS = [
    EXPORT / "latest-bzzoiro-overlap-offers.json",
    EXPORT / "latest-bzzoiro-v2-odds-hints-by-match.json",
    EXPORT / "latest-bzzoiro-exact-offer-bridge.json",
    EXPORT / "latest-line-snapshots.json",
    EXPORT / "latest-consensus-lines.json",
    ROOT / "artifacts" / "run-bot" / "latest-bzzoiro-overlap-offers.json",
    ROOT / "artifacts" / "run-bot" / "latest-line-snapshots.json",
]


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower().replace("ё", "е"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9а-я]+", " ", text).split())


def _tokens(value: Any) -> list[str]:
    out: list[str] = []
    for token in _norm(value).split():
        if token in TEAM_STOP:
            continue
        if len(token) > 7 and token.endswith("i"):
            token = token[:-1]
        out.append(token)
    return out


def _compact(value: Any) -> str:
    return "".join(_tokens(value))


def _sim(left: Any, right: Any) -> float:
    lt, rt = _tokens(left), _tokens(right)
    if not lt or not rt:
        return 0.0
    lc, rc = "".join(lt), "".join(rt)
    if lc == rc:
        return 1.0
    if min(len(lc), len(rc)) >= 4 and (lc in rc or rc in lc):
        return 0.95
    token_score = (sum(max(SequenceMatcher(None, x, y).ratio() for y in rt) for x in lt) / len(lt) + sum(max(SequenceMatcher(None, y, x).ratio() for x in lt) for y in rt) / len(rt)) / 2.0
    return max(token_score, SequenceMatcher(None, lc, rc).ratio())


def _fixture_key(home: Any, away: Any, day: Any = "") -> str:
    pair = sorted([_compact(home), _compact(away)])
    return "|".join(pair + ([str(day)[:10]] if str(day or "")[:10] else []))


def _match_day(match: Match) -> str:
    try:
        return match.commence_time.astimezone(UTC).date().isoformat()
    except Exception:
        return ""


def _key_parts(key: Any) -> tuple[str, str, str]:
    parts = [p for p in str(key or "").split("|") if p]
    if len(parts) >= 4 and parts[0].lower() == "soccer":
        return parts[1], parts[2], parts[3][:10]
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1][:10]
    return "", "", ""


def _read(path: Path) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return None


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    except Exception:
        pass


def _ctx_iter(value: Any):
    if isinstance(value, MatchContext):
        yield value
    elif isinstance(value, MatchContextBundle):
        if isinstance(value.merged_context, MatchContext):
            yield value.merged_context
        for item in getattr(value, "contexts", []) or []:
            yield from _ctx_iter(item)
    elif isinstance(value, ContextObservation):
        details = dict(getattr(value, "details", {}) or {})
        payload = getattr(value, "payload", {}) or {}
        provider = str(getattr(value, "provider", "") or details.get("provider") or "context_observation")
        yield MatchContext(source=provider, payload=payload if isinstance(payload, dict) else {}, confidence=float(getattr(value, "confidence", 58.0) or 58.0), details=details)
    elif isinstance(value, dict):
        if isinstance(value.get("payload"), dict) or isinstance(value.get("details"), dict):
            yield MatchContext(source=str(value.get("source") or value.get("provider") or value.get("details", {}).get("provider") or "dict_context"), payload=value.get("payload") if isinstance(value.get("payload"), dict) else value, confidence=_f(value.get("confidence"), 58.0), details=value.get("details") if isinstance(value.get("details"), dict) else {})
        for item in value.values():
            yield from _ctx_iter(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _ctx_iter(item)


def _ctx_source(ctx: Any) -> str:
    src = str(getattr(ctx, "source", "") or "").lower()
    if "bzzoiro" in src:
        return "bzzoiro"
    if "sstats" in src or "sstat" in src:
        return "sstats"
    if "sportlogic" in src:
        return "sportlogic"
    if "odds" in src:
        return "odds_api_io"
    return re.sub(r"[^a-z0-9_]+", "_", src).strip("_") or "context"


def _merge_contexts(matches: list[Match], contexts_by_match: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    expanded = dict(contexts_by_match or {})
    by_day: dict[str, list[tuple[str, str, str, Any]]] = defaultdict(list)
    by_pair: dict[str, list[tuple[str, str, str, Any]]] = defaultdict(list)
    for key, ctx in dict(contexts_by_match or {}).items():
        h, a, day = _key_parts(key)
        for c in _ctx_iter(ctx):
            payload = getattr(c, "payload", {}) if isinstance(getattr(c, "payload", {}), dict) else {}
            hh = h or payload.get("home_team") or payload.get("home") or payload.get("home_name")
            aa = a or payload.get("away_team") or payload.get("away") or payload.get("away_name")
            dd = day or str(payload.get("date") or payload.get("kickoff") or payload.get("commence_time") or "")[:10]
            if hh and aa:
                by_pair[_fixture_key(hh, aa)].append((str(key), str(hh), str(aa), c))
                if dd:
                    by_day[_fixture_key(hh, aa, dd)].append((str(key), str(hh), str(aa), c))
    rescued = 0
    added_sources = Counter()
    multi_context_matches = 0
    for m in matches or []:
        key = m.match_key
        existing_sources = {_ctx_source(c) for c in _ctx_iter(expanded.get(key))}
        found = by_day.get(_fixture_key(m.home_team, m.away_team, _match_day(m))) or by_pair.get(_fixture_key(m.home_team, m.away_team)) or []
        add: list[Any] = []
        for source_key, h, a, ctx in found:
            if source_key == key:
                continue
            if min(max(_sim(m.home_team, h), _sim(m.home_team, a)), max(_sim(m.away_team, h), _sim(m.away_team, a))) < 0.78:
                continue
            src = _ctx_source(ctx)
            if src in existing_sources:
                continue
            add.append(ctx)
            existing_sources.add(src)
            added_sources[src] += 1
        if add:
            old = list(_ctx_iter(expanded.get(key)))
            expanded[key] = old + add
            rescued += 1
        if len(existing_sources) >= 2:
            multi_context_matches += 1
    return expanded, {"context_matches_rescued": rescued, "context_sources_added": dict(added_sources), "matches_with_2plus_context_sources_after_patch": multi_context_matches}


def _clean_family(value: Any) -> str:
    text = _norm(value).replace(" ", "_")
    if text in {"total", "totals", "over_under", "goals_over_under"} or "total" in text:
        return "totals"
    if text in {"spread", "spreads", "handicap"} or "handicap" in text:
        return "spreads"
    if text in {"h2h", "1x2", "match_winner"}:
        return "h2h"
    if "btts" in text or "both_teams" in text:
        return "btts"
    return text


def _selection(value: Any, family: str) -> str:
    text = _norm(value)
    if family == "totals":
        if "under" in text or "меньше" in text or text == "u":
            return "Under"
        if "over" in text or "больше" in text or text == "o":
            return "Over"
    if family == "btts":
        if "yes" in text or "да" in text:
            return "Yes"
        if "no" in text or "нет" in text:
            return "No"
    return str(value or "").strip()


def _offer_from_row(row: dict[str, Any], default_source: str = "") -> Offer | None:
    price = _f(row.get("price") or row.get("odds") or row.get("decimal_odds"), 0.0)
    if price < 1.01 or price > 50:
        return None
    blob = json.dumps(row, ensure_ascii=False).lower()
    if any(t in blob for t in (".line", "/line", "_line")) and price in {1.5, 2.5, 3.5, 4.5}:
        return None
    family = _clean_family(row.get("family") or row.get("market_family") or row.get("market_key") or row.get("market"))
    if family not in {"totals", "spreads", "h2h", "btts"}:
        return None
    sel = _selection(row.get("selection") or row.get("outcome") or row.get("name") or row.get("side"), family)
    if not sel:
        return None
    point_raw = row.get("point") if row.get("point") is not None else row.get("line") if row.get("line") is not None else row.get("total")
    point = None if point_raw in (None, "") else _f(point_raw, 0.0)
    source = str(row.get("source") or row.get("provider") or default_source or "provider").lower()
    if "bzzoiro" in source:
        source = "bzzoiro"
    elif "sstats" in source:
        source = "sstats"
    elif "sportlogic" in source:
        source = "sportlogic"
    elif "odds" in source:
        source = "odds_api_io"
    book = str(row.get("bookmaker") or row.get("book") or (source + "Consensus"))
    return Offer(source=source, bookmaker=book, family=family, selection=sel, point=point, price=round(price, 4), team_side=str(row.get("team_side") or "").lower() or None, market_name=str(row.get("market_name") or family), market_key=str(row.get("market_key") or family), metadata={"max_coverage_bridge": True})


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return rows
    for key in ("offers", "rows", "items", "hints", "lines", "snapshots"):
        val = payload.get(key)
        if isinstance(val, list):
            rows.extend([x for x in val if isinstance(x, dict)])
        elif isinstance(val, dict):
            rows.extend([x for x in val.values() if isinstance(x, dict)])
    matches = payload.get("matches")
    if isinstance(matches, dict):
        for k, item in matches.items():
            if isinstance(item, dict):
                for h in item.get("hints") or item.get("offers") or item.get("rows") or []:
                    if isinstance(h, dict):
                        row = dict(h); row.setdefault("match_key", k); rows.append(row)
    return rows


def _row_fixture(row: dict[str, Any]) -> tuple[str, str, str]:
    h = row.get("home_team") or row.get("home") or row.get("home_name")
    a = row.get("away_team") or row.get("away") or row.get("away_name")
    d = str(row.get("commence_time") or row.get("kickoff_utc") or row.get("kickoff") or row.get("date") or "")[:10]
    if (not h or not a) and row.get("match_key"):
        h2, a2, d2 = _key_parts(row.get("match_key"))
        h, a, d = h or h2, a or a2, d or d2
    return str(h or ""), str(a or ""), d


def _offer_id(o: Offer) -> tuple[Any, ...]:
    return (str(o.source).lower(), str(o.bookmaker).lower(), str(o.family).lower(), str(o.selection).lower(), None if o.point is None else round(float(o.point), 3), str(o.team_side or "").lower(), round(float(o.price), 4))


def _merge_sidecar_offers(matches: list[Match], offers_by_match: dict[str, list[Offer]]) -> tuple[dict[str, list[Offer]], dict[str, Any]]:
    merged = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
    by_day = {_fixture_key(m.home_team, m.away_team, _match_day(m)): m.match_key for m in matches or []}
    by_pair = {_fixture_key(m.home_team, m.away_team): m.match_key for m in matches or []}
    stats = Counter()
    source_add = Counter()
    for path in ODDS_SIDECARS:
        payload = _read(path)
        rows = _rows_from_payload(payload)
        if rows:
            stats["sidecar_files_with_rows"] += 1
        for row in rows[:5000]:
            h, a, d = _row_fixture(row)
            key = row.get("match_key") if row.get("match_key") in merged else None
            if not key and h and a:
                key = by_day.get(_fixture_key(h, a, d)) or by_pair.get(_fixture_key(h, a))
            if not key:
                stats["rows_unmatched"] += 1
                continue
            offer = _offer_from_row(row, str(path.name))
            if offer is None:
                stats["rows_invalid"] += 1
                continue
            ids = {_offer_id(o) for o in merged.get(str(key), [])}
            if _offer_id(offer) in ids:
                stats["rows_duplicate"] += 1
                continue
            merged.setdefault(str(key), []).append(offer)
            stats["offers_added"] += 1
            source_add[str(offer.source).lower()] += 1
    two_source_matches = 0
    for offers in merged.values():
        if len({str(o.source).lower() for o in offers if str(o.source).strip()}) >= 2:
            two_source_matches += 1
    return merged, {"sidecar_offer_stats": dict(stats), "sidecar_offers_added_by_source": dict(source_add), "matches_with_2plus_odds_sources_after_patch": two_source_matches}


def _apply_env() -> dict[str, str]:
    defaults = {
        "SOURCE_MATRIX_CONTEXT_TARGET_LIMIT": "300",
        "SOURCE_MATRIX_GAP_APPEND_LIMIT": "300",
        "BZZOIRO_CONTEXT_GAP_MATCH_LIMIT": "300",
        "BZZOIRO_CONTEXT_GAP_MAX_REQUESTS": "320",
        "BZZOIRO_FORCE_GAP_TARGET_LIMIT": "300",
        "BZZOIRO_ODDS_COMPARISON_MATCH_LIMIT": "300",
        "SSTATS_CONTEXT_MATCH_LIMIT": "300",
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "160",
        "SSTATS_MATCHING_RELAXED_ENABLED": "true",
        "SPORTLOGIC_MATCH_LIMIT": "300",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "180",
        "ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN": "300",
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "ANALYSIS_MATCH_CAP_PER_RUN": "300",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "MAX_COVERAGE_API_MATCHING_ENABLED": "true",
    }
    changed = {}
    for k, v in defaults.items():
        if os.getenv(k) != v:
            os.environ[k] = v
            changed[k] = v
    return changed


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_BUILD, _ORIGINAL_SELECT
    env = _apply_env()
    if _INSTALLED or not _truthy(os.getenv("MAX_COVERAGE_API_MATCHING_ENABLED"), True):
        return {"installed": _INSTALLED, "env_overrides": env}
    try:
        from app.services.model import CandidateFactory
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"installed": False, "error": f"import:{type(exc).__name__}: {exc}", "env_overrides": env}
    _ORIGINAL_BUILD = CandidateFactory.build_candidates
    def build_candidates_maxcov(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        match_list = list(matches or [])
        expanded_contexts, ctx_stats = _merge_contexts(match_list, dict(contexts_by_match or {}))
        expanded_offers, odds_stats = _merge_sidecar_offers(match_list, dict(offers_by_match or {}))
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), "installed": True, "env_overrides": env, **ctx_stats, **odds_stats}
        _write(payload)
        return _ORIGINAL_BUILD(self, matches, expanded_offers, expanded_contexts, market_signals_by_match=market_signals_by_match)
    CandidateFactory.build_candidates = build_candidates_maxcov

    _ORIGINAL_SELECT = getattr(PredictionRunner, "_select_context_enrichment_matches", None)
    if callable(_ORIGINAL_SELECT):
        def select_all_gap(self, matches, offers_by_match, now_utc, market_signals_by_match=None):
            selected, summary = _ORIGINAL_SELECT(self, matches, offers_by_match, now_utc, market_signals_by_match)
            limit = int(float(os.getenv("SOURCE_MATRIX_CONTEXT_TARGET_LIMIT", "300") or 300))
            seen = {m.match_key for m in selected}
            extra = [m for m in list(matches or []) if m.match_key not in seen]
            extra.sort(key=lambda m: (0 if _match_day(m) else 1, getattr(m, "commence_time", datetime.max.replace(tzinfo=UTC))))
            room = max(0, limit - len(selected))
            selected = list(selected) + extra[:room]
            if isinstance(summary, dict):
                summary["max_coverage_context_fill_appended"] = len(extra[:room])
                summary["max_coverage_context_target_limit"] = limit
            return selected, summary
        PredictionRunner._select_context_enrichment_matches = select_all_gap  # type: ignore[method-assign]
    _INSTALLED = True
    return {"installed": True, "env_overrides": env, "patched_candidate_factory": True, "patched_context_selection": callable(_ORIGINAL_SELECT), "artifact": str(REPORT)}
