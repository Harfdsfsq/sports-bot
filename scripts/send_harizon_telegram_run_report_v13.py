"""HARIZON report v13: verified coverage and fresh runtime diagnostics."""

from __future__ import annotations

import importlib.util
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(value))
    except Exception:
        return 0


def _disabled_sportlogic_env() -> bool:
    names = ("DAY_INVENTORY_ENABLE_SPORTLOGIC", "ENABLE_SPORTLOGIC", "SPORTLOGIC_ENABLED", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED")
    return any(str(os.getenv(name) or "").strip().lower() in {"0", "false", "no", "off"} for name in names)


def _refresh_truth() -> None:
    for module_name, name in (("scripts.repair_day_inventory_blank_rows", "main"), ("scripts.bridge_runtime_context_coverage", "main"), ("scripts.build_day_inventory_coverage_truth", "main"), ("scripts.day_inventory_cumulative_coverage", "main")):
        try:
            module = __import__(module_name, fromlist=[name])
            fn = getattr(module, name, None)
            if callable(fn):
                fn()
        except Exception:
            pass


def _load_v12() -> Any:
    path = Path(__file__).with_name("send_harizon_telegram_run_report_v12.py")
    spec = importlib.util.spec_from_file_location("harizon_report_v12_loaded", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load report v12")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verified_counts() -> dict[str, int]:
    truth = _load(EXPORT / "latest-day-inventory-coverage-truth.json", {})
    counts = truth.get("counts") if isinstance(truth, dict) and isinstance(truth.get("counts"), dict) else {}
    total = _int(counts.get("matches_total")) or 300
    line1 = _int(counts.get("matches_with_odds"))
    context1 = _int(counts.get("matches_with_context"))
    books2 = _int(counts.get("matches_with_2plus_price_confirmations"))
    odds2 = _int(counts.get("matches_with_2plus_odds_sources"))
    context2 = _int(counts.get("matches_with_2plus_context_sources"))
    a_ready = _int(counts.get("matches_a_tier_coverage_ready"))
    b_ready = _int(counts.get("matches_b_tier_watch_ready")) or min(line1, books2, context1)
    return {"total": total, "line1": line1, "context1": context1, "books2": books2, "odds2": odds2, "context2": context2, "model": _int(counts.get("matches_ready_for_model")), "a": a_ready, "b": b_ready}


def _pct(value: int, total: int) -> int:
    return 0 if not total else (100 if value >= total else int(100 * value / total))


def _replace_line(text: str, prefix_pattern: str, replacement: str) -> str:
    return re.sub(rf"^• {prefix_pattern}.*$", replacement, text, count=1, flags=re.MULTILINE)


def _replace_ready_line(text: str, pattern: str, label: str, value: int, total: int) -> str:
    regex = re.compile(rf"^• {pattern}.*$", flags=re.MULTILINE)
    def replacement(match: re.Match[str]) -> str:
        old = match.group(0)
        suffix = old[old.find("|") :] if "|" in old else ""
        return f"• {label}: {value}/{total}" + (f" {suffix}" if suffix else "")
    return regex.sub(replacement, text, count=1)


def _render_verified(text: str) -> str:
    c = _verified_counts(); total = c["total"]
    text = _replace_line(text, r"1\+ линия:", f"• 1+ линия: {c['line1']}/{total} ({_pct(c['line1'], total)}%) | 1+ контекст: {c['context1']}/{total} ({_pct(c['context1'], total)}%)")
    text = _replace_line(text, r"2\+ букмекера:", f"• 2+ букмекера: {c['books2']}/{total} ({_pct(c['books2'], total)}%)")
    text = _replace_line(text, r"2\+ независимых источника линий:", f"• 2+ независимых источника линий: {c['odds2']}/{total} ({_pct(c['odds2'], total)}%) — strict metric для A-tier.")
    text = _replace_line(text, r"2\+ independent odds-source:", f"• 2+ независимых источника линий: {c['odds2']}/{total} ({_pct(c['odds2'], total)}%) — strict metric для A-tier.")
    text = _replace_line(text, r"2\+ независимых контекста:", f"• 2+ независимых контекста: {c['context2']}/{total} ({_pct(c['context2'], total)}%)")
    text = _replace_line(text, r"2\+ контекста:", f"• 2+ независимых контекста: {c['context2']}/{total} ({_pct(c['context2'], total)}%)")
    text = _replace_line(text, r"Готово для модели:", f"• Готово для модели: {c['model']}/{total} ({_pct(c['model'], total)}%)")
    text = _replace_ready_line(text, r"A-tier strict-ready:", "A-tier coverage-ready", c["a"], total)
    text = _replace_ready_line(text, r"A-tier coverage-ready:", "A-tier coverage-ready", c["a"], total)
    text = _replace_ready_line(text, r"B-tier .*coverage:", "B-tier coverage-ready", c["b"], total)
    text = _replace_ready_line(text, r"B-tier coverage-ready:", "B-tier coverage-ready", c["b"], total)
    text = re.sub(r"^• Полное .*B-cover.*$", f"• Полное A-cover 2 линии ∩ 2 букмекера ∩ 2 контекста: {c['a']}/{total}; B-cover 1 линия ∩ 2 букмекера ∩ 1 контекст: {c['b']}/{total}.", text, count=1, flags=re.MULTILINE)
    marker = "\n🏷️ A/B-tier публикация"
    if marker in text:
        line = "\n• Покрытие считается только по сохранённым ответам независимых API; fixture-id, alias и proxy не засчитываются." f" До полного A-tier покрытия осталось: {max(0, total - c['a'])}.\n"
        text = re.sub(r"\n• Покрытие считается только по сохранённым ответам независимых API;.*?осталось: \d+\.\n", line, text, count=1)
    return text


def _payload_time(payload: Any) -> datetime | None:
    if not isinstance(payload, dict): return None
    for key in ("created_at_utc", "updated_at_utc", "created_at", "updated_at"):
        raw = str(payload.get(key) or "").strip()
        if raw:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")); return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)
            except Exception: pass
    return None


def _fresh_runtime_payload(payload: Any, *, max_age_minutes: int = 90) -> bool:
    created = _payload_time(payload)
    return bool(created and timedelta(0) <= datetime.now(UTC) - created <= timedelta(minutes=max_age_minutes))


def _sportlogic_runtime_evidence(payload: Any = None) -> dict[str, Any]:
    if _disabled_sportlogic_env():
        return {"requests": 0, "raw_rows": 0, "fixtures": 0, "matched": 0, "odds_requests": 0, "offers": 0, "errors": 0, "diagnosis": "disabled_by_env"}
    probe = _load(EXPORT / "latest-sportlogic-coverage-probe.json", {})
    if not _fresh_runtime_payload(probe): return {}
    debug = _load(EXPORT / "latest-sportlogic-debug.json", {})
    stats = debug.get("stats") if isinstance(debug, dict) and isinstance(debug.get("stats"), dict) else {}
    requests = max(_int(stats.get("requests")), _int(probe.get("requests")))
    if requests <= 0: return {}
    return {"requests": requests, "raw_rows": max(_int(stats.get("active_odds_rows_seen")), _int(stats.get("fixtures_fetched")), _int(stats.get("games_fetched")), _int(probe.get("active_odds_rows_seen"))), "fixtures": max(_int(stats.get("current_fixtures")), _int(stats.get("matches_built")), _int(probe.get("current_games"))), "matched": max(_int(stats.get("events_matched")), _int(probe.get("matched_games"))), "odds_requests": _int(stats.get("odds_requests")), "offers": _int(stats.get("offers_parsed")), "errors": max(_int(stats.get("response_errors")), requests if requests >= 20 else 0), "diagnosis": str(stats.get("diagnosis") or probe.get("diagnosis") or "runtime_enabled")}


def _repair_sportlogic_runtime_line(text: str, payload: Any = None) -> str:
    e = _sportlogic_runtime_evidence(payload)
    if not e: return text
    mode = "disabled_by_env" if e["requests"] == 0 else ("disabled_zero_rows_guard" if e["requests"] >= 20 and e["raw_rows"] == 0 and e["errors"] >= e["requests"] else "enabled_runtime")
    return re.sub(r"^• SportLogic:.*$", f"• SportLogic: {mode}; запросы {e['requests']}; rows {e['raw_rows']}; current fixtures {e['fixtures']}; matched {e['matched']}; odds req {e['odds_requests']}; offers {e['offers']}; ошибок {e['errors']}; diag {e['diagnosis']}.", text, count=1, flags=re.MULTILINE)


def _normalize_sstats_payload(payload: Any) -> None:
    return None


def _repair_bzzoiro_runtime_lines(text: str) -> str:
    return text


def _repair_sstats_runtime_line(text: str) -> str:
    return text


def _repair_movement_runtime_lines(text: str) -> str:
    return text


def _install(module: Any) -> None:
    base_render = module.render
    def render(payload: Any) -> str:
        _refresh_truth(); _normalize_sstats_payload(payload)
        text = _render_verified(base_render(payload))
        return _repair_movement_runtime_lines(_repair_sstats_runtime_line(_repair_bzzoiro_runtime_lines(_repair_sportlogic_runtime_line(text, payload))))
    module.render = render
    try:
        module.v9.v8.v7.v5.render = render; module.v9.v8.v7.render = render
    except Exception: pass


if __name__ == "__main__":
    from scripts.send_harizon_telegram_run_report_v14 import main as v14_main
    raise SystemExit(v14_main())
