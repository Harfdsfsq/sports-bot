from __future__ import annotations

"""Build a compact fresh-vs-cumulative B-cover diagnostic report.

Cumulative inventory can say that a match has bookmaker/context coverage, while
current publish-time odds buckets may be absent or stale.  This report separates
those layers so Telegram/Run-ID audits can explain why B-cover did not become a
candidate.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
PROMO = EXPORT / "latest-b-cover-value-promotion.json"
TRUTH = EXPORT / "latest-day-inventory-coverage-truth.json"
OUT = EXPORT / "latest-fresh-b-cover-diagnostics.json"
UTC = timezone.utc


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "matches", "items", "inventory", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def main() -> int:
    created = datetime.now(UTC).isoformat()
    promo = _load_json(PROMO, {})
    truth_rows = _rows(_load_json(TRUTH, {}))
    reason_counts = Counter()
    if isinstance(promo, dict):
        reason_counts.update({str(k): int(v) for k, v in (promo.get("reason_counts") or {}).items() if isinstance(v, (int, float))})

    considered = int(_num(promo.get("considered_b_cover_rows") if isinstance(promo, dict) else 0))
    promoted = int(_num(promo.get("promoted_count") if isinstance(promo, dict) else 0))
    accepted_offer_rows = int(_num(((promo.get("offer_diagnostics") or {}) if isinstance(promo, dict) else {}).get("accepted_offer_rows")))
    selected_b_cover = int(_num((((promo.get("inventory_load") or {}) if isinstance(promo, dict) else {}).get("selected_b_cover_rows"))))

    # Estimate current offer usefulness from promotion skip reasons.  This is not
    # a replacement for raw odds parsing; it is a stable compact diagnostic that
    # uses the already-computed promotion report.
    no_bucket = int(reason_counts.get("promotion_skip_no_offer_bucket", 0))
    stale_or_unusable = int(reason_counts.get("promotion_skip_odds_above_max", 0) + reason_counts.get("promotion_skip_odds_below_min", 0) + reason_counts.get("promotion_skip_price_outlier", 0))
    weak_value = int(reason_counts.get("promotion_skip_edge_below_min", 0) + reason_counts.get("promotion_skip_ev_below_min", 0))
    duplicates = int(reason_counts.get("promotion_skip_duplicate_candidate", 0))

    payload = {
        "status": "ok",
        "created_at_utc": created,
        "promotion_report_path": str(PROMO),
        "coverage_truth_path": str(TRUTH),
        "inventory_rows": len(truth_rows),
        "cumulative_b_cover_rows": selected_b_cover or considered,
        "promotion_considered": considered,
        "promotion_promoted": promoted,
        "accepted_offer_rows_scanned": accepted_offer_rows,
        "fresh_gap_summary": {
            "no_offer_bucket": no_bucket,
            "price_or_odds_unusable": stale_or_unusable,
            "weak_value": weak_value,
            "duplicate_candidate": duplicates,
            "usable_promoted": promoted,
        },
        "reason_counts": dict(reason_counts),
        "interpretation": [
            "cumulative_b_cover_rows is inventory coverage; it is not the same as fresh publishable odds coverage.",
            "no_offer_bucket means the B-covered match did not have a current usable market bucket matched at promotion time.",
            "price_or_odds_unusable combines odds above/below max and price-outlier skips.",
        ],
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "cumulative_b_cover_rows": payload["cumulative_b_cover_rows"], "promoted": promoted, "no_offer_bucket": no_bucket}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
