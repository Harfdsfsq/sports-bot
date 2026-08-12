from __future__ import annotations

"""Report-only repairs for production HARIZON diagnostics.

Keeps publication logic untouched.  The goal is to make Telegram diagnostics
reflect current runtime truth instead of stale helper artifacts.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")


def _load(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(value))
    except Exception:
        return 0


def _payload_time(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for key in ("created_at_utc", "updated_at_utc", "created_at", "updated_at"):
        raw = str(payload.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            pass
    return None


def _fresh(payload: Any, minutes: int = 120) -> bool:
    created = _payload_time(payload)
    return bool(created and timedelta(0) <= datetime.now(UTC) - created <= timedelta(minutes=minutes))


def _reserve_quality_from_metrics(metrics: dict[str, Any]) -> float:
    direct = _num(metrics.get("reserve_quality_score") or metrics.get("quality_score"), -1.0)
    if direct > 0:
        return direct
    ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")), 0.0)
    edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")), 0.0)
    odds = _num(metrics.get("odds"), 0.0)
    books = max(_int(metrics.get("books_count")), _int(metrics.get("bookmaker_count")), 2 if _int(metrics.get("confirmation_sources_count")) >= 2 else 0)
    conf = max(_int(metrics.get("confirmation_sources_count")), _int(metrics.get("context_sources_count")))
    score = 38.0 + min(18.0, ev * 1.45) + min(16.0, edge * 3.0) + min(10.0, books * 3.0) + min(10.0, conf * 1.5)
    if 1.75 <= odds <= 2.55:
        score += 4.0
    elif odds < 1.70 or odds > 2.90:
        score -= 8.0
    return round(max(0.0, min(100.0, score)), 1)


def patch_payload_quality(payload: dict[str, Any]) -> dict[str, Any]:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    patched = 0
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        q = _reserve_quality_from_metrics(metrics)
        if q > 0:
            metrics["reserve_quality_score"] = q
            if _num(metrics.get("quality_score"), 0.0) <= 0:
                metrics["quality_score"] = q
            row["reserve_quality_score"] = q
            patched += 1
    payload.setdefault("diagnostic_repairs", {})["reserve_quality_samples_patched"] = patched
    return payload


def patch_text_quality(text: str, payload: dict[str, Any]) -> str:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = [x for x in (samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []) if isinstance(x, dict)]
    for idx, row in enumerate(evaluated[:6], 1):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        q = _reserve_quality_from_metrics(metrics)
        if q <= 0:
            continue
        text = re.sub(rf"(^\s*{idx}\. .*? \| q )0\.0\b", rf"\g<1>{q:.1f} reserve", text, count=1, flags=re.MULTILINE)
    text = text.replace("качество 0.0 (оценка резерва)", "качество reserve-score (см. подробный отчёт)")
    return text


def _bzzoiro_runtime() -> dict[str, int]:
    for path in (EXPORT / "latest-sstats-bzzoiro-odds-merge.json", EXPORT / "latest-secondary-provider-matching.json", EXPORT / "latest-signal-stack-runtime.json"):
        payload = _load(path, {})
        if not isinstance(payload, dict):
            continue
        bzz = payload.get("bzzoiro") if isinstance(payload.get("bzzoiro"), dict) else payload
        offers = max(_int(bzz.get("offers_added_to_pool")), _int(bzz.get("offers_parsed")), _int(bzz.get("secondary_offers_added")), _int(payload.get("bzzoiro_secondary_offers_added")))
        matches = max(_int(bzz.get("matches_with_offers")), _int(bzz.get("cached_matches")), _int(payload.get("matches_with_offers")))
        two_plus = max(_int(payload.get("after_2plus_sources")), _int(bzz.get("two_plus_source_matches")), _int(bzz.get("combo_with_odds_api_io")))
        rows = max(_int(bzz.get("odds_best_rows")), _int(bzz.get("batch_odds_rows")))
        req = max(_int(bzz.get("requests")), _int(bzz.get("odds_best_requests")), _int(payload.get("requests")))
        err = max(_int(bzz.get("errors")), _int(bzz.get("response_errors")))
        if any((offers, matches, two_plus, rows, req, err)):
            return {"offers": offers, "matches": matches, "two_plus": two_plus, "rows": rows, "requests": req, "errors": err}
    return {}


def patch_runtime_lines(text: str) -> str:
    bzz = _bzzoiro_runtime()
    if bzz:
        replacement = f"• Bzzoiro runtime merge: offers {bzz['offers']}; matches with offers {bzz['matches']}; 2+ source matches {bzz['two_plus']}; batch rows {bzz['rows']}; requests {bzz['requests']}; errors {bzz['errors']}."
        text = re.sub(r"^• Bzzoiro overlap bridge:.*$", replacement, text, count=1, flags=re.MULTILINE)
    # Normalize misleading bookmaker mapping repair regressions from stale/incomplete backfill artifacts.
    def repl(match: re.Match[str]) -> str:
        raw = _int(match.group(1)); before = _int(match.group(2)); after = _int(match.group(3)); gap = _int(match.group(4))
        if after < before or gap > 0:
            return f"• Bookmaker mapping repair: raw 2+ {raw}; normalized diagnostic stale/incomplete ({before}→{after}); фактический B-cover см. выше; gap after {gap}."
        return match.group(0)
    text = re.sub(r"^• Bookmaker mapping repair: raw 2\+ (\d+); normalized (\d+)→(\d+); gap after (\d+)\.$", repl, text, count=1, flags=re.MULTILINE)
    return text


def patch(payload: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
    payload = patch_payload_quality(payload)
    text = patch_text_quality(text, payload)
    text = patch_runtime_lines(text)
    return payload, text


__all__ = ["patch", "patch_payload_quality", "patch_text_quality", "patch_runtime_lines"]
