from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.daily_sla import build_daily_sla_report, load_json_file, thresholds_from_env


def _candidate_paths() -> list[Path]:
    explicit = os.getenv("DAY_SLA_INVENTORY_PATH")
    if explicit:
        return [Path(explicit)]
    return [
        Path(".data/day_inventory/latest.json"),
        Path(".data/day_inventory/current.json"),
        Path(".data/cache/day_inventory/latest.json"),
        Path(".data/cache/day_inventory/current.json"),
        Path(".data/exports/latest-day-inventory-cumulative-coverage.json"),
        Path(".data/exports/latest-matches.json"),
    ]


def _load_payload() -> tuple[Path | None, Any]:
    for path in _candidate_paths():
        if path.exists() and path.stat().st_size > 0:
            return path, load_json_file(path)
    return None, []


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report.get("summary", {})
    lines = [
        "# HARIZON daily SLA report",
        "",
        f"Inventory: {summary.get('inventory_count', 0)}/{summary.get('inventory_target', 0)}",
        f"2+ odds sources: {summary.get('odds_2plus_count', 0)} ({summary.get('odds_2plus_pct', 0)}%)",
        f"2+ context sources: {summary.get('context_2plus_count', 0)} ({summary.get('context_2plus_pct', 0)}%)",
        f"2+ books: {summary.get('books_2plus_count', 0)} ({summary.get('books_2plus_pct', 0)}%)",
        f"Line movement checked: {summary.get('line_movement_ready_count', 0)} ({summary.get('line_movement_ready_pct', 0)}%)",
        f"Coverage ready: {summary.get('coverage_ready_count', 0)} ({summary.get('coverage_ready_pct', 0)}%)",
        "",
    ]
    breaches = report.get("breaches") or []
    if breaches:
        lines.append("## Breaches")
        lines.extend(f"- {item}" for item in breaches)
    else:
        lines.append("No SLA breaches detected.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    source_path, payload = _load_payload()
    report = build_daily_sla_report(payload, thresholds=thresholds_from_env())
    report["source_path"] = str(source_path) if source_path else None

    export_dir = Path(os.getenv("STORAGE_EXPORT_DIR") or ".data/exports")
    export_dir.mkdir(parents=True, exist_ok=True)
    json_path = export_dir / "latest-day-sla-report.json"
    md_path = export_dir / "latest-day-sla-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    fail_on_breach = str(os.getenv("DAY_SLA_FAIL_ON_BREACH") or "").strip().lower() in {"1", "true", "yes", "on"}
    return 2 if fail_on_breach and report.get("breaches") else 0


if __name__ == "__main__":
    raise SystemExit(main())
