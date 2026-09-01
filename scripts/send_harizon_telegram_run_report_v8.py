"""HARIZON Telegram run report v8.

Readable bookmaker-quorum report. Publication price contract:
- selected market side confirmed by 2+ real bookmakers;
- price-integrity guard stays mandatory;
- 2+ independent odds sources are an A-tier strict metric, not a B-tier block.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

V7_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v7.py")
EXPORT_DIR = Path(".data/exports")
PROGRESSIVE_PLAN = EXPORT_DIR / "latest-progressive-coverage-plan.json"
DAILY_COVERAGE_PLAN = EXPORT_DIR / "latest-daily-coverage-plan.json"
TRUTH_REPORT = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
V8_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v8-status.json"
PRICE_GUARD_PATH = EXPORT_DIR / "latest-controlled-fallback-price-integrity-guard.json"
BOOKMAKER_NORMALIZER_PATH = EXPORT_DIR / "latest-bookmaker-quorum-coverage-normalizer.json"
TIMING_GUARD_PATH = EXPORT_DIR / "latest-controlled-fallback-publication-timing-guard.json"
BOOKMAKER_BACKFILL_PATH = EXPORT_DIR / "latest-odds-api-bookmaker-quorum-mapping-backfill.json"
ODDS_API_OFFER_SNAPSHOT_PATH = EXPORT_DIR / "latest-odds-api-io-offer-snapshot.json"
PUBLICATION_LEDGER_SYNC_PATH = EXPORT_DIR / "latest-publication-ledger-sync.json"
RUN_BOT_STEP_STATUS_PATH = EXPORT_DIR / "latest-run-bot-step-status.json"


def _load_v7() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v7", V7_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V7_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v7 = _load_v7()
_base_build_payload = v7.v5.build_payload


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


def _as_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


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
    deep_report = _load_json(EXPORT_DIR / "latest-sstats-deep-inventory-enrichment.json")
    if deep_report:
        deep = deep or _as_int(deep_report.get("enriched_matches"))
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


def _line_lifecycle_reason(reason: Any) -> bool:
    text = str(reason or "").strip()
    if not text:
        return False
    return text in {
        "line_movement_guard_waiting_next_run",
        "line_movement_guard_dropped",
        "needs_next_cron_line_movement_recheck",
    } or "line_movement" in text


def _best_non_line_reject_reason(payload: dict[str, Any]) -> str:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    for row in reasons:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").strip()
        if reason and not _line_lifecycle_reason(reason):
            return reason
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    try:
        if v7.v5.has_non_line_candidate_rejections(evaluated):
            for row in evaluated:
                if not isinstance(row, dict):
                    continue
                for reason in row.get("reject_reasons") or []:
                    text = str(reason or "").strip()
                    if text and not _line_lifecycle_reason(text):
                        return text
    except Exception:
        pass
    return ""


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    _normalize_runtime_patched_sstats(payload)
    plan = _load_json(PROGRESSIVE_PLAN)
    daily_plan = _load_json(DAILY_COVERAGE_PLAN)
    payload["version"] = "harizon-telegram-report-v8-bookmaker-mapping-guard"
    payload.setdefault("diagnostics", {})["progressive_core_coverage"] = {
        "contract": plan.get("contract") if isinstance(plan.get("contract"), dict) else {},
        "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
        "gap_sample_size": len(plan.get("core_gap_sample") or plan.get("gap_sample") or []) if isinstance(plan, dict) else 0,
    }
    provider_health = (
        daily_plan.get("provider_assignment_health")
        if isinstance(daily_plan.get("provider_assignment_health"), dict)
        else {}
    )
    if provider_health:
        payload.setdefault("diagnostics", {})["provider_coverage_routing"] = {
            "provider_targets": _as_int(
                provider_health.get("provider_coverage_targets")
                or daily_plan.get("provider_coverage_target_count")
            ),
            "model_targets": _as_int(
                provider_health.get("focused_targets")
                or daily_plan.get("phase_cumulative_target")
            ),
            "role_assignments": _as_int(
                provider_health.get("provider_role_assignments")
            ),
            "active_provider_eligible_rows": _as_int(
                provider_health.get("active_provider_eligible_rows")
            ),
            "publication_scope_widened": False,
        }
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
    price_guard = _load_json(PRICE_GUARD_PATH)
    if price_guard:
        payload.setdefault("diagnostics", {})["price_integrity_guard"] = {
            "enabled": bool(price_guard.get("enabled", True)),
            "policy": price_guard.get("policy") or "",
            "removed_total": _as_int(price_guard.get("removed_total")),
            "sources": price_guard.get("sources") if isinstance(price_guard.get("sources"), dict) else {},
        }
    bookmaker_normalizer = _load_json(BOOKMAKER_NORMALIZER_PATH)
    if bookmaker_normalizer:
        payload.setdefault("diagnostics", {})["bookmaker_quorum_normalizer"] = bookmaker_normalizer
        # Prefer normalized bookmaker-contract counts over legacy independent-source truth.
        if isinstance(bookmaker_normalizer.get("counts"), dict):
            payload.setdefault("diagnostics", {}).setdefault("coverage_truth", {})["counts"] = bookmaker_normalizer["counts"]
    timing_guard = _load_json(TIMING_GUARD_PATH)
    if timing_guard:
        payload.setdefault("diagnostics", {})["publication_timing_guard"] = timing_guard
    bookmaker_backfill = _load_json(BOOKMAKER_BACKFILL_PATH)
    if bookmaker_backfill:
        payload.setdefault("diagnostics", {})["bookmaker_quorum_backfill"] = bookmaker_backfill
    odds_api_snapshot = _load_json(ODDS_API_OFFER_SNAPSHOT_PATH)
    if odds_api_snapshot:
        payload.setdefault("diagnostics", {})["odds_api_io_offer_snapshot"] = {
            "status": odds_api_snapshot.get("status") or "",
            "rows_count": _as_int(odds_api_snapshot.get("rows_count")),
            "matches_count": _as_int(odds_api_snapshot.get("matches_count")),
            "matches_with_2plus_books_any_market": _as_int(odds_api_snapshot.get("matches_with_2plus_books_any_market")),
            "matches_with_2plus_books_same_side_market": _as_int(odds_api_snapshot.get("matches_with_2plus_books_same_side_market")),
        }
    inventory_target_expand = _load_json(EXPORT_DIR / "latest-day-inventory-target-expand.json")
    if inventory_target_expand:
        payload.setdefault("diagnostics", {})["inventory_target_expand"] = inventory_target_expand
    publication_ledger_sync = _load_json(PUBLICATION_LEDGER_SYNC_PATH)
    if publication_ledger_sync:
        payload.setdefault("diagnostics", {})["publication_ledger_sync"] = publication_ledger_sync
    run_bot_step_status = _load_json(RUN_BOT_STEP_STATUS_PATH)
    if run_bot_step_status:
        payload.setdefault("diagnostics", {})["run_bot_step_status"] = run_bot_step_status
    payload.setdefault("diagnostics", {})["github_actions"] = {
        "run_id": os.getenv("GITHUB_RUN_ID") or "",
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or "",
        "workflow": os.getenv("GITHUB_WORKFLOW") or "run-bot",
        "repository": os.getenv("GITHUB_REPOSITORY") or "Harfdsfsq/sports-bot",
    }
    if str(payload.get("top_reason") or "") == "line_movement_guard_waiting_next_run":
        replacement = _best_non_line_reject_reason(payload)
        if replacement:
            payload["top_reason"] = replacement
    return payload


def _pct(part: Any, total: Any) -> str:
    p = _as_int(part)
    t = _as_int(total)
    if t <= 0:
        return "0%"
    return f"{round(p * 100.0 / t)}%"


def _reason_ru(reason: Any) -> str:
    try:
        return v7.v5.reason_ru(str(reason))
    except Exception:
        return str(reason or "").replace("_", " ")


def _top_reasons(payload: dict[str, Any], limit: int = 8) -> list[str]:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    if not reasons:
        return ["• Нет reject reasons в свежих артефактах."]
    visible: list[dict[str, Any]] = []
    diagnostic_odds_source_count = 0
    for row in reasons:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        count = _as_int(row.get("count"))
        if count <= 0:
            continue
        if "odds_sources_below_min" in reason or "odds sources below min" in reason.lower():
            diagnostic_odds_source_count += count
            continue
        visible.append(row)
    if not visible and diagnostic_odds_source_count:
        visible = [{"reason_ru": "устаревшая диагностика odds-source; проверяй 2+ букмекера/price-integrity", "count": diagnostic_odds_source_count}]
    total = sum(_as_int(row.get("count")) for row in visible if isinstance(row, dict)) or 1
    out: list[str] = []
    for row in visible[:limit]:
        if not isinstance(row, dict):
            continue
        count = _as_int(row.get("count"))
        if count <= 0:
            continue
        ru = row.get("reason_ru") or _reason_ru(row.get("reason"))
        out.append(f"• {ru}: {count} ({round(count * 100.0 / total)}%)")
    if diagnostic_odds_source_count:
        out.append(f"• Диагностика legacy odds-source: {diagnostic_odds_source_count} — не заменяет обязательные 2+ odds-source")
    return out or ["• Нет reject reasons в свежих артефактах."]


def _candidate_lines(payload: dict[str, Any], limit: int = 4) -> list[str]:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    rows = [row for row in evaluated if isinstance(row, dict)][:limit]
    if not rows:
        return ["• Нет проверенных reserve-кандидатов в свежем fallback report."]
    out: list[str] = []
    for idx, row in enumerate(rows, 1):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        odds = _as_float(metrics.get("odds") or row.get("odds"))
        ev = _as_float(metrics.get("canonical_ev_pct") or row.get("ev_pct"))
        edge = _as_float(metrics.get("canonical_edge_pp") or row.get("edge_pct"))
        q = _as_float(metrics.get("quality_score") or row.get("quality_score"))
        out.append(
            f"{idx}. {row.get('home_team')} — {row.get('away_team')} | {row.get('selection')} @{odds:.2f} | "
            f"EV {ev:+.1f}% | edge {edge:+.1f} п.п. | q {q:.1f}"
        )
        reasons = row.get("reject_reasons") if isinstance(row.get("reject_reasons"), list) else []
        visible_reasons = []
        legacy_count = 0
        for reason in reasons:
            text = str(reason or "")
            if "odds_sources_below_min" in text or "odds sources below min" in text.lower():
                legacy_count += 1
                continue
            visible_reasons.append(reason)
        if visible_reasons:
            out.append("   • причина: " + ", ".join(_reason_ru(x) for x in visible_reasons[:4]))
        elif legacy_count:
            out.append("   • причина: только legacy odds-source диагностика; публикацию решают 2+ odds-source + 2+ букмекера + 2+ контекста + value")
        thresholds = metrics.get("proxy_single_source_thresholds") if isinstance(metrics.get("proxy_single_source_thresholds"), dict) else {}
        if thresholds and thresholds.get("applies"):
            out.append(
                "   • proxy single-source: "
                f"fact edge {edge:.1f}pp / EV {ev:.1f}% / conf {_as_float(metrics.get('confidence')):.1f}; "
                f"min edge {_as_float(thresholds.get('min_edge_pp')):.1f}pp / "
                f"EV {_as_float(thresholds.get('min_ev_pct')):.1f}% / "
                f"conf {_as_float(thresholds.get('min_confidence')):.1f}"
            )
        xg = metrics.get("xg_sanity") if isinstance(metrics.get("xg_sanity"), dict) else {}
        if xg and not bool(xg.get("enabled")):
            out.append(f"   • xG sanity: missing ({xg.get('reason') or 'no usable xG'})")
    return out


def _safe_line(value: Any, default: str = "н/д") -> str:
    text = str(value or "").strip()
    return text if text else default


def render(payload: dict[str, Any]) -> str:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
    line = payload.get("line_guard") if isinstance(payload.get("line_guard"), dict) else {}
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    truth = diag.get("coverage_truth") if isinstance(diag.get("coverage_truth"), dict) else {}
    truth_counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
    price_guard = diag.get("price_integrity_guard") if isinstance(diag.get("price_integrity_guard"), dict) else {}
    bookmaker_norm = diag.get("bookmaker_quorum_normalizer") if isinstance(diag.get("bookmaker_quorum_normalizer"), dict) else {}
    bookmaker_backfill = diag.get("bookmaker_quorum_backfill") if isinstance(diag.get("bookmaker_quorum_backfill"), dict) else {}
    odds_api_snapshot = diag.get("odds_api_io_offer_snapshot") if isinstance(diag.get("odds_api_io_offer_snapshot"), dict) else {}
    timing_guard = diag.get("publication_timing_guard") if isinstance(diag.get("publication_timing_guard"), dict) else {}
    inventory_target_expand = diag.get("inventory_target_expand") if isinstance(diag.get("inventory_target_expand"), dict) else {}
    run_bot_step_status = diag.get("run_bot_step_status") if isinstance(diag.get("run_bot_step_status"), dict) else {}
    github_actions = diag.get("github_actions") if isinstance(diag.get("github_actions"), dict) else {}
    publication_ledger_sync = diag.get("publication_ledger_sync") if isinstance(diag.get("publication_ledger_sync"), dict) else {}
    provider_routing = diag.get("provider_coverage_routing") if isinstance(diag.get("provider_coverage_routing"), dict) else {}
    window_counts = bookmaker_norm.get("window_counts") if isinstance(bookmaker_norm.get("window_counts"), dict) else {}

    inv_total = _as_int(truth_counts.get("matches_total")) or _as_int(coverage.get("day_inventory_total"))
    with_odds = _as_int(truth_counts.get("matches_with_odds")) or _as_int(coverage.get("day_inventory_with_odds")) or _as_int(coverage.get("matches_with_offers"))
    with_context = _as_int(truth_counts.get("matches_with_context")) or _as_int(coverage.get("day_inventory_with_context")) or _as_int(coverage.get("matches_with_context"))
    price2 = _as_int(truth_counts.get("matches_with_2plus_price_confirmations")) or _as_int(coverage.get("matches_with_2plus_books"))
    odds_sources2 = _as_int(truth_counts.get("matches_with_2plus_odds_sources"))
    context2 = _as_int(truth_counts.get("matches_with_2plus_context_sources"))
    ready_model = _as_int(truth_counts.get("matches_ready_for_model")) or _as_int(coverage.get("ready_for_model"))
    ready_publish = _as_int(truth_counts.get("matches_ready_for_publish"))
    run_matches = _as_int(coverage.get("matches_seen"))
    inv_target = (
        _as_int(inventory_target_expand.get("target"))
        or _as_int(truth_counts.get("target_matches"))
        or _as_int(coverage.get("day_inventory_target"))
        or 300
    )
    target_shortfall = max(0, inv_target - inv_total) if inv_target else 0
    expand_matches_after = _as_int(inventory_target_expand.get("matches_after"))
    expand_target_shortfall = _as_int(inventory_target_expand.get("target_shortfall"))
    target_status = str(inventory_target_expand.get("status") or "").strip()
    target_note = f"; shortfall {target_shortfall}" if target_shortfall else ""
    if expand_matches_after and expand_matches_after != inv_total:
        target_note += f"; target-expand stage {expand_matches_after}/{inv_target}"
        if expand_target_shortfall:
            target_note += f", shortfall {expand_target_shortfall}"
    if target_status:
        target_note += f"; status {target_status}"

    raw_top_reason = str(payload.get("top_reason") or "").strip()
    if _line_lifecycle_reason(raw_top_reason):
        replacement_reason = _best_non_line_reject_reason(payload)
        if replacement_reason:
            raw_top_reason = replacement_reason
    timing_deferred_total = _as_int(timing_guard.get("deferred_total"))
    top_reason = _reason_ru(raw_top_reason)
    waiting_line_next_run = _as_int(line.get("waiting_next_run"))
    dropped_final_line = _as_int(line.get("dropped_final"))
    published_count = _as_int(funnel.get("published_count"))
    fallback_published = _as_int(funnel.get("fallback_published_count"))
    published = published_count > 0 or payload.get("status") == "published"
    runtime_status_raw = str(run_bot_step_status.get("status") if run_bot_step_status else "").strip()
    runtime_failed = bool(runtime_status_raw) and runtime_status_raw not in {"0", "0.0", "ok", "success"}
    if not published and timing_deferred_total > 0 and raw_top_reason.lower() in {"", "no viable controlled fallback", "no_viable_controlled_fallback", "none"}:
        top_reason = f"кандидаты отложены до финального run перед матчем ({timing_deferred_total})"
    if (
        not published
        and waiting_line_next_run > 0
        and raw_top_reason.lower() in {
            "line_movement_guard_dropped",
            "line_movement_guard_waiting_next_run",
            "needs_next_cron_line_movement_recheck",
        }
    ):
        top_reason = f"кандидаты ждут следующий cron для второго снимка линии ({waiting_line_next_run})"
    if published:
        status_line = "✅ прогноз опубликован"
    elif runtime_failed:
        status_line = "🔴 Прогнозный прогон не завершился: ниже post-run диагностика по сохранённым артефактам."
        top_reason = f"runtime failed: run-once завершился status {runtime_status_raw}"
    elif timing_deferred_total > 0 and raw_top_reason.lower() in {"", "no viable controlled fallback", "no_viable_controlled_fallback", "none"}:
        status_line = "🟡 Прогнозов нет: есть кандидаты, но timing guard отложил их до финального run."
    else:
        status_line = "🟡 Прогнозов нет: текущие кандидаты не прошли финальные guards."

    odds = api.get("odds_api_io") if isinstance(api.get("odds_api_io"), dict) else {}
    sstats = api.get("sstats") if isinstance(api.get("sstats"), dict) else {}
    bzz = api.get("bzzoiro") if isinstance(api.get("bzzoiro"), dict) else {}
    sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}
    combos = coverage.get("secondary_combinations") if isinstance(coverage.get("secondary_combinations"), dict) else {}
    combo_text = ", ".join(f"{k}: {v}" for k, v in sorted(combos.items(), key=lambda item: -_as_int(item[1]))[:6]) or "н/д"

    raw_books2 = _as_int(bookmaker_norm.get("raw_odds_api_2plus_books")) or _as_int(odds.get("books_2plus"))
    normalized_books2 = _as_int(bookmaker_norm.get("normalized_inventory_2plus_books")) or price2
    if normalized_books2 and normalized_books2 != price2:
        price2 = normalized_books2
    # Always compute the displayed mapping gap from the same raw/normalized values.
    # Older normalizer artifacts can contain raw=0/lost=0, while the API section already knows raw books_2plus.
    lost_mapping = max(0, raw_books2 - price2) if raw_books2 else _as_int(bookmaker_norm.get("lost_mapping"))
    raw_book_line = None
    if raw_books2:
        if price2 > raw_books2:
            raw_book_line = (
                f"• Raw 2+ букмекера odds-api.io current snapshot: {raw_books2} | "
                f"normalized cumulative inventory: {price2}."
            )
        else:
            raw_book_line = f"• Raw 2+ букмекера odds-api.io: {raw_books2} | normalized inventory: {price2} | mapping gap: {lost_mapping}"
    backfill_line = None
    if bookmaker_backfill:
        backfill_line = (
            f"• Odds-api mapping backfill: mapped {_as_int(bookmaker_backfill.get('mapped_matches'))}, "
            f"changed truth {_as_int(bookmaker_backfill.get('changed_truth_rows'))}, "
            f"changed inventory {_as_int(bookmaker_backfill.get('changed_inventory_rows'))}"
        )
    snapshot_line = None
    if odds_api_snapshot:
        snapshot_line = (
            f"• Odds-api offer snapshot: rows {_as_int(odds_api_snapshot.get('rows_count'))}, "
            f"matches {_as_int(odds_api_snapshot.get('matches_count'))}, "
            f"same-side 2+ books {_as_int(odds_api_snapshot.get('matches_with_2plus_books_same_side_market'))}"
        )

    ledger_line = None
    if publication_ledger_sync:
        bets_sync = publication_ledger_sync.get("bets") if isinstance(publication_ledger_sync.get("bets"), dict) else {}
        unique_bets = _as_int(bets_sync.get("unique_published_bets") or bets_sync.get("published_ledger_rows"))
        duplicates_removed = _as_int(bets_sync.get("duplicates_removed"))
        pending_unique = _as_int(bets_sync.get("pending_unique_rows"))
        policy = str(bets_sync.get("dedupe_policy") or "semantic dedupe").replace("_", " ")
        if unique_bets or pending_unique or duplicates_removed:
            ledger_line = (
                f"• Ledger: unique published {unique_bets}; pending {pending_unique}; "
                f"duplicates removed {duplicates_removed}; policy {policy}."
            )

    line_guard_text = (
        f"• Line guard: увидел {_as_int(line.get('seen'))}, "
        f"оставил {_as_int(line.get('kept'))}, "
    )
    if waiting_line_next_run and dropped_final_line <= 0:
        line_guard_text += f"отложил {waiting_line_next_run} до следующего cron"
    elif waiting_line_next_run:
        line_guard_text += f"отложил {waiting_line_next_run}, снял {dropped_final_line}"
    else:
        line_guard_text += f"снял {_as_int(line.get('dropped'))}"

    lines: list[str] = [
        "🧾 HARIZON — понятный отчёт по запуску",
        status_line,
        f"• Главная причина: {top_reason}",
        "",
        "📦 Инвентарь и покрытие",
        f"• Инвентарь дня: собрано {inv_total}/{inv_target} матчей (цель: 300 лучших, добор каждый run{target_note}). Runtime rows processed: {run_matches} (это не размер inventory).",
    ]
    provider_targets = _as_int(provider_routing.get("provider_targets"))
    if provider_targets:
        lines.append(
            "• Очередь provider-enrichment: "
            f"{provider_targets} активных матчей; model scope "
            f"{_as_int(provider_routing.get('model_targets'))}; назначений по ролям "
            f"{_as_int(provider_routing.get('role_assignments'))}. "
            "Очередь сбора не расширяет публикационный scope."
        )
    lines.extend([
        f"• 1+ линия: {with_odds}/{inv_total} ({_pct(with_odds, inv_total)}) | 1+ контекст: {with_context}/{inv_total} ({_pct(with_context, inv_total)})",
        f"• 2+ букмекера: {price2}/{inv_total} ({_pct(price2, inv_total)})",
    ])
    if raw_book_line:
        lines.append(raw_book_line)
    if backfill_line:
        lines.append(backfill_line)
    if snapshot_line:
        lines.append(snapshot_line)
    lines.extend([
        f"• 2+ independent odds-source: {odds_sources2}/{inv_total} ({_pct(odds_sources2, inv_total)}) — обязательный блок публикации",
        f"• 2+ контекста: {context2}/{inv_total} ({_pct(context2, inv_total)})",
        f"• Готово для модели: {ready_model}/{inv_total} ({_pct(ready_model, inv_total)})",
    ])
    if window_counts:
        lines.extend(["", "🧭 Покрытие ближайших окон"])
        for key in ("0-4", "4-8", "8-12", "12-16", "16-20", "20-24", ">24"):
            w = window_counts.get(key) if isinstance(window_counts.get(key), dict) else {}
            if not w:
                continue
            lines.append(
                f"• {key} ч: матчей {_as_int(w.get('matches'))}; "
                f"2+ бук {_as_int(w.get('bookmaker_2plus'))}; "
                f"2+ конт {_as_int(w.get('context_2plus'))}; "
                f"A-cover {_as_int(w.get('a_contract'))}; "
                f"B-cover {_as_int(w.get('b_contract'))}; "
                f"ждут line {_as_int(w.get('waiting_movement'))}"
            )
    lines.extend([
        "",
        "🏷️ A/B-tier публикация",
        f"• A-tier strict-ready: {max(ready_publish, published_count)} | опубликовано: {published_count}",
        "  A-tier = 2+ букмекера по той же стороне рынка + 2+ контекста + подтверждённое движение линии + value.",
        f"• B-tier bookmaker coverage: {price2} | fallback опубликовано: {fallback_published}",
        "  B-tier = 2+ букмекера + 1+ контекст + второй снимок линии + value сохранился.",
        f"• Пересечение 2+ букмекера ∩ 2+ контекста: до {min(price2, context2)} матчей; exact-ready считается после movement/value/xG.",
        "• A-cover/B-cover в окнах = покрытие; strict-ready = после movement/value/xG/quality.",
        "• 2+ independent odds-source — A-tier strict metric; для B-tier не обязательный блок.",
    ])
    if price_guard:
        lines.append(f"• Price-integrity guard: снял {_as_int(price_guard.get('removed_total'))} подозрительных кандидатов до fallback.")
    if timing_guard:
        deferred = _as_int(timing_guard.get('deferred_total'))
        if deferred:
            lines.append(f"• Timing guard: отложил {deferred} кандидатов до последнего регулярного run перед матчем.")
    lines.append("• Тоталы к публикации: только целые и .5; четвертные линии .25/.75 не публикуются.")
    if ledger_line:
        lines.append(ledger_line)
    lines.extend([
        "",
        "🛡️ Движение линии и финальный фильтр",
        f"• Pre-kickoff проверок: {_as_int(line.get('final_pre_kickoff_checks'))} | матчей без следующего регулярного run: {_as_int(line.get('no_more_regular_run_before_kickoff'))}",
        line_guard_text,
        "",
        "🧪 Воронка кандидатов",
        f"• Raw/candidates before quality: {_as_int(funnel.get('raw_candidates'))}/{_as_int(funnel.get('candidates_before_quality'))}",
        f"• Quality прошло: {_as_int(funnel.get('passed_candidates'))} | publishable: {_as_int(funnel.get('publishable_candidates'))} | опубликовано: {published_count}",
        f"• Controlled fallback: seen {_as_int(funnel.get('fallback_candidates_seen'))} | evaluated {_as_int(funnel.get('fallback_evaluated'))} | published {fallback_published}",
        "",
        "📡 Провайдеры: запросы и полезные данные",
        f"• odds-api.io: events req {_as_int(odds.get('events_req'))}, odds req {_as_int(odds.get('odds_req'))}; смэтчил матчей {_as_int(odds.get('matched'))}; offers {_as_int(odds.get('offers'))}; 2+ букмекера {_as_int(odds.get('books_2plus'))}; ошибок {_as_int(odds.get('errors'))}.",
        f"• Bzzoiro: direct req {_as_int(bzz.get('requests'))}, v2 req {_as_int(bzz.get('v2_requests'))}; v2 ctx {_as_int(bzz.get('v2_contexts'))}; v2 odds {_as_int(bzz.get('v2_odds_resources'))}; secondary offers {_as_int(bzz.get('secondary_offers_added'))}; overlap odds-api.io {_as_int(bzz.get('overlap'))}; ошибок {max(_as_int(bzz.get('errors')), _as_int(bzz.get('v2_errors')))}.",
        f"• SStats: запросы {_as_int(sstats.get('requests'))}; сырых строк {_as_int(sstats.get('rows'))}; контекстов {_as_int(sstats.get('contexts'))}; deep-enriched {_as_int(sstats.get('deep_enriched'))}; team-form {_as_int(sstats.get('team_form_contexts'))}; direct {_as_int(sstats.get('direct_contexts'))}; ошибок {_as_int(sstats.get('errors'))}.",
        f"• SportLogic: enabled {bool(sport.get('enabled'))}; запросы {_as_int(sport.get('requests'))}; fixtures {_as_int(sport.get('fixtures_fetched'))}; matched {_as_int(sport.get('matched'))}; odds req {_as_int(sport.get('odds_requests'))}; offers {_as_int(sport.get('offers'))}; ошибок {_as_int(sport.get('errors'))}; diag {_safe_line(sport.get('diagnosis'))}.",
        f"• Комбинации источников линий: {combo_text}",
        "",
        "🚫 Почему не опубликовано",
        *_top_reasons(payload),
        "",
        "🔎 Проверенные reserve-кандидаты",
        *_candidate_lines(payload),
        "",
        "📌 Что это значит",
    ])
    if published:
        lines.append("• Прогноз реально опубликован; отчёт выше показывает, через какой контракт он прошёл.")
    elif timing_deferred_total > 0 and raw_top_reason.lower() in {"", "no viable controlled fallback", "no_viable_controlled_fallback", "none"}:
        lines.append(f"• Есть кандидаты, но timing guard отложил {timing_deferred_total} до финального регулярного run перед стартом. Сейчас публикацию не форсируем.")
    elif _as_int(price_guard.get('removed_total')) > 0 and _as_int(funnel.get('fallback_candidates_seen')) == 0:
        lines.append("• Fallback-пул опустел после price-integrity: подозрительные цены не публикуем, даже если EV выглядел положительным.")
    elif ("line movement" in top_reason.lower() or "line_movement" in raw_top_reason) and waiting_line_next_run > 0:
        lines.append("• Есть кандидат по bookmaker-contract, но нужен второй снимок линии. Ждём следующий регулярный run.")
    elif ("line movement" in top_reason.lower() or "line_movement" in raw_top_reason) and dropped_final_line > 0:
        lines.append("• Кандидат был проверен финальным line guard и снят: публикацию не форсируем, пока edge/EV/movement ниже порога.")
    else:
        lines.append("• Не форсировать публикацию: текущие кандидаты отрезаны xG/quality/value/line movement, а не старым требованием 2 independent odds sources.")
    if lost_mapping > 0:
        lines.append("• Есть mapping gap между raw bookmaker coverage и normalized inventory: следующий слой — матчинг raw odds-api offers к frozen inventory.")
    lines.append("• Ценовой контракт сейчас: 2+ букмекера по той же стороне рынка; price-integrity guard остаётся обязательным.")
    run_id = str(github_actions.get('run_id') or '').strip()
    repo = str(github_actions.get('repository') or 'Harfdsfsq/sports-bot').strip()
    workflow = str(github_actions.get('workflow') or 'run-bot').strip()
    attempt = str(github_actions.get('run_attempt') or '').strip() or '1'
    if run_id:
        lines.extend([
            "",
            "🔗 GitHub Actions",
            f"• Run ID: {run_id}",
            f"• Run URL: https://github.com/{repo}/actions/runs/{run_id}",
            f"• Artifact: run-bot-{run_id}",
            f"• workflow {workflow}, attempt {attempt}",
        ])
    return "\n".join(lines)


v7.v5.build_payload = build_payload
v7.v5.render = render
v7.build_payload = build_payload
v7.render = render
_write_status({
    "status": "installed",
    "renderer": "v8",
    "main_module": "v7.v5",
    "format": "readable_bookmaker_quorum_mapping_timing_point_guard_windows",
    "sstats_nested_normalizer": True,
})


if __name__ == "__main__":
    raise SystemExit(v7.v5.main())
