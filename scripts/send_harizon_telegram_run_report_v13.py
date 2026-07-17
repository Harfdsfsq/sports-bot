from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

EXPORT_DIR = Path(".data/exports")


def _sanitize() -> None:
    try:
        from scripts.sanitize_line_movement_value_waits import main as sanitize_main

        sanitize_main()
    except Exception:
        pass


def _refresh_inventory_truth() -> None:
    """Make the Telegram report read the final repaired inventory state."""
    steps = (
        ("scripts.expand_day_inventory_to_target", "main"),
        ("scripts.extend_day_inventory_for_target_shortfall", "main"),
        ("scripts.repair_day_inventory_blank_rows", "main"),
        ("scripts.guard_day_inventory_no_shrink", "main"),
        ("scripts.backfill_inventory_bookmaker_coverage", "main"),
        ("scripts.bridge_runtime_context_coverage", "main"),
        ("scripts.repair_day_inventory_blank_rows", "main"),
        ("scripts.build_day_inventory_coverage_truth", "main"),
        ("scripts.day_inventory_cumulative_coverage", "main"),
    )
    for module_name, fn_name in steps:
        try:
            module = __import__(module_name, fromlist=[fn_name])
            fn = getattr(module, fn_name, None)
            if callable(fn):
                if module_name.endswith("guard_day_inventory_no_shrink"):
                    try:
                        fn(["repair"])
                    except TypeError:
                        fn()
                else:
                    fn()
        except Exception:
            pass


def _load_v12():
    target = Path(__file__).with_name("send_harizon_" + "telegram_run_report_v12.py")
    spec = importlib.util.spec_from_file_location("harizon_report_v12_loaded", target)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load report v12")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_any(path: Path) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return None


def _load_json(path: Path) -> dict[str, Any]:
    value = _load_any(path)
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def _run_status_text() -> str:
    path = EXPORT_DIR / "latest-run-bot-step-status.json"
    try:
        if path.exists() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""


def _main_run_timeout_status() -> str:
    status = _run_status_text()
    lowered = status.lower()
    if "status 124" in lowered or "timed out" in lowered or "timeout" in lowered:
        return status or "run-once timeout"
    return ""


def _strict_coverage_counts() -> dict[str, int]:
    truth = _load_json(EXPORT_DIR / "latest-day-inventory-coverage-truth.json")
    counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
    a_ready = _as_int(counts.get("matches_a_tier_coverage_ready"))
    b_ready = _as_int(counts.get("matches_b_tier_watch_ready"))
    if not b_ready:
        b_ready = a_ready
    return {
        "a_ready": a_ready,
        "b_ready": b_ready,
        "odds_2plus": _as_int(counts.get("matches_with_2plus_odds_sources")),
        "books_2plus": _as_int(counts.get("matches_with_2plus_price_confirmations")),
        "contexts_2plus": _as_int(counts.get("matches_with_2plus_context_sources")),
    }


def _replace_two_plus_contract_text(text: str) -> str:
    text = re.sub(
        r"B-tier 1\+ line/\d+\+ bookmaker/\d+\+ context coverage:",
        "B-tier 2+ line/2+ bookmaker/2+ context coverage:",
        text,
    )
    text = re.sub(
        r"B-tier = 1\+ линия/odds-source \+ \d+\+? букмекер/ценовое подтверждение \+ \d+\+? контекст \+ движение линии \+ value\.",
        "B-tier = 2+ линия/odds-source + 2+ букмекер/ценовое подтверждение + 2+ контекст + движение линии + value.",
        text,
    )
    text = re.sub(
        r"Контракт публикации сейчас: A-tier = 2 odds-source \+ 2 букмекера \+ 2 контекста; B-tier = 1 odds-source \+ \d+ букмекер \+ \d+ контекст;",
        "Контракт публикации сейчас: A-tier = 2 odds-source + 2 букмекера + 2 контекста; B-tier = 2 odds-source + 2 букмекера + 2 контекста;",
        text,
    )
    text = re.sub(
        r"• Active A/B contract: A=2 odds/2 books/2 context; B=1 odds/\d+ book/\d+ context\.",
        "• Active A/B contract: A=2 odds/2 books/2 context; B=2 odds/2 book/2 context.",
        text,
    )

    strict = _strict_coverage_counts()
    if any(strict.values()):
        text = re.sub(r"(• A-tier strict-ready:)\s*\d+", rf'\1 {strict["a_ready"]}', text)
        text = re.sub(
            r"(• B-tier 2\+ line/2\+ bookmaker/2\+ context coverage:)\s*\d+",
            rf'\1 {strict["b_ready"]}',
            text,
        )
        text = re.sub(
            r"• A-cover 2\+ odds-source ∩ 2\+ букмекера ∩ 2\+ контекста: до \d+ матчей; B-cover: до \d+ матчей\.",
            f'• A-cover 2+ odds-source ∩ 2+ букмекера ∩ 2+ контекста: {strict["a_ready"]} матчей; '
            f'B-cover strict intersection: {strict["b_ready"]} матчей.',
            text,
        )
        text = text.replace(
            "• 2+ independent odds-source — A-tier strict metric; для B-tier не обязательный блок.",
            "• 2+ independent odds-source обязателен и для A, и для B в текущем strict accumulation contract.",
        )
    return text


def _replace_bzzoiro_line(text: str) -> str:
    report = _load_json(EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json") or _load_json(
        EXPORT_DIR / "latest-bzzoiro-v2-source-matrix-runtime.json"
    )
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    bridge = _load_json(EXPORT_DIR / "latest-bzzoiro-overlap-bridge.json")
    hard = _load_json(EXPORT_DIR / "latest-bzzoiro-runtime-hard-budget.json")
    if not stats and not bridge and not hard:
        return text
    direct_req = 11
    current_match = re.search(r"• Bzzoiro: direct req (\d+),", text)
    if current_match:
        direct_req = _as_int(current_match.group(1)) or direct_req
    hard_claimed = _as_int(hard.get("requests_claimed"))
    hard_cap = _as_int(hard.get("request_cap"))
    v2_req = max(_as_int(stats.get("requests")), hard_claimed)
    v2_ctx = max(
        _as_int(stats.get("contexts_added_total")),
        _as_int(stats.get("contexts_added")),
        _as_int(stats.get("hinted_contexts")),
    )
    v2_odds = max(
        _as_int(stats.get("odds_hints")),
        _as_int(stats.get("odds_resources")),
        _as_int(stats.get("odds_comparison_attached")),
    )
    errors = _as_int(stats.get("errors")) or _as_int(stats.get("response_errors"))
    offers = _as_int(bridge.get("bzzoiro_offer_rows"))
    overlap = _as_int(bridge.get("overlap_same_bucket_rows"))
    if not any((v2_req, v2_ctx, v2_odds, offers, overlap)):
        return text
    budget_suffix = ""
    if hard_claimed:
        stop = str(hard.get("last_stop_reason") or "none")
        budget_suffix = f"; hard budget {hard_claimed}/{hard_cap or '?'} stop {stop}"
    line = (
        f"• Bzzoiro: direct req {direct_req}, v2 req {v2_req}; v2 ctx {v2_ctx}; "
        f"v2 odds {v2_odds}; secondary offers {offers}; overlap odds-api.io {overlap}; ошибок {errors}"
        f"{budget_suffix}.\n"
    )
    return re.sub(r"• Bzzoiro: .*?(?:\n|$)", line, text, count=1)


def _autonomous_section(timeout_status: str = "") -> str:
    coverage = _load_json(EXPORT_DIR / "latest-autonomous-coverage-matrix.json")
    latest = _load_json(EXPORT_DIR / "latest-autonomous-accumulation-report.json")
    ledger = _load_any(EXPORT_DIR / "latest-autonomous-prediction-ledger.json")
    summary = coverage.get("summary") if isinstance(coverage.get("summary"), dict) else {}
    if timeout_status:
        return (
            "\n🧠 Автономное накопление\n"
            "• Цикл candidate/quality не завершён: matrix и prediction ledger не обновлены в этом run.\n"
            f"• Причина: {timeout_status}. Значения 0/0 не являются измерением покрытия.\n"
        )
    if not summary and not latest:
        return ""
    rows = len(ledger) if isinstance(ledger, list) else 0
    total = _as_int(summary.get("matches_total"))
    full = _as_int(summary.get("matches_full_2plus_coverage"))
    exact = _as_int(summary.get("matches_with_2plus_exact_odds_sources"))
    contexts = _as_int(summary.get("matches_with_2plus_core_contexts"))
    public_safe = _as_int(latest.get("public_safe"))
    shadow = _as_int(latest.get("shadow_blocked"))
    return (
        "\n🧠 Автономное накопление\n"
        f"• Матрица L3 (2+ exact odds + 2+ books + 2+ core context): {full}/{total}; "
        f"2+ exact odds {exact}; 2+ core context {contexts}.\n"
        f"• Prediction ledger: {rows} строк; public-safe в текущем run {public_safe}; shadow-blocked {shadow}.\n"
    )


def _insert_autonomous_section(text: str, timeout_status: str = "") -> str:
    section = _autonomous_section(timeout_status)
    if not section or "🧠 Автономное накопление" in text:
        return text
    marker = "\n🔗 GitHub Actions"
    if marker in text:
        return text.replace(marker, section + marker, 1)
    return text.rstrip() + section + "\n"


def _mark_timeout_truth(text: str, timeout_status: str) -> str:
    if not timeout_status:
        return text
    text = re.sub(
        r"🟡 Прогнозов нет:.*?(?:\n|$)",
        "🔴 Основной prediction run не завершён: timeout; свежего результата модели нет.\n",
        text,
        count=1,
    )
    text = re.sub(
        r"• Главная причина:.*?(?:\n|$)",
        "• Главная причина отсутствия свежего результата: run-once превысил лимит времени.\n",
        text,
        count=1,
    )
    warning = (
        "\n⚠️ Достоверность текущего run\n"
        f"• Основной процесс: {timeout_status}.\n"
        "• Кандидаты, line guard и fallback ниже могли быть прочитаны из сохранённых диагностик предыдущего запуска.\n"
        "• Их нельзя считать свежей воронкой текущего run.\n"
    )
    marker = "\n📦 Инвентарь и покрытие"
    if marker in text and "⚠️ Достоверность текущего run" not in text:
        text = text.replace(marker, warning + marker, 1)
    return text


def _install_report_patch(mod) -> None:
    base_render = mod.render

    def render(payload):
        timeout_status = _main_run_timeout_status()
        text = base_render(payload)
        text = _replace_two_plus_contract_text(text)
        text = _replace_bzzoiro_line(text)
        text = _mark_timeout_truth(text, timeout_status)
        text = _insert_autonomous_section(text, timeout_status)
        api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
        sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}
        diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        sport_diag = diag.get("sportlogic") if isinstance(diag.get("sportlogic"), dict) else {}
        enabled = bool(
            sport.get("enabled")
            or sport_diag.get("enabled")
            or _as_int(sport.get("requests"))
            or _as_int(sport_diag.get("requests"))
        )
        if enabled:
            requests = max(_as_int(sport.get("requests")), _as_int(sport_diag.get("requests")))
            fixtures = max(
                _as_int(sport.get("fixtures")),
                _as_int(sport.get("fixtures_fetched")),
                _as_int(sport_diag.get("fixtures_fetched")),
                _as_int(sport_diag.get("games_fetched")),
            )
            matched = max(
                _as_int(sport.get("matched")),
                _as_int(sport_diag.get("matched")),
                _as_int(sport_diag.get("events_matched")),
            )
            odds_req = max(_as_int(sport.get("odds_requests")), _as_int(sport_diag.get("odds_requests")))
            offers = max(_as_int(sport.get("offers")), _as_int(sport_diag.get("offers_parsed")))
            errors = max(_as_int(sport.get("errors")), _as_int(sport_diag.get("response_errors")))
            diagnosis = sport.get("diagnosis") or sport_diag.get("diagnosis") or "n/a"
            line = (
                f"• SportLogic: enabled; запросы {requests}; fixtures {fixtures}; matched {matched}; "
                f"odds req {odds_req}; offers {offers}; ошибок {errors}; diag {diagnosis}.\n"
            )
            text = re.sub(r"• SportLogic: .*?(?:\n|$)", line, text, count=1)
        return text

    mod.render = render
    mod.v9.v8.v7.v5.render = render
    mod.v9.v8.v7.render = render


if __name__ == "__main__":
    _sanitize()
    _refresh_inventory_truth()
    try:
        from scripts.repair_bzzoiro_v2_report_metrics import (
            main as repair_bzzoiro_metrics,
        )

        repair_bzzoiro_metrics()
    except Exception:
        pass
    module = _load_v12()
    _install_report_patch(module)
    raise SystemExit(module.v9.v8.v7.v5.main())
