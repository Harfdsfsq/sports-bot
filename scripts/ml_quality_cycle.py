from __future__ import annotations

"""Regular ML/quality cycle over training dataset.

This script is deliberately dependency-light. It reads the exported training
dataset, computes calibration/ROI/CLV by stable segments, and writes actionable
runtime recommendations. It does not auto-relax guards; output is observe-first.
"""

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
DATASET_CANDIDATES = [
    ROOT / ".data" / "exports" / "latest-training-dataset.csv",
    ROOT / ".data" / "exports" / "training-dataset.csv",
    ROOT / ".data" / "exports" / "latest-training-dataset.json",
]
OUT_PATH = ROOT / ".data" / "exports" / "latest-ml-quality-cycle.json"
OUT_MD_PATH = ROOT / ".data" / "exports" / "latest-ml-quality-cycle.md"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def load_rows() -> tuple[list[dict[str, Any]], str]:
    for path in DATASET_CANDIDATES:
        if not path.exists():
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    return [dict(row) for row in payload if isinstance(row, dict)], str(path)
                if isinstance(payload, dict):
                    for key in ("rows", "data", "items"):
                        rows = payload.get(key)
                        if isinstance(rows, list):
                            return [dict(row) for row in rows if isinstance(row, dict)], str(path)
            except Exception:
                continue
        else:
            try:
                with path.open("r", encoding="utf-8", newline="") as fh:
                    return list(csv.DictReader(fh)), str(path)
            except Exception:
                continue
    return [], ""


def outcome(row: dict[str, Any]) -> tuple[bool, float, float, float]:
    pnl = as_float(row.get("pnl") or row.get("profit") or row.get("profit_loss") or row.get("settled_pnl"), 0.0)
    stake = as_float(row.get("stake") or row.get("stake_amount"), 0.0)
    odds = as_float(row.get("odds"), 0.0)
    result = str(row.get("result") or row.get("outcome") or row.get("settlement_status") or "").lower()
    if stake <= 0:
        stake = 1.0 if pnl else 0.0
    if pnl == 0.0 and stake > 0 and result:
        if any(x in result for x in ("win", "won", "выиг")):
            pnl = max(0.0, odds - 1.0) * stake if odds > 1 else stake
        elif any(x in result for x in ("loss", "lost", "проиг")):
            pnl = -stake
    settled = bool(result) or pnl != 0.0
    win = pnl > 0
    return settled, win, pnl, stake


def probability(row: dict[str, Any]) -> float:
    for key in ("adjusted_probability", "final_probability", "model_probability", "probability", "confidence_probability"):
        value = as_float(row.get(key), math.nan)
        if not math.isnan(value) and value > 0:
            return value / 100.0 if value > 1 else value
    odds = as_float(row.get("odds"), 0.0)
    return 1.0 / odds if odds > 1 else 0.0


def segment_key(row: dict[str, Any]) -> str:
    family = str(row.get("family") or row.get("market_family") or "unknown").lower()
    mode = str(row.get("model_mode") or row.get("tier") or "unknown").lower()
    sources = str(row.get("sources_count") or row.get("confirmation_sources_count") or "?")
    return f"family={family}|mode={mode}|sources={sources}"


def brier(rows: list[dict[str, Any]]) -> float | None:
    vals = []
    for row in rows:
        settled, win, _, _ = outcome(row)
        if not settled:
            continue
        p = probability(row)
        if p <= 0:
            continue
        vals.append((p - (1.0 if win else 0.0)) ** 2)
    return round(sum(vals) / len(vals), 6) if vals else None


def segment_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[segment_key(row)].append(row)
    out = []
    for key, items in groups.items():
        settled_rows = [row for row in items if outcome(row)[0]]
        stake = sum(outcome(row)[3] for row in settled_rows)
        pnl = sum(outcome(row)[2] for row in settled_rows)
        wins = sum(1 for row in settled_rows if outcome(row)[1])
        clv_values = [as_float(row.get("clv_pct") or row.get("closing_line_value_pct"), math.nan) for row in settled_rows]
        clv_values = [x for x in clv_values if not math.isnan(x)]
        out.append({
            "segment_key": key,
            "rows": len(items),
            "settled": len(settled_rows),
            "stake": round(stake, 4),
            "pnl": round(pnl, 4),
            "roi_pct": round((pnl / stake * 100.0), 3) if stake > 0 else None,
            "win_rate_pct": round((wins / len(settled_rows) * 100.0), 3) if settled_rows else None,
            "avg_probability": round(sum(probability(row) for row in items) / len(items), 4) if items else 0.0,
            "brier": brier(items),
            "avg_clv_pct": round(sum(clv_values) / len(clv_values), 3) if clv_values else None,
        })
    out.sort(key=lambda row: (-(row.get("settled") or 0), str(row.get("segment_key"))))
    return out


def recommendations(stats: list[dict[str, Any]]) -> list[str]:
    recs: list[str] = []
    mature = [row for row in stats if int(row.get("settled") or 0) >= 20]
    if not mature:
        return ["Not enough settled samples per segment yet; keep observe_only and avoid automatic threshold changes."]
    weak = [row for row in mature if (row.get("roi_pct") is not None and float(row["roi_pct"]) < -5.0) or (row.get("avg_clv_pct") is not None and float(row["avg_clv_pct"]) < -1.5)]
    strong = [row for row in mature if (row.get("roi_pct") is not None and float(row["roi_pct"]) > 3.0) and (row.get("avg_clv_pct") is None or float(row["avg_clv_pct"]) >= 0.0)]
    for row in weak[:5]:
        recs.append(f"Tighten or observe segment {row['segment_key']}: ROI {row.get('roi_pct')}%, CLV {row.get('avg_clv_pct')}%.")
    for row in strong[:5]:
        recs.append(f"Candidate segment for controlled expansion {row['segment_key']}: ROI {row.get('roi_pct')}%, CLV {row.get('avg_clv_pct')}%.")
    return recs or ["Segments are mixed; keep current guards and continue accumulating settled samples."]


def write_md(report: dict[str, Any]) -> None:
    lines = [
        "# ML / Quality Cycle",
        "",
        f"- Created UTC: `{report['created_at_utc']}`",
        f"- Dataset: `{report['dataset_path']}`",
        f"- Rows: **{report['rows']}**",
        f"- Settled rows: **{report['settled_rows']}**",
        f"- Overall Brier: `{report.get('overall_brier')}`",
        "",
        "## Recommendations",
        "",
    ]
    lines.extend([f"- {item}" for item in report.get("recommendations", [])])
    lines.extend(["", "## Top segments", "", "| Segment | Rows | Settled | ROI % | CLV % | Brier |", "|---|---:|---:|---:|---:|---:|"])
    for row in report.get("segments", [])[:20]:
        lines.append(f"| `{row['segment_key']}` | {row['rows']} | {row['settled']} | {row.get('roi_pct')} | {row.get('avg_clv_pct')} | {row.get('brier')} |")
    OUT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows, path = load_rows()
    settled_count = sum(1 for row in rows if outcome(row)[0])
    stats = segment_stats(rows)
    report = {
        "status": "ok" if rows else "no_dataset",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "dataset_path": path,
        "rows": len(rows),
        "settled_rows": settled_count,
        "overall_brier": brier(rows),
        "segments": stats,
        "recommendations": recommendations(stats),
        "mode": "observe_only",
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_md(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
