from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")


def _load(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(value))
    except Exception:
        return 0


def _disabled_sportlogic_env() -> bool:
    return any(str(os.getenv(n) or "").strip().lower() in {"0", "false", "no", "off"} for n in ("DAY_INVENTORY_ENABLE_SPORTLOGIC", "ENABLE_SPORTLOGIC", "SPORTLOGIC_ENABLED", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED"))


def _truth_counts() -> dict[str, int]:
    truth = _load(EXPORT / "latest-day-inventory-coverage-truth.json", {})
    counts = truth.get("counts") if isinstance(truth, dict) and isinstance(truth.get("counts"), dict) else {}
    return {str(k): _int(v) for k, v in counts.items()}


def _reserve_quality_from_metrics(metrics: dict[str, Any]) -> float:
    direct = _num(metrics.get("reserve_quality_score") or metrics.get("quality_score"), -1.0)
    if direct > 0:
        return direct
    ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")), 0.0)
    edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")), 0.0)
    odds = _num(metrics.get("odds"), 0.0)
    books = max(_int(metrics.get("books_count")), _int(metrics.get("bookmaker_count")), 2 if _int(metrics.get("confirmation_sources_count")) >= 2 else 0)
    conf = max(_int(metrics.get("confirmation_sources_count")), _int(metrics.get("context_sources_count")))
    score = 38.0 + min(18.0, ev * 1.45) + min(16.0, edge * 3.0) + min(10.0, books * 3.0) + min(10.0, conf * 1.5)
    if 1.75 <= odds <= 2.55:
        score += 4.0
    elif odds < 1.70 or odds > 2.90:
        score -= 8.0
    return round(max(0.0, min(100.0, score)), 1)


def patch_payload_quality(payload: dict[str, Any]) -> dict[str, Any]:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    patched = 0
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        q = _reserve_quality_from_metrics(metrics)
        if q > 0:
            metrics["reserve_quality_score"] = q
            if _num(metrics.get("quality_score"), 0.0) <= 0:
                metrics["quality_score"] = q
            row["reserve_quality_score"] = q
            patched += 1
    payload.setdefault("diagnostic_repairs", {})["reserve_quality_samples_patched"] = patched
    return payload


def patch_text_quality(text: str, payload: dict[str, Any]) -> str:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = [x for x in (samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []) if isinstance(x, dict)]
    for idx, row in enumerate(evaluated[:6], 1):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        q = _reserve_quality_from_metrics(metrics)
        if q > 0:
            text = re.sub(rf"(^\s*{idx}\. .*? \| q )0\.0\b", rf"\g<1>{q:.1f} reserve", text, count=1, flags=re.MULTILINE)
    return text


def patch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if _disabled_sportlogic_env():
        api = payload.setdefault("api", {})
        if isinstance(api, dict):
            api["sportlogic"] = {"enabled": False, "requests": 0, "fixtures": 0, "odds_requests": 0, "matched": 0, "offers": 0, "errors": 0, "diagnosis": "disabled_by_env"}
    counts = _truth_counts()
    coverage = payload.setdefault("coverage", {})
    if isinstance(coverage, dict) and counts:
        coverage["day_inventory_total"] = counts.get("matches_total", coverage.get("day_inventory_total", 300)) or 300
        coverage["day_inventory_with_odds"] = counts.get("matches_with_odds", coverage.get("day_inventory_with_odds", 0))
        coverage["day_inventory_with_context"] = counts.get("matches_with_context", coverage.get("day_inventory_with_context", 0))
        coverage["ready_for_model"] = counts.get("matches_ready_for_model", coverage.get("ready_for_model", 0))
        coverage["b_tier_coverage_ready"] = counts.get("matches_b_tier_watch_ready", 0) or min(_int(coverage.get("day_inventory_with_odds")), counts.get("matches_with_2plus_price_confirmations", 0), _int(coverage.get("day_inventory_with_context")))
    queue = _load(EXPORT / "latest-a-tier-targeted-enrichment-queue.json", {})
    if isinstance(queue, dict):
        payload.setdefault("a_tier_enrichment", queue.get("summary") or {})
    return payload


def patch_runtime_lines(text: str) -> str:
    if _disabled_sportlogic_env():
        text = re.sub(r"^• sportlogic:.*$", "• sportlogic: enabled False, req 0, odds req 0, matched 0, offers 0, err 0", text, count=1, flags=re.MULTILINE)
        text = re.sub(r"^• SportLogic:.*$", "• SportLogic: disabled_by_env; запросы 0; rows 0; current fixtures 0; matched 0; odds req 0; offers 0; ошибок 0; diag disabled_by_env.", text, count=1, flags=re.MULTILINE)
    def repl(match: re.Match[str]) -> str:
        raw = _int(match.group(1)); before = _int(match.group(2)); after = _int(match.group(3)); gap = _int(match.group(4))
        if after < before or gap > 0:
            return f"• Bookmaker mapping repair: raw 2+ {raw}; normalized diagnostic stale/incomplete ({before}→{after}); фактический B-cover см. выше; gap after {gap}."
        return match.group(0)
    return re.sub(r"^• Bookmaker mapping repair: raw 2\+ (\d+); normalized (\d+)→(\d+); gap after (\d+)\.$", repl, text, count=1, flags=re.MULTILINE)


def patch_line_diagnostics(text: str) -> str:
    diag = _load(EXPORT / "latest-line-movement-diagnostics.json", {})
    counts = diag.get("class_counts") if isinstance(diag, dict) and isinstance(diag.get("class_counts"), dict) else {}
    if counts:
        order = ["actual_bad_movement", "selected_price_not_current", "line_snapshot_alias_or_missing", "line_snapshot_pending_or_alias", "not_confirmed", "unconfirmed_final", "missing_second_snapshot", "odds_below_min", "duplicate", "xg_direction_conflict"]
        labels = {"actual_bad_movement":"реально плохое движение","selected_price_not_current":"выбранный коэффициент уже не текущий","line_snapshot_alias_or_missing":"нет/alias mismatch snapshot линии","line_snapshot_pending_or_alias":"pending/alias snapshot","not_confirmed":"движение не подтверждено","unconfirmed_final":"финальная проверка не подтвердила","missing_second_snapshot":"нет второго снимка","odds_below_min":"коэффициент ниже минимума","duplicate":"дубликат","xg_direction_conflict":"конфликт направления с xG"}
        parts = [f"{labels[k]} {int(counts.get(k) or 0)}" for k in order if int(counts.get(k) or 0) > 0]
        if parts and "Line movement breakdown" not in text:
            text = text.replace("🚫 Почему не опубликовано", "• Line movement breakdown: " + "; ".join(parts[:8]) + ".\n🚫 Почему не опубликовано", 1)
    return text


def patch_a_tier_summary(text: str) -> str:
    queue = _load(EXPORT / "latest-a-tier-targeted-enrichment-queue.json", {})
    summary = queue.get("summary") if isinstance(queue, dict) and isinstance(queue.get("summary"), dict) else {}
    if not summary:
        return text
    bzz = _int(summary.get("bzzoiro_odds_target_count")); ctx = _int(summary.get("context_projection_target_count")); rech = _int(summary.get("high_value_recheck_target_count"))
    line = f"• A-tier enrichment queue: Bzzoiro odds targets {bzz}; context projection targets {ctx}; high-value recheck {rech}."
    if "A-tier enrichment queue" not in text:
        text = text.replace("🧪 Воронка кандидатов", line + "\n🧪 Воронка кандидатов", 1)
    return text


def patch_conclusion(text: str) -> str:
    replacement = "• Главный текущий стопор: line movement/freshness/current-price и уже опубликованные дубликаты; A-tier требует targeted Bzzoiro odds overlap + второго context-source."
    text = re.sub(r"^• Главный технический bottleneck:.*$", replacement, text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^• Главный текущий стопор:.*$", replacement, text, count=1, flags=re.MULTILINE)
    return text


def patch(payload: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
    payload = patch_payload_quality(payload)
    payload = patch_payload(payload)
    text = patch_text_quality(text, payload)
    text = patch_runtime_lines(text)
    text = patch_line_diagnostics(text)
    text = patch_a_tier_summary(text)
    text = patch_conclusion(text)
    return payload, text

__all__ = ["patch", "patch_payload_quality", "patch_text_quality", "patch_runtime_lines", "patch_payload"]
