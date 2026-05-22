from __future__ import annotations

"""HARIZON Telegram run report v8.

Extends v7 with progressive core coverage metrics. The core contract is:
- core line/odds sources: odds_api_io + bzzoiro + sportlogic;
- core context sources: sstats + bzzoiro.

The report also normalizes runtime-patched SStats counters: the provider wrapper
stores real v1 numbers under source_stats.sstats.v1_stats / v1_* fields, while
older renderers only read top-level requests/contexts/rows.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

V7_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v7.py")
EXPORT_DIR = Path(".data/exports")
PROGRESSIVE_PLAN = EXPORT_DIR / "latest-progressive-coverage-plan.json"
ACTIVE_CORE_PATCH = EXPORT_DIR / "latest-progressive-active-core-budget-patch.json"
TRUTH_REPORT = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
REFRESH_PLAN = EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
PRIORITY_STATE = EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json"
V8_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v8-status.json"
CANDIDATE_FACTORY_DIAGNOSTICS = EXPORT_DIR / "latest-candidate-factory-diagnostics.json"
POST_INTEGRITY_RESCUE_REPORT = EXPORT_DIR / "latest-post-integrity-candidate-rescue.json"


def _load_v7() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v7", V7_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V7_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v7 = _load_v7()
_base_build_payload = v7.v5.build_payload
_base_render = v7.v5.render


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return 0


def _load_progressive_plan() -> dict[str, Any]:
    try:
        if PROGRESSIVE_PLAN.exists() and PROGRESSIVE_PLAN.stat().st_size > 0:
            payload = json.loads(PROGRESSIVE_PLAN.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        else:
            payload = {}
    except Exception:
        payload = {}
    try:
        active = _load_json(ACTIVE_CORE_PATCH)
        if active:
            contract = dict(payload.get("contract") or {})
            if isinstance(active.get("active_core_odds_providers"), list):
                contract["core_odds_providers"] = [str(x) for x in active.get("active_core_odds_providers") or []]
            if isinstance(active.get("active_core_context_providers"), list):
                contract["core_context_providers"] = [str(x) for x in active.get("active_core_context_providers") or []]
            if isinstance(active.get("excluded_core_providers"), list):
                contract["excluded_core_providers"] = active.get("excluded_core_providers") or []
            contract["core_providers"] = sorted(set(contract.get("core_odds_providers") or []) | set(contract.get("core_context_providers") or []))
            contract["reason"] = "active core merged from budget patch; disabled/zero-budget providers excluded"
            payload["contract"] = contract
            payload.setdefault("diagnostics", {})["active_core_budget_patch_merged_in_renderer"] = True
    except Exception:
        pass
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        V8_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V8_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _normalize_runtime_patched_sstats(payload: dict[str, Any]) -> None:
    """Expose nested SStats v1 counters at the top-level report API row."""
    try:
        data = v7.v5.artifacts()
        stats = v7.v5.source_stats(data)
    except Exception:
        return
    row = _first_dict(stats.get("sstats"))
    if not row:
        return
    v1 = _first_dict(row.get("v1_stats"))
    requests = _as_int(row.get("requests")) or _as_int(row.get("v1_requests")) or _as_int(v1.get("requests"))
    contexts = _as_int(row.get("contexts_built")) or _as_int(row.get("v1_contexts_built")) or _as_int(v1.get("contexts_built"))
    rows = _as_int(row.get("rows_fetched")) or _as_int(row.get("v1_games_list_rows_fetched")) or _as_int(v1.get("games_list_rows_fetched"))
    errors = _as_int(row.get("response_errors")) or _as_int(row.get("v1_response_errors")) or _as_int(v1.get("response_errors"))
    deep = _as_int(_first_dict(row.get("sstats_deep")).get("contexts_enriched"))
    deep = deep or _as_int(row.get("v1_last_games_stats_fetched")) or _as_int(v1.get("last_games_stats_fetched"))
    api = payload.setdefault("api", {})
    sstats_api = dict(api.get("sstats") or {})
    if requests:
        sstats_api["requests"] = requests
    if contexts or _as_int(sstats_api.get("contexts")) == 0:
        sstats_api["contexts"] = contexts
    if rows:
        sstats_api["rows"] = rows
    sstats_api["errors"] = errors
    sstats_api["deep_enriched"] = deep
    sstats_api["runtime_patch"] = row.get("runtime_patch") or ""
    sstats_api["team_form_contexts"] = _as_int(row.get("v1_team_form_contexts_built")) or _as_int(v1.get("team_form_contexts_built"))
    sstats_api["direct_contexts"] = _as_int(row.get("v1_direct_contexts_built")) or _as_int(v1.get("direct_contexts_built"))
    api["sstats"] = sstats_api



def _current_inventory_window_payload() -> dict[str, Any]:
    refresh = _load_json(REFRESH_PLAN)
    priority = _load_json(PRIORITY_STATE)
    plan = refresh if refresh else {}
    if not plan and isinstance(priority.get("refresh_plan"), dict):
        plan = priority["refresh_plan"]
    truth = _load_json(TRUTH_REPORT)
    truth_rows = truth.get("rows") if isinstance(truth.get("rows"), list) else []
    counts = {
        "active_matches": _as_int(plan.get("active_matches")),
        "final_pre_kickoff_checks": _as_int(plan.get("final_pre_kickoff_checks")),
        "no_more_regular_run_before_kickoff": _as_int(plan.get("no_more_regular_run_before_kickoff")),
        "matches_needing_odds_refresh": _as_int(plan.get("matches_needing_odds_refresh")),
        "window_0_4h": 0, "window_0_4h_ready": 0, "window_0_12h": 0, "window_0_12h_ready": 0,
        "window_0_4h_context_2plus": 0, "window_0_12h_context_2plus": 0,
        "window_0_4h_price_2plus": 0, "window_0_12h_price_2plus": 0,
        "source": "latest-day-inventory-refresh-plan + coverage-truth",
    }
    for row in truth_rows:
        if not isinstance(row, dict):
            continue
        try:
            minutes = float(row.get("minutes_to_kickoff"))
        except Exception:
            continue
        if 0 <= minutes <= 4 * 60:
            counts["window_0_4h"] += 1
            counts["window_0_4h_ready"] += int(bool(row.get("ready_for_publish")))
            counts["window_0_4h_context_2plus"] += int(_as_int(row.get("context_sources_count")) >= 2)
            counts["window_0_4h_price_2plus"] += int(_as_int(row.get("price_confirmations")) >= 2)
        if 0 <= minutes <= 12 * 60:
            counts["window_0_12h"] += 1
            counts["window_0_12h_ready"] += int(bool(row.get("ready_for_publish")))
            counts["window_0_12h_context_2plus"] += int(_as_int(row.get("context_sources_count")) >= 2)
            counts["window_0_12h_price_2plus"] += int(_as_int(row.get("price_confirmations")) >= 2)
    if counts["window_0_12h"] <= 0:
        for row in plan.get("top_priority_matches") or []:
            if not isinstance(row, dict):
                continue
            try:
                minutes = float(row.get("minutes_to_kickoff"))
            except Exception:
                continue
            if 0 <= minutes <= 4 * 60:
                counts["window_0_4h"] += 1
            if 0 <= minutes <= 12 * 60:
                counts["window_0_12h"] += 1
    return counts

def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    _normalize_runtime_patched_sstats(payload)
    plan = _load_progressive_plan()
    payload["version"] = "harizon-telegram-report-v8-progressive-core-coverage"
    payload.setdefault("diagnostics", {})["progressive_core_coverage"] = {
        "contract": plan.get("contract") if isinstance(plan.get("contract"), dict) else {},
        "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
        "gap_sample_size": len(plan.get("core_gap_sample") or plan.get("gap_sample") or []) if isinstance(plan, dict) else 0,
    }
    payload.setdefault("diagnostics", {})["current_inventory_windows"] = _current_inventory_window_payload()
    day_summary = _load_json(EXPORT_DIR / "latest-day-inventory-summary.json")
    truth_counts = day_summary.get("coverage_truth_counts") if isinstance(day_summary.get("coverage_truth_counts"), dict) else {}
    truth_report = _load_json(TRUTH_REPORT)
    if not truth_counts and isinstance(truth_report.get("counts"), dict):
        truth_counts = truth_report["counts"]
    if truth_counts:
        sources = day_summary.get("sources") if isinstance(day_summary.get("sources"), dict) else {}
        payload.setdefault("diagnostics", {})["coverage_truth"] = {
            "counts": truth_counts,
            "source": sources.get("coverage_truth") if isinstance(sources.get("coverage_truth"), dict) else {"path": str(TRUTH_REPORT)},
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
            "• Progressive coverage считает только active core-contract текущего запуска. Главный gap сейчас — "
            "добор Bzzoiro/SStats и второго odds source на ближайшие окна, а не ослабление guards. "
            f"Core-ready: {core_ready}; 0–4ч: {win4}/{win4_total}; 0–12ч: {win12}/{win12_total}."
        )
    else:
        new = (
            "• Progressive coverage включён, но core-ready 2+ ещё не накоплен. Нужно добирать именно core gaps: "
            "Bzzoiro/SStats по матчам ближайшего окна; supplemental источники не должны закрывать core-дырки."
        )
    return text.replace(old, new)



def _candidate_factory_blockers_block() -> str:
    diagnostics = _load_json(CANDIDATE_FACTORY_DIAGNOSTICS)
    if not diagnostics:
        return ""
    rejections = diagnostics.get("rejections") if isinstance(diagnostics.get("rejections"), dict) else {}
    blockers = diagnostics.get("top_likely_blockers") if isinstance(diagnostics.get("top_likely_blockers"), dict) else {}
    rescue = _load_json(POST_INTEGRITY_RESCUE_REPORT)
    rows: list[str] = ["🧩 CandidateFactory diagnostics"]
    rows.append(
        f"• matches/offers/context: {_as_int(diagnostics.get('matches_seen'))}/"
        f"{_as_int(diagnostics.get('matches_with_offers'))}/"
        f"{_as_int(diagnostics.get('matches_with_context'))} | raw: {_as_int(diagnostics.get('raw_candidates'))}"
    )
    if blockers:
        top = sorted(blockers.items(), key=lambda item: _as_int(item[1]), reverse=True)[:5]
        rows.append("• likely blockers: " + ", ".join(f"{k}: {_as_int(v)}" for k, v in top))
    if rejections:
        top_rej = sorted(rejections.items(), key=lambda item: _as_int(item[1]), reverse=True)[:7]
        rows.append("• factory rejects: " + ", ".join(f"{k}: {_as_int(v)}" for k, v in top_rej))
    if rescue:
        stage = str(rescue.get("stage") or rescue.get("status") or "n/a")
        returned = _as_int(rescue.get("returned"))
        built = _as_int(rescue.get("built_before_market_integrity"))
        rows.append(f"• post-integrity rescue: {stage} | built {built} | returned {returned}")
    rows.append("• Смысл: это причины до quality/fallback; они объясняют, почему raw pool стал нулевым.")
    return "\n".join(rows)


def _patch_sstats_line(text: str, payload: dict[str, Any]) -> str:
    api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
    sstats = api.get("sstats") if isinstance(api.get("sstats"), dict) else {}
    if not sstats:
        return text
    import re
    new_line = (
        f"• sstats: req {_as_int(sstats.get('requests'))}, ctx {_as_int(sstats.get('contexts'))}, "
        f"rows {_as_int(sstats.get('rows'))}, err {_as_int(sstats.get('errors'))}, "
        f"deep enriched {_as_int(sstats.get('deep_enriched'))}"
    )
    extra = []
    if sstats.get("runtime_patch"):
        extra.append(f"patch {sstats.get('runtime_patch')}")
    if _as_int(sstats.get("team_form_contexts")) or _as_int(sstats.get("direct_contexts")):
        extra.append(f"direct {_as_int(sstats.get('direct_contexts'))}, form {_as_int(sstats.get('team_form_contexts'))}")
    if extra:
        new_line += " | " + "; ".join(extra)
    return re.sub(r"• sstats: req \d+, ctx \d+, rows \d+, err \d+, deep enriched \d+(?: \| [^\n]*)?", new_line, text)


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload).replace("HARIZON run report v7", "HARIZON run report v8")
    text = _patch_sstats_line(text, payload)
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    prog = diag.get("progressive_core_coverage") if isinstance(diag.get("progressive_core_coverage"), dict) else {}
    contract = prog.get("contract") if isinstance(prog.get("contract"), dict) else {}
    counts = prog.get("counts") if isinstance(prog.get("counts"), dict) else {}

    if counts:
        core_odds_values = [str(x) for x in (contract.get("core_odds_providers") or ["bzzoiro", "odds_api_io"]) if str(x) != "sstats"]
        core_odds = ",".join(sorted(set(core_odds_values))) or "n/a"
        core_context = ",".join(sorted(set(str(x) for x in (contract.get("core_context_providers") or ["bzzoiro", "sstats"]))))
        excluded_rows = contract.get("excluded_core_providers") if isinstance(contract.get("excluded_core_providers"), list) else []
        excluded_values: list[str] = []
        for item in excluded_rows:
            if isinstance(item, dict):
                provider = str(item.get("provider") or "").strip()
                reason = str(item.get("reason") or "excluded").strip()
                if provider:
                    excluded_values.append(f"{provider} ({reason})")
            elif str(item).strip():
                excluded_values.append(str(item).strip())
        block_lines = [
            "🧭 Progressive core coverage",
            f"• Active core odds/line: {core_odds}",
            f"• Active core context: {core_context}",
            f"• Tracked: {_as_int(counts.get('matches_tracked'))} | core odds 2+: {_as_int(counts.get('core_odds_2plus') or counts.get('odds_2plus'))} | core context 2+: {_as_int(counts.get('core_context_2plus') or counts.get('context_2plus'))} | core-ready both: {_as_int(counts.get('core_ready_2plus_both') or counts.get('ready_2plus_both'))}",
            f"• 0–4ч: {_as_int(counts.get('window_0_4h_core_ready_2plus_both') or counts.get('window_0_4h_ready_2plus_both'))}/{_as_int(counts.get('window_0_4h'))} core-ready",
            f"• 0–12ч: {_as_int(counts.get('window_0_12h_core_ready_2plus_both') or counts.get('window_0_12h_ready_2plus_both'))}/{_as_int(counts.get('window_0_12h'))} core-ready",
        ]
        if excluded_values:
            block_lines.append(f"• Excluded from active core: {', '.join(excluded_values)}")
        block = "\n".join(block_lines)
        text = _insert_before(text, "🚫 Почему не опубликовано", block)
        text = _replace_conclusion(text, counts)

    inv = diag.get("current_inventory_windows") if isinstance(diag.get("current_inventory_windows"), dict) else {}
    if inv:
        block = "\n".join([
            "🕒 Current day inventory windows",
            f"• Active matches: {_as_int(inv.get('active_matches'))} | odds refresh needed: {_as_int(inv.get('matches_needing_odds_refresh'))}",
            f"• Final pre-kickoff checks: {_as_int(inv.get('final_pre_kickoff_checks'))} | no next regular run: {_as_int(inv.get('no_more_regular_run_before_kickoff'))}",
            f"• 0–4ч inventory: {_as_int(inv.get('window_0_4h_ready'))}/{_as_int(inv.get('window_0_4h'))} ready | context 2+: {_as_int(inv.get('window_0_4h_context_2plus'))} | price 2+: {_as_int(inv.get('window_0_4h_price_2plus'))}",
            f"• 0–12ч inventory: {_as_int(inv.get('window_0_12h_ready'))}/{_as_int(inv.get('window_0_12h'))} ready | context 2+: {_as_int(inv.get('window_0_12h_context_2plus'))} | price 2+: {_as_int(inv.get('window_0_12h_price_2plus'))}",
        ])
        text = _insert_before(text, "Coverage truth", block)

    truth = diag.get("coverage_truth") if isinstance(diag.get("coverage_truth"), dict) else {}
    truth_counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
    if truth_counts:
        block = "\n".join([
            "Coverage truth",
            f"• Inventory rows: {_as_int(truth_counts.get('matches_total'))}",
            f"• 2+ price confirmations: {_as_int(truth_counts.get('matches_with_2plus_price_confirmations'))}",
            f"• 2+ independent odds sources: {_as_int(truth_counts.get('matches_with_2plus_odds_sources'))}",
            f"• 2+ context sources: {_as_int(truth_counts.get('matches_with_2plus_context_sources'))}",
            f"• ready publish by strict truth: {_as_int(truth_counts.get('matches_ready_for_publish'))}",
        ])
        text = _insert_before(text, "🚫 Почему не опубликовано", block)
    if "Нет reject reasons в свежих артефактах" in text or "raw-кандидатов нет" in text:
        block = _candidate_factory_blockers_block()
        if block:
            text = _insert_before(text, "📌 Вывод", block)
    return text


v7.v5.build_payload = build_payload
v7.v5.render = render
v7.build_payload = build_payload
v7.render = render
_write_status({"status": "installed", "renderer": "v8", "main_module": "v7.v5", "sstats_nested_normalizer": True})


if __name__ == "__main__":
    raise SystemExit(v7.v5.main())
