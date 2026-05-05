from __future__ import annotations

"""Runtime safety gate for scripts/publish_controlled_fallback.py.

The candidate lifecycle gate decides *which* candidate may be published.  The
legacy controlled fallback publisher can still re-rank the pool and choose a
better-looking but non-lifecycle-approved candidate.  This module is loaded by
scripts/sitecustomize.py only inside the publish_controlled_fallback.py process.
It filters runtime candidate pools to the lifecycle-selected candidate and adds
hard price-source guards before any Telegram request can leave the process.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import parse as url_parse
from urllib import request as url_request

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".data" / "exports" / "latest-controlled-fallback-prepublish-guard.json"
TARGET_SCRIPT = "publish_controlled_fallback.py"

_ORIGINAL_READ_TEXT = Path.read_text
_ORIGINAL_URL_OPEN = url_request.urlopen


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    return _as_float(os.getenv(name), default)


def _env_int(name: str, default: int) -> int:
    return _as_int(os.getenv(name), default)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _norm_key(value: Any) -> str:
    return _norm(value).replace(" ", "")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(_ORIGINAL_READ_TEXT(path, encoding="utf-8"))
    except Exception:
        return default


def _write_audit(payload: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_selected() -> dict[str, Any]:
    report_path = Path(os.getenv("CANDIDATE_LIFECYCLE_REPORT_PATH") or ".data/exports/latest-candidate-lifecycle-report.json")
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report = _load_json(report_path, {})
    decision = report.get("decision") if isinstance(report, dict) else {}
    if not isinstance(decision, dict):
        decision = {}
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else None
    if selected is None and isinstance(report, dict) and isinstance(report.get("selected"), dict):
        selected = report.get("selected")
    selected = dict(selected or {})

    env_key = os.getenv("CANDIDATE_LIFECYCLE_SELECTED_KEY")
    env_match_key = os.getenv("CANDIDATE_LIFECYCLE_SELECTED_MATCH_KEY")
    if env_key:
        selected.setdefault("key", env_key)
    if env_match_key:
        selected.setdefault("match_key", env_match_key)
    return selected


def _selected_metric(selected: dict[str, Any], name: str) -> Any:
    if name in selected:
        return selected.get(name)
    metrics = selected.get("last_metrics")
    if isinstance(metrics, dict):
        return metrics.get(name)
    return None


def _point_equal(left: Any, right: Any) -> bool:
    if left in (None, "") and right in (None, ""):
        return True
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except Exception:
        return _norm(left) == _norm(right)


def _selection_side(selection: Any) -> str:
    text = _norm(selection)
    if any(token in text for token in ("over", "больше", "тб")):
        return "over"
    if any(token in text for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in text for token in ("yes", "да")):
        return "yes"
    if any(token in text for token in ("no", "нет")):
        return "no"
    return _norm_key(selection)


def _candidate_matches_selected(candidate: dict[str, Any], selected: dict[str, Any]) -> bool:
    selected_match_key = _norm(selected.get("match_key"))
    if selected_match_key and _norm(candidate.get("match_key")) != selected_match_key:
        return False

    selected_family = _norm(_selected_metric(selected, "family"))
    if selected_family and _norm(candidate.get("family")) != selected_family:
        return False

    selected_point = _selected_metric(selected, "point")
    if selected_point not in (None, "") and not _point_equal(candidate.get("point"), selected_point):
        return False

    selected_selection = _selected_metric(selected, "selection")
    if selected_selection not in (None, ""):
        cand_selection = candidate.get("selection") or candidate.get("selection_key")
        if _selection_side(cand_selection) != _selection_side(selected_selection):
            if _norm_key(cand_selection) != _norm_key(selected_selection):
                return False

    selected_odds = _as_float(_selected_metric(selected, "odds") or selected.get("last_odds"), 0.0)
    cand_odds = _as_float(candidate.get("odds"), 0.0)
    if selected_odds > 1.0 and cand_odds > 1.0 and abs(selected_odds - cand_odds) > 0.035:
        return False

    return bool(selected_match_key or selected_family or selected_selection)


def _values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [part for part in re.split(r"[,+;/|]+", value) if part.strip()]
    return []


def _unique_count_from_fields(candidate: dict[str, Any], fields: tuple[str, ...]) -> int:
    seen: set[str] = set()
    containers = [candidate]
    for key in ("source_summary", "market_summary", "price_summary", "diagnostics"):
        value = candidate.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for field in fields:
            value = container.get(field)
            for item in _values(value):
                text = _norm(item)
                if text:
                    seen.add(text)
    return len(seen)


def _odds_sources_count(candidate: dict[str, Any]) -> int:
    fields = (
        "odds_sources",
        "odds_source_names",
        "price_sources",
        "price_source_names",
        "bookmaker_sources",
        "selected_odds_sources",
    )
    count = _unique_count_from_fields(candidate, fields)
    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    for field in (
        "odds_sources_count",
        "price_sources_count",
        "source_count",
        "sources_count",
        "independent_odds_sources_count",
    ):
        count = max(count, _as_int(candidate.get(field)), _as_int(source_summary.get(field)))
    selected_source = source_summary.get("selected_source") or source_summary.get("source") or candidate.get("source")
    if selected_source:
        count = max(count, 1)
    return count


def _bookmakers_count(candidate: dict[str, Any]) -> int:
    fields = (
        "bookmakers",
        "bookmaker_names",
        "books",
        "book_names",
        "selected_bookmakers",
        "exact_line_bookmakers",
    )
    count = _unique_count_from_fields(candidate, fields)
    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    for field in ("books_count", "bookmakers_count", "bookmaker_count", "exact_line_bookmakers_count"):
        count = max(count, _as_int(candidate.get(field)), _as_int(source_summary.get(field)))
    selected_bookmaker = source_summary.get("selected_bookmaker") or source_summary.get("bookmaker") or candidate.get("bookmaker")
    if selected_bookmaker:
        count = max(count, 1)
    return count


def _is_over_15(candidate: dict[str, Any]) -> bool:
    family = _norm(candidate.get("family"))
    if family != "totals":
        return False
    if abs(_as_float(candidate.get("point"), -999.0) - 1.5) > 1e-9:
        return False
    return _selection_side(candidate.get("selection") or candidate.get("selection_key")) == "over"


def _price_guard(candidate: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    odds = _as_float(candidate.get("odds"), 0.0)
    odds_sources = _odds_sources_count(candidate)
    books = _bookmakers_count(candidate)
    min_odds_sources = max(1, _env_int("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES", 2))
    details = {
        "odds": odds,
        "odds_sources_count": odds_sources,
        "bookmakers_count": books,
        "family": candidate.get("family"),
        "selection": candidate.get("selection"),
        "point": candidate.get("point"),
    }

    if _env_bool("CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM", True) and odds_sources < min_odds_sources:
        return False, f"price_odds_sources_below_min:{odds_sources}/{min_odds_sources}", details

    if _is_over_15(candidate):
        max_reasonable = _env_float("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS", 1.65)
        min_over15_books = max(3, _env_int("MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS", 3))
        if odds > max_reasonable and max(books, odds_sources) < min_over15_books:
            return False, f"suspicious_total_over_1_5_price:{odds}>{max_reasonable};confirmations={max(books, odds_sources)}/{min_over15_books}", details

    return True, "ok", details


def _is_candidate_like(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    keys = {"match_key", "home_team", "away_team", "family", "selection", "odds"}
    return len(keys.intersection(row.keys())) >= 3


def _filter_payload(obj: Any, selected: dict[str, Any], audit: dict[str, Any]) -> Any:
    if isinstance(obj, list):
        if obj and all(isinstance(item, dict) for item in obj) and any(_is_candidate_like(item) for item in obj):
            kept: list[Any] = []
            rejected = 0
            rejected_reasons: dict[str, int] = {}
            for item in obj:
                if not _is_candidate_like(item):
                    rejected += 1
                    rejected_reasons["not_candidate_like"] = rejected_reasons.get("not_candidate_like", 0) + 1
                    continue
                if not _candidate_matches_selected(item, selected):
                    rejected += 1
                    rejected_reasons["not_lifecycle_selected"] = rejected_reasons.get("not_lifecycle_selected", 0) + 1
                    continue
                ok, reason, details = _price_guard(item)
                if not ok:
                    rejected += 1
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                    audit["last_rejected_selected_details"] = details
                    continue
                kept.append(item)
            audit.setdefault("filtered_candidate_lists", []).append(
                {"before": len(obj), "after": len(kept), "rejected": rejected, "reasons": rejected_reasons}
            )
            return kept
        return [_filter_payload(item, selected, audit) for item in obj]
    if isinstance(obj, dict):
        return {key: _filter_payload(value, selected, audit) for key, value in obj.items()}
    return obj


def _should_filter_path(path: Path) -> bool:
    raw = str(path).replace("\\", "/")
    if raw.endswith("latest-candidate-lifecycle-report.json"):
        return False
    needles = (
        ".logs/debug-last-run.json",
        "latest-near-miss-enrichment-queue.json",
        "latest-run-summary.json",
        "latest-profit-watchlist.json",
        "latest-profit-publishable-watchlist.json",
        "latest-controlled-fallback-report.json",
        "run-bot/",
    )
    return any(needle in raw for needle in needles)


def _enhance_stake_percent(text: str) -> str:
    if "% банка" in text:
        return text
    bank_match = re.search(r"💼\s*Банк:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    stake_match = re.search(r"(💰\s*Сумма ставки:\s*)([0-9]+(?:[.,][0-9]+)?)(\s*\()", text)
    if not bank_match or not stake_match:
        return text
    bank = _as_float(bank_match.group(1).replace(",", "."), 0.0)
    stake = _as_float(stake_match.group(2).replace(",", "."), 0.0)
    if bank <= 0 or stake <= 0:
        return text
    pct = stake / bank * 100.0
    replacement = f"{stake_match.group(1)}{stake_match.group(2)} ({pct:.2f}% банка, "
    return text[: stake_match.start()] + replacement + text[stake_match.end() :]


def _patch_telegram_request(req: Any, allowed: bool, reason: str) -> Any:
    url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
    if "api.telegram.org" not in str(url) or "sendMessage" not in str(url):
        return req
    if not allowed:
        raise RuntimeError(f"controlled fallback telegram send blocked by prepublish guard: {reason}")

    data = getattr(req, "data", None)
    if not data:
        return req
    try:
        raw = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        parsed = url_parse.parse_qs(raw, keep_blank_values=True)
        texts = parsed.get("text")
        if not texts:
            return req
        new_text = _enhance_stake_percent(texts[0])
        if new_text == texts[0]:
            return req
        parsed["text"] = [new_text]
        encoded = url_parse.urlencode(parsed, doseq=True).encode("utf-8")
        try:
            req.data = encoded
            req.headers["Content-length"] = str(len(encoded))
        except Exception:
            pass
    except Exception:
        return req
    return req


def install() -> None:
    if Path(sys.argv[0] or "").name != TARGET_SCRIPT:
        return

    selected = _load_selected()
    allow_env = _env_bool("CANDIDATE_LIFECYCLE_ALLOW_PUBLISH", False)
    selected_match_key = selected.get("match_key") or os.getenv("CANDIDATE_LIFECYCLE_SELECTED_MATCH_KEY")
    initial_allowed = bool(allow_env and selected_match_key)
    initial_reason = "ok" if initial_allowed else "missing_lifecycle_selected_candidate"

    audit: dict[str, Any] = {
        "active": True,
        "allowed_initially": initial_allowed,
        "initial_reason": initial_reason,
        "selected": selected,
        "filtered_candidate_lists": [],
        "blocked_telegram_sends": 0,
        "telegram_stake_percent_patch": True,
    }

    if not initial_allowed:
        os.environ["CONTROLLED_FALLBACK_ENABLED"] = "false"
        os.environ["CONTROLLED_FALLBACK_SEND_TELEGRAM"] = "false"
        os.environ["CONTROLLED_FALLBACK_DRY_RUN"] = "true"

    def guarded_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        text = _ORIGINAL_READ_TEXT(self, *args, **kwargs)
        if not _should_filter_path(self):
            return text
        try:
            payload = json.loads(text)
        except Exception:
            return text
        transformed = _filter_payload(payload, selected, audit)
        if transformed != payload:
            audit.setdefault("filtered_paths", []).append(str(self))
            return json.dumps(transformed, ensure_ascii=False)
        return text

    def guarded_urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
        allowed_now = initial_allowed
        reason_now = initial_reason
        try:
            patched = _patch_telegram_request(req, allowed_now, reason_now)
            return _ORIGINAL_URL_OPEN(patched, *args, **kwargs)
        except RuntimeError:
            audit["blocked_telegram_sends"] = int(audit.get("blocked_telegram_sends") or 0) + 1
            audit["final_allowed"] = False
            audit["final_reason"] = reason_now
            _write_audit(audit)
            raise

    Path.read_text = guarded_read_text
    url_request.urlopen = guarded_urlopen
    os.environ["CONTROLLED_FALLBACK_PREPUBLISH_GUARD_ACTIVE"] = "true"
    _write_audit(audit)
    try:
        print(f"controlled fallback prepublish guard active: selected_match_key={selected_match_key or 'none'}")
    except Exception:
        pass
