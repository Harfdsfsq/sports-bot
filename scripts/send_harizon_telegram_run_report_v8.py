from __future__ import annotations

"""HARIZON Telegram run report v8.

Extends v7 with progressive core coverage metrics. The core contract is:
- core line/odds sources: odds_api_io + bzzoiro + sstats;
- core context sources: sstats + bzzoiro.

The old v7 conclusion looked only at Bzzoiro+odds-api.io overlap. That is still
useful for live-price independence, but it no longer represents the user's full
core odds/source contract because SStats is also a core line/model source.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

V7_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v7.py")
EXPORT_DIR = Path(".data/exports")
PROGRESSIVE_PLAN = EXPORT_DIR / "latest-progressive-coverage-plan.json"


def _load_v7() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v7", V7_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V7_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v7 = _load_v7()
_base_build_payload = v7.build_payload
_base_render = v7.render


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _load_progressive_plan() -> dict[str, Any]:
    try:
        if PROGRESSIVE_PLAN.exists() and PROGRESSIVE_PLAN.stat().st_size > 0:
            payload = json.loads(PROGRESSIVE_PLAN.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    plan = _load_progressive_plan()
    payload["version"] = "harizon-telegram-report-v8-progressive-core-coverage"
    payload.setdefault("diagnostics", {})["progressive_core_coverage"] = {
        "contract": plan.get("contract") if isinstance(plan.get("contract"), dict) else {},
        "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
        "gap_sample_size": len(plan.get("core_gap_sample") or plan.get("gap_sample") or []) if isinstance(plan, dict) else 0,
    }
    return payload


def _insert_before(text: str, marker: str, block: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return text + "\n\n" + block
    return text[:idx].rstrip() + "\n\n" + block.rstrip() + "\n\n" + text[idx:].lstrip()


def _replace_conclusion(text: str, counts: dict[str, Any]) -> str:
    old = "• Главный технический bottleneck: мало матчей с 2 independent odds sources. Нужно добирать SportLogic/Bzzoiro overlap, а не ослаблять guards."
    core_ready = _as_int(counts.get("core_ready_2plus_both") or counts.get("ready_2plus_both"))
    win4 = _as_int(counts.get("window_0_4h_core_ready_2plus_both") or counts.get("window_0_4h_ready_2plus_both"))
    win12 = _as_int(counts.get("window_0_12h_core_ready_2plus_both") or counts.get("window_0_12h_ready_2plus_both"))
    win4_total = _as_int(counts.get("window_0_4h"))
    win12_total = _as_int(counts.get("window_0_12h"))
    if core_ready > 0:
        new = (
            "• Progressive coverage уже считает core-contract: odds_api_io + bzzoiro + sstats для линий, "
            "sstats + bzzoiro для контекста. Главный gap сейчас — добор Bzzoiro/SStats на ближайшие окна, "
            f"а не просто общий overlap odds-api.io+Bzzoiro. Core-ready: {core_ready}; 0–4ч: {win4}/{win4_total}; 0–12ч: {win12}/{win12_total}."
        )
    else:
        new = (
            "• Progressive coverage включён, но core-ready 2+ ещё не накоплен. Нужно добирать именно core gaps: "
            "Bzzoiro/SStats по матчам ближайшего окна; supplemental источники не должны закрывать core-дырки."
        )
    return text.replace(old, new)


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload).replace("HARIZON run report v7", "HARIZON run report v8")
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    prog = diag.get("progressive_core_coverage") if isinstance(diag.get("progressive_core_coverage"), dict) else {}
    contract = prog.get("contract") if isinstance(prog.get("contract"), dict) else {}
    counts = prog.get("counts") if isinstance(prog.get("counts"), dict) else {}

    if counts:
        core_odds = ",".join(contract.get("core_odds_providers") or ["bzzoiro", "odds_api_io", "sstats"])
        core_context = ",".join(contract.get("core_context_providers") or ["bzzoiro", "sstats"])
        block = "\n".join([
            "🧭 Progressive core coverage",
            f"• Core odds/line: {core_odds}",
            f"• Core context: {core_context}",
            f"• Tracked: {_as_int(counts.get('matches_tracked'))} | core odds 2+: {_as_int(counts.get('core_odds_2plus') or counts.get('odds_2plus'))} | core context 2+: {_as_int(counts.get('core_context_2plus') or counts.get('context_2plus'))} | core-ready both: {_as_int(counts.get('core_ready_2plus_both') or counts.get('ready_2plus_both'))}",
            f"• 0–4ч: {_as_int(counts.get('window_0_4h_core_ready_2plus_both') or counts.get('window_0_4h_ready_2plus_both'))}/{_as_int(counts.get('window_0_4h'))} core-ready",
            f"• 0–12ч: {_as_int(counts.get('window_0_12h_core_ready_2plus_both') or counts.get('window_0_12h_ready_2plus_both'))}/{_as_int(counts.get('window_0_12h'))} core-ready",
        ])
        text = _insert_before(text, "🚫 Почему не опубликовано", block)
        text = _replace_conclusion(text, counts)
    return text


v7.build_payload = build_payload
v7.render = render


if __name__ == "__main__":
    raise SystemExit(v7.v5.main())
