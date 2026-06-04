from __future__ import annotations

"""Daily report wrapper that reads durable HARIZON ledgers.

The legacy daily report only counted archived .logs/state rows. Runtime artifact
commits can leave those empty while Telegram fallback picks were really sent.
This wrapper patches the legacy report to read .data/bets/*.jsonl and the run
ledger created by sync_publication_ledger.py.
"""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts" / "build_daily_ops_report.py"
BET_DIR = ROOT / ".data" / "bets"


def _load_original() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_original_daily_ops_report", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ORIGINAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


def _key(row: dict[str, Any]) -> str:
    import hashlib
    raw = str(row.get("dedupe_key") or row.get("prediction_id") or row.get("fingerprint") or "")
    if not raw:
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _patch(module: Any) -> None:
    original_tracked_bets = module.tracked_bets
    original_collect_runs = module.collect_runs

    def tracked_bets_with_ledger() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend([dict(x) for x in original_tracked_bets() if isinstance(x, dict)])
        except Exception:
            pass
        for path in (
            BET_DIR / "published_bets.jsonl",
            BET_DIR / "settled_bets.jsonl",
            Path(".data/exports/published-picks-ledger.json"),
            Path(".data/exports/controlled-fallback-published-ledger.json"),
            Path(".data/exports/published-bets-ledger.json"),
        ):
            payload = _json(path, None) if path.suffix == ".json" else _jsonl(path)
            if isinstance(payload, list):
                rows.extend([dict(x) for x in payload if isinstance(x, dict)])
        pending = _json(BET_DIR / "pending_bets.json", [])
        if isinstance(pending, list):
            rows.extend([dict(x) for x in pending if isinstance(x, dict)])
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out[_key(row)] = row
        return list(out.values())

    def collect_runs_with_ledger(report_date: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend([dict(x) for x in original_collect_runs(report_date) if isinstance(x, dict)])
        except Exception:
            pass
        tz = module.app_tz()
        for item in _jsonl(BET_DIR / "run_report_ledger.jsonl"):
            created = item.get("created_at_utc") or item.get("created_at")
            try:
                if module.local_date(created, tz) != report_date:
                    continue
            except Exception:
                continue
            rows.append({"created_at": created, "summary": dict(item.get("summary") or {}), "_archive_path": "ledger:run_report_ledger"})
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    module.tracked_bets = tracked_bets_with_ledger
    module.collect_runs = collect_runs_with_ledger


def main() -> int:
    module = _load_original()
    _patch(module)
    return int(module.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
