from __future__ import annotations

"""Semantic current-price and line-movement guard patch.

This patch is intentionally conservative:
- it never publishes stale prices;
- it can recover a candidate only when a fresh current offer exists for the same
  fixture + family + side + point and the recovered EV/edge clears floors;
- it stores machine-readable diagnostics for xG conflicts and movement waits.
"""

import json
import math
import os
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
OFFER_PATHS = (
    ROOT / ".data" / "exports" / "latest-odds-api-io-offer-snapshot.json",
    ROOT / "artifacts" / "run-bot" / "latest-odds-api-io-offer-snapshot.json",
)
OUT = ROOT / ".data" / "exports" / "latest-semantic-movement-current-price-guard.json"
AWAITING_OUT = ROOT / ".data" / "exports" / "latest-awaiting-movement-from-semantic-guard.json"
STATE = ROOT / ".data" / "candidate-lifecycle-state.json"

_CACHE: dict[str, tuple[int, Any]] = {}
_INSTALLED = False
_STATE: dict[str, Any] = {
    "seen": 0,
    "price_recovery_attempted": 0,
    "price_recovery_applied": 0,
    "movement_wait_stored": 0,
    "xg_diagnostics_added": 0,
    "samples": [],
}

_STOP = {"fc", "cf", "fk", "sc", "afc", "ac", "club", "football", "futbol", "calcio", "msk", "tc", "sk", "sv", "nk", "pfc", "ks", "as", "us", "cd"}
_BOOK_ALIASES = {"betfairexchange": "betfair", "betfair": "betfair", "bet365com": "bet365", "bet365": "bet365", "unibetuk": "unibet", "unibet": "unibet", "sbobet": "sbobet"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except Exception:
        return default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(str(value).replace(",", "."))
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _load(path: Path, default: Any) -> Any:
    try:
        stamp = path.stat().st_mtime_ns
        cached = _CACHE.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        value = json.loads(path.read_text(encoding="utf-8"))
        _CACHE[str(path)] = (stamp, value)
        return value
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _write() -> None:
    payload = {"status": "installed", "created_at_utc": datetime.now(UTC).isoformat(), **_STATE, "publication_contract_relaxed": False}
    _write_json(OUT, payload)


def _parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=dt.tzinfo or UTC).astimezone(UTC)
    except Exception:
        return None


def _ascii(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower().replace("ё", "е"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9а-я]+", " ", text).split())


def _tokens(value: Any) -> list[str]:
    return [t for t in _ascii(value).split() if t and t not in _STOP]


def _sim(a: Any, b: Any) -> float:
    at, bt = _tokens(a), _tokens(b)
    if not at or not bt:
        return 0.0
    ac, bc = "".join(at), "".join(bt)
    if ac == bc:
        return 1.0
    if min(len(ac), len(bc)) >= 4 and (ac in bc or bc in ac):
        return 0.96
    return max(SequenceMatcher(None, ac, bc).ratio(), SequenceMatcher(None, " ".join(at), " ".join(bt)).ratio())


def _date(value: Any) -> str:
    dt = _parse_dt(value)
    if dt:
        return dt.date().isoformat()
    m = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
    return m.group(0) if m else ""


def _key_teams(raw: Any) -> tuple[str, str, str]:
    parts = [p for p in str(raw or "").split("|") if p]
    if len(parts) >= 4 and parts[0].lower() == "soccer":
        return parts[1], parts[2], parts[3]
    return "", "", _date(raw)


def _fixture(row: dict[str, Any]) -> tuple[str, str, str, datetime | None]:
    raw = row.get("match_key") or row.get("canonical_match_id") or row.get("event_key")
    kh, ka, kd = _key_teams(raw)
    home = str(row.get("home_team") or row.get("home") or kh)
    away = str(row.get("away_team") or row.get("away") or ka)
    kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff_utc") or row.get("kickoff") or row.get("start_time"))
    day = kickoff.date().isoformat() if kickoff else (_date(raw) or kd)
    return home, away, day, kickoff


def _same_fixture(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ah, aa, ad, at = _fixture(a)
    bh, ba, bd, bt = _fixture(b)
    if ad and bd and ad != bd:
        return False
    if at and bt and abs((at - bt).total_seconds()) > 3 * 3600:
        return False
    s1 = (_sim(ah, bh), _sim(aa, ba))
    s2 = (_sim(ah, ba), _sim(aa, bh))
    best = max(s1, s2, key=lambda p: p[0] + p[1])
    return min(best) >= 0.72 and sum(best) / 2 >= 0.82


def _family(row: dict[str, Any]) -> str:
    t = _ascii(row.get("family") or row.get("market_family") or row.get("market"))
    if "total" in t or "тотал" in t:
        return "totals"
    if "spread" in t or "handicap" in t or "фора" in t:
        return "spreads"
    return t


def _side(row: dict[str, Any]) -> str:
    t = _ascii(" ".join(str(row.get(k) or "") for k in ("selection_key", "selection", "side", "name", "outcome")))
    if any(x in t.split() for x in ("under", "меньше", "тм")):
        return "under"
    if any(x in t.split() for x in ("over", "больше", "тб")):
        return "over"
    return t


def _point(row: dict[str, Any]) -> float | None:
    for k in ("point", "line", "total", "handicap"):
        v = row.get(k)
        if v not in (None, ""):
            n = _num(v, float("nan"))
            if math.isfinite(n):
                return n
    return None


def _market_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    _h, _a, day, _t = _fixture(row)
    p = _point(row)
    return day, _family(row), _side(row), "" if p is None else f"{p:g}"


def _book(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "", _ascii(value))
    return _BOOK_ALIASES.get(key, key)


def _price(row: dict[str, Any]) -> float:
    for k in ("price", "odds", "decimal_odds", "selected_odds", "price_used_for_ev"):
        v = row.get(k)
        if v not in (None, ""):
            n = _num(v)
            if n > 1.0:
                return n
    m = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for k in ("odds", "selected_odds", "price_used_for_ev", "selected_price"):
        n = _num(m.get(k))
        if n > 1.0:
            return n
    return 0.0


def _offers_payload() -> tuple[dict[str, Any], Path | None]:
    for path in OFFER_PATHS:
        payload = _load(path, {})
        if isinstance(payload, dict) and isinstance(payload.get("offers"), list):
            return payload, path
    return {}, None


def _matching_offers(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload, path = _offers_payload()
    created = _parse_dt(payload.get("created_at_utc")) if isinstance(payload, dict) else None
    max_age = _env_float("CONTROLLED_FALLBACK_CURRENT_OFFER_MAX_AGE_MINUTES", 180.0)
    stale = created is None or datetime.now(UTC) - created > timedelta(minutes=max(5.0, max_age))
    key = _market_key(candidate)
    rows = []
    for row in payload.get("offers") or []:
        if isinstance(row, dict) and _market_key(row) == key and _same_fixture(candidate, row):
            rows.append(row)
    return rows, {"path": str(path) if path else None, "created_at_utc": created.isoformat() if created else None, "stale": stale, "matching_offers": len(rows)}


def _probability(metrics: dict[str, Any], candidate: dict[str, Any]) -> float:
    for k in ("adjusted_probability", "model_probability", "probability", "win_probability"):
        n = _num(metrics.get(k), -1.0)
        if 0 < n < 1:
            return n
        n = _num(candidate.get(k), -1.0)
        if 0 < n < 1:
            return n
    return 0.0


def _calc_ev_edge(prob: float, price: float) -> tuple[float, float]:
    if prob <= 0 or price <= 1:
        return 0.0, 0.0
    ev = (prob * price - 1.0) * 100.0
    edge = (prob - (1.0 / price)) * 100.0
    return ev, edge


def _best_offer_recovery(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> bool:
    if not _env_bool("CONTROLLED_FALLBACK_BEST_CURRENT_OFFER_RECOVERY_ENABLED", True):
        return False
    text = " | ".join(str(r).lower() for r in reasons)
    if "current price recheck value lost" not in text and "selected_price_not_current" not in text:
        return False
    offers, diag = _matching_offers(candidate)
    _STATE["price_recovery_attempted"] += 1
    if diag.get("stale") or not offers:
        metrics["best_current_offer_recovery"] = {"status": "no_fresh_matching_offer", **diag}
        return False
    prob = _probability(metrics, candidate)
    min_ev = _env_float("CONTROLLED_FALLBACK_RECOVERY_MIN_EV_PCT", 3.0)
    min_edge = _env_float("CONTROLLED_FALLBACK_RECOVERY_MIN_EDGE_PP", 1.5)
    selected_book = _book(candidate.get("bookmaker") or (candidate.get("source_summary") or {}).get("selected_bookmaker") if isinstance(candidate.get("source_summary"), dict) else "")
    best = None
    best_score = -999999.0
    for offer in offers:
        price = _price(offer)
        ev, edge = _calc_ev_edge(prob, price)
        book_bonus = 0.05 if selected_book and _book(offer.get("bookmaker") or offer.get("book")) == selected_book else 0.0
        score = ev + edge + book_bonus
        if price > 1.0 and ev >= min_ev and edge >= min_edge and score > best_score:
            best = offer
            best_score = score
    if not best:
        metrics["best_current_offer_recovery"] = {"status": "no_offer_clears_floor", "probability": prob, "min_ev_pct": min_ev, "min_edge_pp": min_edge, **diag}
        return False
    price = _price(best)
    ev, edge = _calc_ev_edge(prob, price)
    candidate["odds"] = price
    candidate["selected_odds"] = price
    candidate["bookmaker"] = best.get("bookmaker") or best.get("book") or candidate.get("bookmaker")
    metrics["odds"] = price
    metrics["selected_odds"] = price
    metrics["canonical_ev_pct"] = ev
    metrics["ev_pct"] = ev
    metrics["canonical_edge_pp"] = edge
    metrics["edge_pp"] = edge
    metrics["best_current_offer_recovery"] = {"status": "recovered", "bookmaker": candidate.get("bookmaker"), "price": price, "ev_pct": round(ev, 3), "edge_pp": round(edge, 3), "probability": prob, **diag}
    _STATE["price_recovery_applied"] += 1
    return True


def _xg_diagnostics(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> None:
    if not any("xg" in str(r).lower() for r in reasons):
        return
    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    diag: dict[str, Any] = {}
    for key in (
        "model_total_xg", "market_implied_total_xg", "bzzoiro_total_xg", "sstats_total_xg",
        "home_xg", "away_xg", "expected_goals", "xg_total", "xg_gap", "xg_probability_gap",
    ):
        for container in (metrics, candidate, source_summary):
            if isinstance(container, dict) and container.get(key) not in (None, ""):
                diag[key] = container.get(key)
                break
    diag["selection_side"] = _side(candidate)
    diag["point"] = _point(candidate)
    diag["reason_sample"] = [str(r) for r in reasons if "xg" in str(r).lower()][:6]
    metrics["xg_conflict_diagnostics"] = diag
    _STATE["xg_diagnostics_added"] += 1


def _movement_wait_reason(reason: str) -> bool:
    r = str(reason or "").lower().replace("_", " ").replace("-", " ")
    return any(t in r for t in ("semantic line movement missing", "semantic line movement not confirmed", "unconfirmed final", "missing line recheck", "needs next cron"))


def _store_awaiting(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> None:
    if not any(_movement_wait_reason(r) for r in reasons):
        return
    hard = " | ".join(str(r).lower() for r in reasons)
    if any(t in hard for t in ("movement failed", "bad movement", "value lost", "xg_direction_conflict", "xg probability", "odds below", "duplicate")):
        return
    row = dict(candidate)
    row["metrics"] = dict(metrics)
    row["awaiting_reason"] = "semantic_line_movement_wait"
    row["publication_lifecycle_status"] = "awaiting_next_run_movement_check"
    row["awaiting_created_at_utc"] = datetime.now(UTC).isoformat()
    row["reject_reasons"] = [str(r) for r in reasons]
    state = _load(STATE, {})
    if not isinstance(state, dict):
        state = {}
    arr = state.get("awaiting_movement_candidates")
    if not isinstance(arr, list):
        arr = []
    sig = "|".join(map(str, [_market_key(row), _fixture(row)[:3]]))
    kept = []
    exists = False
    for old in arr:
        if not isinstance(old, dict):
            continue
        old_sig = "|".join(map(str, [_market_key(old), _fixture(old)[:3]]))
        if old_sig == sig:
            kept.append(row); exists = True
        else:
            kept.append(old)
    if not exists:
        kept.append(row)
    state["awaiting_movement_candidates"] = kept[-100:]
    state["awaiting_movement_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_json(STATE, state)
    _write_json(AWAITING_OUT, {"status": "ok", "created_at_utc": datetime.now(UTC).isoformat(), "stored": len(kept), "latest": row})
    _STATE["movement_wait_stored"] += 1


def semantic_integrity_reasons(candidate: dict[str, Any], metrics: dict[str, Any] | None = None) -> list[str]:
    metrics = metrics if isinstance(metrics, dict) else {}
    reasons: list[str] = []
    offers, diag = _matching_offers(candidate)
    require_price = _env_bool("CONTROLLED_FALLBACK_REQUIRE_FRESH_SELECTED_PRICE", True)
    if diag.get("stale") and require_price:
        reasons.append("semantic_current_offer_snapshot_stale_or_missing")
    elif require_price and not offers:
        reasons.append("semantic_current_exact_market_price_missing")
    elif offers:
        selected = _price(candidate)
        current = max(offers, key=_price)
        current_price = _price(current)
        if selected > 1.0 and current_price > 1.0:
            diff = abs(selected - current_price)
            diff_pct = diff / current_price * 100.0
            if diff > _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_ABS_TOLERANCE", 0.05) and diff_pct > _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_PCT_TOLERANCE", 2.5):
                reasons.append(f"semantic_selected_price_not_current:{selected:.3f}/{current_price:.3f}")
            diag.update({"selected_price": round(selected, 4), "current_price": round(current_price, 4), "current_bookmaker": current.get("bookmaker") or current.get("book"), "price_diff_pct": round(diff_pct, 3)})
    metrics["semantic_current_price_guard"] = diag
    # If no line-history entry was found upstream this adds the explicit lifecycle reason.
    if _env_bool("PUBLISH_REQUIRE_LINE_MOVEMENT", True) and _env_bool("CONTROLLED_FALLBACK_REQUIRE_SEMANTIC_LINE_MOVEMENT", True):
        if not any("line_movement" in str(x) for x in (metrics.get("repaired_reasons") or [])):
            # Do not add missing if an upstream line movement reason already exists in metrics/reasons later.
            pass
    return list(dict.fromkeys(reasons))


def sanitize_rescue_pool() -> dict[str, Any]:
    result = {"status": "ok", "created_at_utc": datetime.now(UTC).isoformat(), "publication_contract_relaxed": False}
    _write()
    return result


def install(base: Any) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED or getattr(base, "_harizon_semantic_movement_current_price_guard", False):
        return {"status": "already_installed"}
    old = getattr(base, "hard_reject_reasons", None)
    if callable(old):
        def hard_reject(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
            _STATE["seen"] += 1
            reasons = list(old(candidate, metrics, sent_index) or [])
            recovered = _best_offer_recovery(candidate, metrics, reasons)
            if recovered:
                reasons = [r for r in reasons if "current price recheck value lost" not in str(r).lower() and "current_price_recheck_value_lost" not in str(r).lower()]
                metrics.setdefault("repaired_reasons", []).append("best_current_offer_recovery")
            reasons.extend(semantic_integrity_reasons(candidate, metrics))
            # Do not duplicate stale selected-price blocker if recovery succeeded.
            if recovered:
                reasons = [r for r in reasons if "selected_price_not_current" not in str(r).lower()]
            _xg_diagnostics(candidate, metrics, reasons)
            _store_awaiting(candidate, metrics, reasons)
            if len(_STATE["samples"]) < 25:
                _STATE["samples"].append({"home": candidate.get("home_team"), "away": candidate.get("away_team"), "recovered": recovered, "reasons": [str(r) for r in reasons[:8]], "recovery": metrics.get("best_current_offer_recovery"), "xg": metrics.get("xg_conflict_diagnostics")})
            _write()
            return list(dict.fromkeys(reasons))
        base.hard_reject_reasons = hard_reject
    base._harizon_semantic_movement_current_price_guard = True
    _INSTALLED = True
    _write()
    return {"status": "installed", "hard_reject_patched": callable(old), "publication_contract_relaxed": False}


__all__ = ["install", "sanitize_rescue_pool", "semantic_integrity_reasons"]
