from __future__ import annotations

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
LINE_HISTORY_PATHS = (
    ROOT / ".data" / "line_history" / "latest.json",
    ROOT / "artifacts" / "run-bot" / "line_history" / "latest.json",
)
RESCUE_PATH = ROOT / ".data" / "exports" / "latest-rescue-candidates.json"
OUT = ROOT / ".data" / "exports" / "latest-semantic-movement-current-price-guard.json"

_CACHE: dict[str, tuple[int, Any]] = {}
_OFFER_INDEX_CACHE: tuple[
    str, dict[tuple[str, str, str, str], list[dict[str, Any]]]
] | None = None
_LINE_INDEX_CACHE: tuple[
    str, dict[tuple[str, str, str, str], list[dict[str, Any]]]
] | None = None
_INSTALLED = False
_ORIGINAL_HARD_REJECT = None
_ORIGINAL_CANONICAL_PUBLICATION_KEY = None
_ORIGINAL_SELECT_TOP_PICKS = None

_STOP = {
    "fc",
    "cf",
    "fk",
    "sc",
    "afc",
    "ac",
    "club",
    "football",
    "futbol",
    "calcio",
    "msk",
    "tc",
    "sk",
    "sv",
    "nk",
    "rks",
    "pfc",
    "fkc",
    "ks",
    "as",
    "us",
    "cd",
}
_BOOK_ALIASES = {
    "betfairexchange": "betfair",
    "betfair": "betfair",
    "bet365com": "bet365",
    "bet365": "bet365",
    "unibetuk": "unibet",
    "unibet": "unibet",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
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


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        temporary = OUT.with_suffix(OUT.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(OUT)
    except Exception:
        pass


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _ascii(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD", str(value or "").lower().replace("ё", "е")
    )
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9а-я]+", " ", text).split())


def _tokens(value: Any) -> list[str]:
    result: list[str] = []
    for token in _ascii(value).split():
        if token in _STOP:
            continue
        if len(token) > 7 and token.endswith("i"):
            token = token[:-1]
        result.append(token)
    return result


def _team_similarity(left: Any, right: Any) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    left_text = " ".join(left_tokens)
    right_text = " ".join(right_tokens)
    left_compact = "".join(left_tokens)
    right_compact = "".join(right_tokens)
    if left_compact == right_compact:
        return 1.0
    if min(len(left_compact), len(right_compact)) >= 4 and (
        left_compact in right_compact or right_compact in left_compact
    ):
        return 0.96
    compact_score = SequenceMatcher(None, left_compact, right_compact).ratio()
    left_scores = [
        max(SequenceMatcher(None, token, other).ratio() for other in right_tokens)
        for token in left_tokens
    ]
    right_scores = [
        max(SequenceMatcher(None, token, other).ratio() for other in left_tokens)
        for token in right_tokens
    ]
    token_score = (
        sum(left_scores) / len(left_scores) + sum(right_scores) / len(right_scores)
    ) / 2.0
    return max(
        compact_score,
        token_score,
        SequenceMatcher(None, left_text, right_text).ratio(),
    )


def _date(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is not None:
        return parsed.date().isoformat()
    match = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _key_teams(raw: Any) -> tuple[str, str, str]:
    parts = [part for part in str(raw or "").split("|") if part]
    if len(parts) >= 4 and parts[0].lower() == "soccer":
        return parts[1].replace("_", " "), parts[2].replace("_", " "), parts[3]
    if len(parts) >= 3 and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", parts[0]):
        return parts[1].replace("_", " "), parts[2].replace("_", " "), parts[0]
    return "", "", _date(raw)


def _fixture(row: dict[str, Any]) -> tuple[str, str, str, datetime | None]:
    raw = row.get("match_key") or row.get("canonical_match_id") or row.get("event_key")
    key_home, key_away, key_date = _key_teams(raw)
    home = str(
        row.get("home_team") or row.get("home") or row.get("home_name") or key_home
    )
    away = str(
        row.get("away_team") or row.get("away") or row.get("away_name") or key_away
    )
    kickoff = _parse_dt(
        row.get("commence_time")
        or row.get("kickoff_utc")
        or row.get("kickoff")
        or row.get("start_time")
    )
    day = kickoff.date().isoformat() if kickoff is not None else (_date(raw) or key_date)
    return home, away, day, kickoff


def _same_fixture(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_home, left_away, left_day, left_time = _fixture(left)
    right_home, right_away, right_day, right_time = _fixture(right)
    if left_day and right_day and left_day != right_day:
        return False
    if (
        left_time is not None
        and right_time is not None
        and abs((left_time - right_time).total_seconds()) > 3 * 3600
    ):
        return False
    same_order = (
        _team_similarity(left_home, right_home),
        _team_similarity(left_away, right_away),
    )
    reversed_order = (
        _team_similarity(left_home, right_away),
        _team_similarity(left_away, right_home),
    )
    best = max((same_order, reversed_order), key=lambda pair: pair[0] + pair[1])
    return min(best) >= 0.72 and sum(best) / 2.0 >= 0.82


def _family(row: dict[str, Any]) -> str:
    text = _ascii(row.get("family") or row.get("market_family") or row.get("market"))
    compact = text.replace(" ", "")
    if (
        "teamtotal" in compact
        or "individualtotal" in compact
        or "индивидуальн" in text
        or ("team" in text.split() and "total" in text.split())
    ):
        return "team_totals"
    if "total" in text or "тотал" in text:
        return "totals"
    if "spread" in text or "handicap" in text or "фора" in text:
        return "spreads"
    return text


def _side(row: dict[str, Any]) -> str:
    text = _ascii(
        " ".join(
            str(row.get(key) or "")
            for key in ("selection_key", "selection", "side", "name", "outcome")
        )
    )
    if any(token in text.split() for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in text.split() for token in ("over", "больше", "тб")):
        return "over"
    return text


def _point(row: dict[str, Any]) -> float | None:
    for key in ("point", "line", "total", "handicap"):
        try:
            value = row.get(key)
            if value not in (None, ""):
                number = float(str(value).replace(",", "."))
                return number if math.isfinite(number) else None
        except Exception:
            continue
    return None


def _same_market(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _family(left) != _family(right) or _side(left) != _side(right):
        return False
    left_point = _point(left)
    right_point = _point(right)
    return (
        left_point is not None
        and right_point is not None
        and abs(left_point - right_point) <= 1e-6
    )


def _book(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "", _ascii(value))
    return _BOOK_ALIASES.get(key, key)


def _price(row: dict[str, Any]) -> float:
    for key in ("price", "odds", "decimal_odds", "selected_odds", "price_used_for_ev"):
        try:
            value = row.get(key)
            if value not in (None, ""):
                price = float(str(value).replace(",", "."))
                if price > 1.0 and math.isfinite(price):
                    return price
        except Exception:
            continue
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in ("odds", "selected_odds", "price_used_for_ev", "selected_price"):
        try:
            value = metrics.get(key)
            if value not in (None, ""):
                price = float(str(value).replace(",", "."))
                if price > 1.0 and math.isfinite(price):
                    return price
        except Exception:
            continue
    return 0.0


def _offers_payload() -> tuple[dict[str, Any], Path | None]:
    for path in OFFER_PATHS:
        payload = _load(path, {})
        if isinstance(payload, dict) and isinstance(payload.get("offers"), list):
            return payload, path
    return {}, None


def _market_index_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    _home, _away, day, _kickoff = _fixture(row)
    point = _point(row)
    point_key = "" if point is None else f"{point:g}"
    return day, _family(row), _side(row), point_key


def _offer_index(
    payload: dict[str, Any], path: Path | None
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    global _OFFER_INDEX_CACHE
    stamp = f"{path}:{path.stat().st_mtime_ns if path and path.exists() else 0}"
    if _OFFER_INDEX_CACHE and _OFFER_INDEX_CACHE[0] == stamp:
        return _OFFER_INDEX_CACHE[1]
    index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in payload.get("offers") or []:
        if isinstance(row, dict):
            index.setdefault(_market_index_key(row), []).append(row)
    _OFFER_INDEX_CACHE = (stamp, index)
    return index


def _matching_offers(
    candidate: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload, path = _offers_payload()
    rows = _offer_index(payload, path).get(_market_index_key(candidate), [])
    matches = [row for row in rows if _same_fixture(candidate, row)]
    created = _parse_dt(payload.get("created_at_utc")) if isinstance(payload, dict) else None
    max_age = max(
        5.0,
        _env_float("CONTROLLED_FALLBACK_CURRENT_OFFER_MAX_AGE_MINUTES", 120.0),
    )
    stale = created is None or datetime.now(UTC) - created > timedelta(minutes=max_age)
    return matches, {
        "path": str(path) if path else None,
        "created_at_utc": created.isoformat() if created else None,
        "stale": stale,
        "matching_offers": len(matches),
    }


def _line_index() -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    global _LINE_INDEX_CACHE
    for path in LINE_HISTORY_PATHS:
        payload = _load(path, {})
        lines = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines, dict):
            continue
        stamp = f"{path}:{path.stat().st_mtime_ns if path.exists() else 0}"
        if _LINE_INDEX_CACHE and _LINE_INDEX_CACHE[0] == stamp:
            return _LINE_INDEX_CACHE[1]
        index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for key, box in lines.items():
            if not isinstance(box, dict):
                continue
            snapshot = (
                box.get("last_snapshot")
                if isinstance(box.get("last_snapshot"), dict)
                else {}
            )
            probe = dict(snapshot)
            probe.setdefault(
                "match_key",
                str(key).split("|totals|")[0]
                if "|totals|" in str(key)
                else str(key).split("|spreads|")[0],
            )
            guard = (
                box.get("last_guard") if isinstance(box.get("last_guard"), dict) else {}
            )
            item = {
                "key": key,
                "snapshot": snapshot,
                "probe": probe,
                "guard": guard,
                "path": str(path),
            }
            index.setdefault(_market_index_key(probe), []).append(item)
        _LINE_INDEX_CACHE = (stamp, index)
        return index
    return {}


def _line_entries(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    summary = candidate.get("source_summary")
    summary = summary if isinstance(summary, dict) else {}
    selected_book = _book(
        candidate.get("bookmaker") or summary.get("selected_bookmaker")
    )
    result: list[dict[str, Any]] = []
    for item in _line_index().get(_market_index_key(candidate), []):
        probe = item["probe"]
        if not _same_fixture(candidate, probe):
            continue
        snapshot = item["snapshot"]
        book = _book(
            snapshot.get("bookmaker") or str(item["key"]).rsplit("|", 1)[-1]
        )
        if selected_book and book and book != selected_book:
            continue
        result.append({key: value for key, value in item.items() if key != "probe"})
    return result


def _requires_current_snapshot(candidate: dict[str, Any]) -> bool:
    summary = candidate.get("source_summary")
    summary = summary if isinstance(summary, dict) else {}
    text = _ascii(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("_candidate_source"),
                candidate.get("candidate_source"),
                candidate.get("source"),
                candidate.get("bookmaker"),
                summary.get("selected_source"),
                summary.get("selected_bookmaker"),
            )
        )
    )
    markers = (
        "odds api io",
        "a cover market promotion",
        "b cover market promotion",
        "betfair",
        "bet365",
        "unibet",
        "sbobet",
    )
    return any(marker in text for marker in markers)


def semantic_integrity_reasons(
    candidate: dict[str, Any], metrics: dict[str, Any] | None = None
) -> list[str]:
    metrics = metrics if isinstance(metrics, dict) else {}
    reasons: list[str] = []
    offers, offer_diagnostics = _matching_offers(candidate)
    require_price = _env_bool(
        "CONTROLLED_FALLBACK_REQUIRE_FRESH_SELECTED_PRICE", True
    ) and _requires_current_snapshot(candidate)
    if offer_diagnostics["stale"]:
        if require_price:
            reasons.append("semantic_current_offer_snapshot_stale_or_missing")
    elif offers:
        summary = candidate.get("source_summary")
        summary = summary if isinstance(summary, dict) else {}
        selected_book = _book(
            candidate.get("bookmaker") or summary.get("selected_bookmaker")
        )
        book_offers = [
            row
            for row in offers
            if not selected_book
            or _book(row.get("bookmaker") or row.get("book")) == selected_book
        ]
        selected = _price(candidate)
        if selected_book and not book_offers:
            reasons.append("semantic_selected_book_current_price_missing")
            selected = _price(candidate)
            if selected > 1.0 and offers:
                current = max(offers, key=_price)
                current_price = _price(current)
                absolute_tolerance = max(
                    0.0,
                    _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_ABS_TOLERANCE", 0.03),
                )
                percent_tolerance = max(
                    0.0,
                    _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_PCT_TOLERANCE", 1.5),
                )
                difference = abs(selected - current_price)
                difference_pct = (
                    difference / current_price * 100.0 if current_price > 0 else 999.0
                )
                if difference > absolute_tolerance and difference_pct > percent_tolerance:
                    reasons.append(
                        f"semantic_selected_price_not_current:{selected:.3f}/{current_price:.3f}"
                    )
                offer_diagnostics.update(
                    {
                        "selected_bookmaker": selected_book,
                        "selected_price": round(selected, 4),
                        "selected_book_missing_from_current_snapshot": True,
                        "current_price": round(current_price, 4),
                        "current_bookmaker": current.get("bookmaker")
                        or current.get("book"),
                        "price_diff_pct": round(difference_pct, 3),
                    }
                )
        elif book_offers and selected > 1.0:
            current = max(book_offers, key=_price)
            current_price = _price(current)
            absolute_tolerance = max(
                0.0,
                _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_ABS_TOLERANCE", 0.03),
            )
            percent_tolerance = max(
                0.0,
                _env_float("CONTROLLED_FALLBACK_CURRENT_PRICE_PCT_TOLERANCE", 1.5),
            )
            difference = abs(selected - current_price)
            difference_pct = (
                difference / current_price * 100.0 if current_price > 0 else 999.0
            )
            if difference > absolute_tolerance and difference_pct > percent_tolerance:
                reasons.append(
                    f"semantic_selected_price_not_current:{selected:.3f}/{current_price:.3f}"
                )
            offer_diagnostics.update(
                {
                    "selected_bookmaker": selected_book,
                    "selected_price": round(selected, 4),
                    "current_price": round(current_price, 4),
                    "current_bookmaker": current.get("bookmaker")
                    or current.get("book"),
                    "price_diff_pct": round(difference_pct, 3),
                }
            )
    elif require_price:
        reasons.append("semantic_current_exact_market_price_missing")

    entries = _line_entries(candidate)
    movement_diagnostics: dict[str, Any] = {
        "matching_entries": len(entries),
        "entries": [],
    }
    require_movement = _env_bool("PUBLISH_REQUIRE_LINE_MOVEMENT", True) and _env_bool(
        "CONTROLLED_FALLBACK_REQUIRE_SEMANTIC_LINE_MOVEMENT", True
    )
    if entries:
        times = [
            _parse_dt(item["snapshot"].get("captured_at_utc")) for item in entries
        ]
        times = [item for item in times if item is not None]
        newest = max(times) if times else None
        recent: list[dict[str, Any]] = []
        for item in entries:
            captured = _parse_dt(item["snapshot"].get("captured_at_utc"))
            if newest is None or captured is None or newest - captured <= timedelta(minutes=15):
                recent.append(item)
        passed = False
        failed = False
        unresolved_final = False
        for item in recent:
            guard = item["guard"]
            status = str(
                guard.get("line_movement_lifecycle_status") or ""
            ).strip().lower()
            is_passed = bool(guard.get("passed")) and status in {
                "movement_confirmed",
                "publish_now_no_next_cron",
                "confirmed",
                "passed",
            }
            is_failed = status in {"movement_failed", "failed"}
            no_more = bool(guard.get("no_more_cron_before_kickoff")) or bool(
                guard.get("final_pre_kickoff_check")
            )
            if (
                no_more
                and not bool(guard.get("passed"))
                and status in {"awaiting_next_run", "pending", ""}
            ):
                unresolved_final = True
            passed = passed or is_passed
            failed = failed or is_failed
            movement_diagnostics["entries"].append(
                {
                    "key": item["key"],
                    "captured_at_utc": item["snapshot"].get("captured_at_utc"),
                    "status": status,
                    "passed": bool(guard.get("passed")),
                    "no_more_cron_before_kickoff": bool(
                        guard.get("no_more_cron_before_kickoff")
                    ),
                    "current_odds": guard.get("current_odds"),
                    "line_move_pct": guard.get("line_move_pct"),
                    "reasons": guard.get("reasons") or [],
                }
            )
        if passed and failed:
            reasons.append("semantic_line_movement_alias_conflict")
        elif failed:
            reasons.append("semantic_line_movement_failed")
        elif any(
            str(item.get("guard", {}).get("line_movement_lifecycle_status") or "")
            .strip()
            .lower()
            == "not_publishable"
            for item in recent
        ):
            blocked_reasons: list[str] = []
            for item in recent:
                guard = item.get("guard", {}) if isinstance(item, dict) else {}
                status = str(guard.get("line_movement_lifecycle_status") or "").strip().lower()
                if status != "not_publishable":
                    continue
                for reason in guard.get("reasons") or []:
                    text = str(reason or "").strip()
                    if text:
                        blocked_reasons.append(text)
            if blocked_reasons:
                reasons.append(f"semantic_line_movement_not_publishable:{blocked_reasons[0]}")
            else:
                reasons.append("semantic_line_movement_not_publishable")
        elif unresolved_final:
            reasons.append("semantic_line_movement_unconfirmed_final")
        elif require_movement and not passed:
            reasons.append("semantic_line_movement_not_confirmed")
    elif require_movement:
        reasons.append("semantic_line_movement_missing")

    metrics["semantic_current_price_guard"] = offer_diagnostics
    metrics["semantic_line_movement_guard"] = movement_diagnostics
    return list(dict.fromkeys(reasons))


def _extract_rows(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None
    if isinstance(payload, dict):
        for key in ("candidates", "items", "rows", "selected_all"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)], key
    return [], None


def sanitize_rescue_pool() -> dict[str, Any]:
    payload = _load(RESCUE_PATH, [])
    rows, container = _extract_rows(payload)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        diagnostics: dict[str, Any] = {}
        reasons = semantic_integrity_reasons(row, diagnostics)
        semantic_reasons = [
            reason for reason in reasons if reason.startswith("semantic_")
        ]
        if semantic_reasons:
            removed.append(
                {
                    "match_key": row.get("match_key"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "family": row.get("family"),
                    "selection": row.get("selection"),
                    "point": row.get("point"),
                    "odds": row.get("odds"),
                    "reasons": semantic_reasons,
                    "diagnostics": diagnostics,
                }
            )
        else:
            kept.append(row)
    if rows:
        if container is None:
            output: Any = kept
        else:
            output = dict(payload)
            output[container] = kept
        RESCUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESCUE_PATH.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _CACHE.pop(str(RESCUE_PATH), None)
    result = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "rescue_rows_seen": len(rows),
        "rescue_rows_kept": len(kept),
        "rescue_rows_removed": len(removed),
        "removed_sample": removed[:20],
        "publication_contract_relaxed": False,
    }
    _write(result)
    return result


def _semantic_fixture_key(candidate: dict[str, Any]) -> str:
    offers, _diagnostics = _matching_offers(candidate)
    source = offers[0] if offers else candidate
    home, away, day, kickoff = _fixture(source)
    kickoff_key = kickoff.isoformat() if kickoff is not None else day
    teams = sorted((_ascii(home), _ascii(away)))
    return "|".join([kickoff_key, *teams])


def install(base: Any) -> dict[str, Any]:
    global _INSTALLED
    global _ORIGINAL_HARD_REJECT
    global _ORIGINAL_CANONICAL_PUBLICATION_KEY
    global _ORIGINAL_SELECT_TOP_PICKS
    if _INSTALLED or getattr(
        base, "_harizon_semantic_movement_current_price_guard", False
    ):
        return {"status": "already_installed"}

    sanitizer = sanitize_rescue_pool()
    current_hard = getattr(base, "hard_reject_reasons", None)
    if callable(current_hard):
        _ORIGINAL_HARD_REJECT = current_hard

        def hard_reject(
            candidate: dict[str, Any],
            metrics: dict[str, Any],
            sent_index: dict[str, Any],
        ) -> list[str]:
            reasons = list(current_hard(candidate, metrics, sent_index) or [])
            reasons.extend(semantic_integrity_reasons(candidate, metrics))
            return list(dict.fromkeys(reasons))

        base.hard_reject_reasons = hard_reject

    current_key = getattr(base, "canonical_publication_key", None)
    if callable(current_key):
        _ORIGINAL_CANONICAL_PUBLICATION_KEY = current_key

        def canonical_key(candidate: dict[str, Any]) -> str:
            original = str(current_key(candidate) or "")
            parts = original.split("|")
            suffix = "|".join(parts[-4:]) if len(parts) >= 4 else original
            return f"{_semantic_fixture_key(candidate)}|{suffix}"

        base.canonical_publication_key = canonical_key

    current_select = getattr(base, "select_top_picks", None)
    if callable(current_select):
        _ORIGINAL_SELECT_TOP_PICKS = current_select

        def select_top_picks(viable: Any, bankroll: dict[str, Any]):
            selected = list(current_select(viable, bankroll) or [])
            seen: set[str] = set()
            result = []
            for item in selected:
                candidate = item[0] if isinstance(item, tuple) and item else {}
                key = (
                    _semantic_fixture_key(candidate)
                    if isinstance(candidate, dict)
                    else str(candidate)
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
            return result

        base.select_top_picks = select_top_picks

    base._harizon_semantic_movement_current_price_guard = True
    _INSTALLED = True
    result = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "hard_reject_patched": callable(current_hard),
        "canonical_publication_key_patched": callable(current_key),
        "selected_bundle_semantic_dedupe_patched": callable(current_select),
        "sanitizer": sanitizer,
        "publication_contract_relaxed": False,
    }
    _write(result)
    return result


__all__ = ["install", "sanitize_rescue_pool", "semantic_integrity_reasons"]
