from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


class CoverageAuditService:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)

    def build(self, *, debug_path: str) -> dict[str, Any]:
        payload = json.loads(Path(debug_path).read_text(encoding="utf-8"))
        summary = dict(payload.get("summary") or {})
        rejections = dict(summary.get("rejections") or {})
        forecast_rows = payload.get("forecast_rows") or []
        top_rejections = [
            {"reason": key, "count": int(value or 0)}
            for key, value in sorted(rejections.items(), key=lambda item: int(item[1] or 0), reverse=True)
            if int(value or 0) > 0
        ]
        family_counter = Counter()
        for row in forecast_rows:
            family_counter[str(row.get("family") or "unknown")] += 1
        report = {
            "created_at": payload.get("created_at"),
            "debug_path": str(debug_path),
            "matches_seen": int(summary.get("matches_seen") or 0),
            "matches_with_offers": int(summary.get("matches_with_offers") or 0),
            "contexts_built": int(summary.get("contexts_built") or 0),
            "candidates_before_quality": int(summary.get("candidates_before_quality") or 0),
            "candidates_raw": int(summary.get("candidates_raw") or 0),
            "candidates_publishable": int(summary.get("candidates_publishable") or 0),
            "published": int(summary.get("published") or summary.get("published_to_telegram") or 0),
            "offer_coverage_pct": self._pct(summary.get("matches_with_offers"), summary.get("matches_seen")),
            "context_coverage_pct": self._pct(summary.get("contexts_built"), summary.get("matches_seen")),
            "publishable_rate_pct": self._pct(summary.get("candidates_publishable"), summary.get("matches_seen")),
            "forecast_rows": len(forecast_rows),
            "forecast_rows_by_family": dict(sorted(family_counter.items())),
            "top_rejections": top_rejections[:15],
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _pct(numerator: Any, denominator: Any) -> float:
        try:
            num = float(numerator or 0.0)
            den = float(denominator or 0.0)
        except Exception:
            return 0.0
        if den <= 0:
            return 0.0
        return round(num * 100.0 / den, 2)
