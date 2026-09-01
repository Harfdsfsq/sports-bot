from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

LEDGERS = [Path(".data/prediction-quality-ledger.jsonl"), Path(".data/prediction-ledger.jsonl")]
OUT = Path(".data/exports/latest-harizon-learning-report.json")


def _rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in LEDGERS:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict): rows.append(row)
                except Exception: pass
        except Exception: pass
    return rows


def _num(v: Any) -> float:
    try: return float(str(v).replace(",", "."))
    except Exception: return 0.0


def main() -> int:
    rows = _rows()
    published = [r for r in rows if str(r.get("event") or "published").lower() in {"published", "pick", "bet"}]
    settled = [r for r in published if r.get("bet_result") not in (None, "")]
    profit = sum(_num(r.get("profit")) for r in settled)
    stake = sum(abs(_num(r.get("stake") or 5.0)) for r in settled) or 0.0
    by_tier = Counter(str(r.get("tier") or "unknown") for r in published)
    by_market = Counter(str(r.get("market") or "unknown") for r in published)
    quality_scores = [_num(r.get("reserve_quality_score")) for r in published if _num(r.get("reserve_quality_score")) > 0]
    payload = {"status": "ok", "published_count": len(published), "settled_count": len(settled), "roi_pct": round(profit / stake * 100.0, 2) if stake else None, "profit": round(profit, 2), "by_tier": dict(by_tier), "by_market": dict(by_market), "avg_reserve_quality": round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None, "next_required": "fill closing_odds/result_score/bet_result/profit for settled picks to enable CLV/ROI tuning"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
