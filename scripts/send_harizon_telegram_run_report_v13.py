from __future__ import annotations

import importlib.util
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
OUT_TXT = EXPORT / "latest-harizon-telegram-run-report.txt"
OUT_JSON = EXPORT / "latest-harizon-telegram-run-report.json"


def _load(path: Path, default: Any = None) -> Any:
    """Read report evidence without treating missing/invalid JSON as evidence."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _repair_sportlogic_runtime_line(text: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    current = (payload.get("api") or {}).get("sportlogic") or {}
    probe = _load(EXPORT / "latest-sportlogic-coverage-probe.json", {})
    debug = _load(EXPORT / "latest-sportlogic-debug.json", {})
    stamp = probe.get("created_at_utc")
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        fresh = at.tzinfo is not None and timedelta(0) <= datetime.now(UTC) - at <= timedelta(hours=2)
    except (ValueError, TypeError):
        fresh = False
    if current and not current.get("stale_sample"):
        stats = current
        rows = stats.get("fixtures", 0)
        fixtures = rows
    elif fresh:
        stats = {**(debug.get("stats") or {}), **probe}
        rows = stats.get("active_odds_rows_seen", stats.get("fixtures_fetched", 0))
        fixtures = stats.get("current_games", 0)
    else:
        return text
    if not stats.get("requests"):
        return text
    line = (
        f"• SportLogic: enabled_runtime; запросы {stats.get('requests', 0)}; "
        f"rows {rows}; current fixtures {fixtures}; "
        f"matched {stats.get('matched', stats.get('matched_games', 0))}; "
        f"diag {stats.get('diagnosis', '')}."
    )
    return re.sub(r"^• SportLogic:.*$", lambda _: line, text, flags=re.MULTILINE)


def _load_v5() -> Any:
    path = Path(__file__).with_name("send_harizon_telegram_run_report_v5.py")
    spec = importlib.util.spec_from_file_location("harizon_report_v5_loaded", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load report v5")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refresh_truth() -> None:
    for module_name in (
        "scripts.repair_day_inventory_blank_rows",
        "scripts.bridge_runtime_context_coverage",
        "scripts.build_day_inventory_coverage_truth",
        "scripts.harizon_a_tier_coverage_plan",
        "scripts.harizon_a_tier_targeted_enrichment_queue",
        "scripts.apply_a_tier_targeted_provider_env",
        "scripts.persist_bzzoiro_runtime_artifacts",
        "scripts.bzzoiro_targeted_odds_confirmation",
        "scripts.trace_bzzoiro_report_source",
        "scripts.project_sstats_context_into_candidates",
        "scripts.day_inventory_cumulative_coverage",
        "scripts.harizon_learning_report",
        "scripts.harizon_line_movement_diagnostics",
    ):
        try:
            module = __import__(module_name, fromlist=["main"])
            fn = getattr(module, "main", None)
            if callable(fn):
                fn()
        except SystemExit:
            pass
        except Exception:
            pass


def _write(payload: dict[str, Any], text: str) -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    payload["text_length"] = len(text)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_TXT.write_text(text + "\n", encoding="utf-8")
    (EXPORT / "latest-harizon-telegram-run-report-v5.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (EXPORT / "latest-harizon-telegram-run-report-v5.txt").write_text(text + "\n", encoding="utf-8")


def main() -> int:
    _refresh_truth()
    v5 = _load_v5()
    payload = v5.build_payload()
    text = v5.render(payload)
    try:
        from scripts.harizon_report_runtime_repairs import patch

        payload, text = patch(payload, text)
    except Exception:
        pass
    text = _repair_sportlogic_runtime_line(text, payload)
    payload["telegram_sent"] = False
    _write(payload, text)
    if os.getenv("TELEGRAM_CHAT_ID") and (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")):
        payload["telegram_sent"] = bool(v5.send_telegram(text))
        _write(payload, text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
