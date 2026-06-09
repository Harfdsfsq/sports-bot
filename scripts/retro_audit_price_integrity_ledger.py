from __future__ import annotations

"""Retro-audit published/pending HARIZON bets for price-integrity mistakes.

This script does not delete historical bets and does not change Telegram history.
It marks ledger rows that should not be used for model training/ROI learning when
newer price guards would have blocked them.  The main failure mode is a bookmaker
quorum built from an already-corrupted bucket, e.g. Under 2.5 @2.75 while the
real same-side market was around 1.40.

The script is intentionally conservative.  It flags rows when either:
* the current price-integrity guard can reject the row using available raw/snapshot evidence;
* the row matches the high-risk pattern that previously leaked: totals main line,
  high selected odds, only bookmaker_quorum as line source, <=1 independent odds
  source/context confirmation, and no strong external price verification.
"""

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
BET_DIR = Path(".data/bets")
EXPORT_DIR = Path(".data/exports")
PUBLISHED_JSONL = BET_DIR / "published_bets.jsonl"
PENDING_JSON = BET_DIR / "pending_bets.json"
REPORT_PATH = EXPORT_DIR / "latest-ledger-retro-price-integrity-audit.json"

TRUTHY = {"1", "true", "yes", "on", "force"}


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in TRUTHY


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        x = float(str(value).replace(",", "."))
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _norm(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9.]+", " ", str(value or "").strip().lower()).strip()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                out.append(item)
    except Exception:
        pass
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _selected_odds(row: dict[str, Any]) -> float:
    for key in ("selected_odds", "odds", "price", "selected_price"):
        value = _as_float(row.get(key), None)
        if value is not None and value > 1.0:
            return float(value)
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    value = _as_float(payload.get("odds"), None)
    return float(value or 0.0)


def _family(row: dict[str, Any]) -> str:
    return str(row.get("family") or row.get("market_family") or (row.get("bet_payload") or {}).get("family") or "").lower()


def _point(row: dict[str, Any]) -> float | None:
    for key in ("point", "line", "handicap"):
        value = _as_float(row.get(key), None)
        if value is not None:
            return round(float(value), 3)
    text = " ".join(str(row.get(key) or "") for key in ("selection", "selection_key"))
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    text += " " + str(payload.get("selection") or "")
    m = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", text)
    return _as_float(m.group(1), None) if m else None


def _selection_text(row: dict[str, Any]) -> str:
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    return str(row.get("selection") or payload.get("selection") or "")


def _side(row: dict[str, Any]) -> str:
    text = _norm(_selection_text(row))
    if any(x in text for x in ("under", "меньше", "тм")):
        return "under"
    if any(x in text for x in ("over", "больше", "тб")):
        return "over"
    return ""


def _line_sources(row: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("line_sources", "odds_sources", "price_sources"):
        v = row.get(key)
        if isinstance(v, list):
            values.extend(v)
        elif isinstance(v, str):
            values.extend(re.split(r"[,|;]", v))
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in ("line_sources", "odds_sources", "price_sources"):
        v = metrics.get(key)
        if isinstance(v, list):
            values.extend(v)
        elif isinstance(v, str):
            values.extend(re.split(r"[,|;]", v))
    return {_norm(x) for x in values if str(x or "").strip()}


def _confirmation_count(row: dict[str, Any]) -> int:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for src in (row, metrics):
        for key in ("confirmation_sources_count", "sources_count", "context_sources_count"):
            v = _as_int(src.get(key), -1)
            if v >= 0:
                return v
        vals = src.get("confirmation_sources")
        if isinstance(vals, list):
            return len(vals)
    return 0


def _odds_sources_count(row: dict[str, Any]) -> int:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for src in (row, metrics):
        for key in ("independent_odds_sources_count", "odds_sources_count"):
            v = _as_int(src.get(key), -1)
            if v >= 0:
                return v
    return 0


def _has_external_snapshot_verification(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for src in (row, metrics):
        for key in ("external_snapshot_price_guard_mode", "external_snapshot_median_same_side_price"):
            if src.get(key) not in (None, ""):
                return True
    return False


def _semantic_key(row: dict[str, Any]) -> str:
    raw = str(row.get("ledger_semantic_key_raw") or "")
    if raw:
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
    parts = [
        row.get("match_key"), row.get("home_team"), row.get("away_team"), row.get("kickoff") or row.get("commence_time"),
        _family(row), _selection_text(row), _point(row),
    ]
    return hashlib.sha1("|".join(_norm(x) for x in parts).encode("utf-8")).hexdigest()


def _normalize_for_guard(row: dict[str, Any]) -> dict[str, Any]:
    # Flatten ledger rows enough for filter_controlled_fallback_price_integrity.
    out = dict(row)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    payload = row.get("bet_payload") if isinstance(row.get("bet_payload"), dict) else {}
    for key in ("family", "market_family", "selection", "point", "odds", "price", "selected_odds", "market_probability"):
        if key not in out and key in metrics:
            out[key] = metrics[key]
        if key not in out and key in payload:
            out[key] = payload[key]
    if "selected_odds" not in out:
        out["selected_odds"] = _selected_odds(row)
    if "selection" not in out:
        out["selection"] = _selection_text(row)
    return out


def _current_guard_reasons(row: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    try:
        from scripts.filter_controlled_fallback_price_integrity import candidate_reject_reasons
        return candidate_reject_reasons(_normalize_for_guard(row))
    except Exception as exc:
        return [], {"guard_import_error": str(exc)[:200]}


def _heuristic_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if _family(row) not in {"totals", "teamtotals"}:
        return reasons
    selected = _selected_odds(row)
    point = _point(row)
    side = _side(row)
    line_sources = _line_sources(row)
    odds_sources = _odds_sources_count(row)
    confirmations = _confirmation_count(row)
    bookmaker_quorum_only = bool(line_sources) and line_sources <= {"bookmaker quorum", "bookmaker_quorum"}
    if (
        point is not None and abs(float(point) - 2.5) <= 1e-6
        and side in {"under", "over"}
        and selected >= float(os.getenv("RETRO_PRICE_AUDIT_HIGH_MAIN_TOTAL_ODDS", "2.35"))
        and (bookmaker_quorum_only or odds_sources <= 1)
        and confirmations <= int(float(os.getenv("RETRO_PRICE_AUDIT_MAX_CONFIRMATIONS", "1")))
        and not _has_external_snapshot_verification(row)
    ):
        reasons.append("retro_price_integrity:high_main_total_price_without_external_snapshot_confirmation")
    # Known leaked pattern: bookmaker_quorum-only B-tier with selected price built from internal median.
    if selected >= 2.35 and bookmaker_quorum_only and odds_sources <= 1:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        q = metrics.get("tier_b_bookmaker_quorum") if isinstance(metrics.get("tier_b_bookmaker_quorum"), dict) else {}
        if q.get("median_price") not in (None, "") and not _has_external_snapshot_verification(row):
            reasons.append("retro_price_integrity:bookmaker_quorum_only_internal_median_not_training_safe")
    return reasons


def _audit_row(row: dict[str, Any], *, mutate: bool) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
    updated = dict(row)
    guard_reasons, guard_details = _current_guard_reasons(row)
    heuristic = _heuristic_reasons(row)
    reasons = sorted(set(guard_reasons + heuristic))
    if not reasons:
        return updated, False, None
    now = datetime.now(UTC).isoformat()
    if mutate:
        updated["price_integrity_retro_flagged"] = True
        updated["excluded_from_training"] = True
        updated["training_exclusion_reason"] = "; ".join(reasons)
        updated["retro_price_integrity_reasons"] = reasons
        updated["retro_price_integrity_audited_at_utc"] = now
        updated.setdefault("model_learning_status", "excluded_from_training")
        # Preserve betting settlement state; this is a model/training exclusion, not a deletion.
    return updated, True, {
        "semantic_key": _semantic_key(row),
        "match": f"{row.get('home_team') or ''} — {row.get('away_team') or ''}",
        "selection": _selection_text(row),
        "point": _point(row),
        "odds": _selected_odds(row),
        "reasons": reasons,
        "guard_details": guard_details,
    }


def _audit_rows(rows: list[dict[str, Any]], *, mutate: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    changed = 0
    for row in rows:
        new_row, is_flagged, report = _audit_row(row, mutate=mutate)
        out.append(new_row)
        if is_flagged and report:
            flagged.append(report)
            if new_row != row:
                changed += 1
    return out, flagged, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-mutate", action="store_true")
    # When called from runtime atexit hooks, sys.argv belongs to app.cli and may
    # contain unrelated arguments.  Parse an empty argv by default; the __main__
    # block passes real CLI args explicitly.
    args = parser.parse_args([] if argv is None else argv)
    mutate = not args.no_mutate and _truthy(os.getenv("LEDGER_RETRO_PRICE_AUDIT_MUTATE", "true"), True)
    BET_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    published = _read_jsonl(PUBLISHED_JSONL)
    pending = _read_json(PENDING_JSON, [])
    if not isinstance(pending, list):
        pending = []

    published_new, published_flagged, pub_changed = _audit_rows(published, mutate=mutate)
    pending_new, pending_flagged, pend_changed = _audit_rows([x for x in pending if isinstance(x, dict)], mutate=mutate)

    if mutate:
        _write_jsonl(PUBLISHED_JSONL, published_new)
        _write_json(PENDING_JSON, pending_new)
        _write_json(EXPORT_DIR / "latest-pending-bets.json", pending_new)
        _write_json(EXPORT_DIR / "latest-picks.json", published_new)

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "mutated": mutate,
        "policy": "retro_flag_price_integrity_leaks_exclude_from_training_not_delete",
        "published_rows": len(published),
        "pending_rows": len(pending_new),
        "published_flagged": len(published_flagged),
        "pending_flagged": len(pending_flagged),
        "changed_rows": pub_changed + pend_changed,
        "flagged": (published_flagged + pending_flagged)[:50],
    }
    _write_json(REPORT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
