"""HARIZON report v13: verified coverage and fresh runtime diagnostics."""

from __future__ import annotations

import importlib.util
import json
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
        return int(float(value))
    except Exception:
        return 0


def _refresh_truth() -> None:
    steps = (
        ("scripts.repair_day_inventory_blank_rows", "main"),
        ("scripts.build_day_inventory_coverage_truth", "main"),
        ("scripts.day_inventory_cumulative_coverage", "main"),
    )
    for module_name, name in steps:
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
    return {
        "total": total,
        "line1": _int(counts.get("matches_with_odds")),
        "context1": _int(counts.get("matches_with_context")),
        "books2": _int(counts.get("matches_with_2plus_price_confirmations")),
        "odds2": _int(counts.get("matches_with_2plus_odds_sources")),
        "context2": _int(counts.get("matches_with_2plus_context_sources")),
        "model": _int(counts.get("matches_ready_for_model")),
        "a": _int(counts.get("matches_a_tier_coverage_ready")),
        "b": _int(counts.get("matches_b_tier_watch_ready"))
        or _int(counts.get("matches_a_tier_coverage_ready")),
    }


def _pct(value: int, total: int) -> int:
    if not total:
        return 0
    if value >= total:
        return 100
    return int(100 * value / total)


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
    c = _verified_counts()
    total = c["total"]
    text = _replace_line(
        text,
        r"1\+ линия:",
        f"• 1+ линия: {c['line1']}/{total} ({_pct(c['line1'], total)}%) | "
        f"1+ контекст: {c['context1']}/{total} ({_pct(c['context1'], total)}%)",
    )
    text = _replace_line(
        text,
        r"2\+ букмекера:",
        f"• 2+ букмекера: {c['books2']}/{total} ({_pct(c['books2'], total)}%)",
    )
    for pattern in (r"2\+ independent odds-source:", r"2\+ независимых источника линий:"):
        text = _replace_line(
            text,
            pattern,
            f"• 2+ независимых источника линий: {c['odds2']}/{total} "
            f"({_pct(c['odds2'], total)}%) — strict metric для A/B-tier.",
        )
    for pattern in (r"2\+ контекста:", r"2\+ независимых контекста:"):
        text = _replace_line(
            text,
            pattern,
            f"• 2+ независимых контекста: {c['context2']}/{total} ({_pct(c['context2'], total)}%)",
        )
    text = _replace_line(
        text,
        r"Готово для модели:",
        f"• Готово для модели: {c['model']}/{total} ({_pct(c['model'], total)}%)",
    )
    text = _replace_ready_line(text, r"A-tier strict-ready:", "A-tier coverage-ready", c["a"], total)
    text = _replace_ready_line(text, r"A-tier coverage-ready:", "A-tier coverage-ready", c["a"], total)
    text = _replace_ready_line(text, r"B-tier .*coverage:", "B-tier coverage-ready", c["b"], total)
    text = _replace_ready_line(text, r"B-tier coverage-ready:", "B-tier coverage-ready", c["b"], total)
    text = re.sub(
        r"^• A-cover 2\+ odds-source ∩ 2\+ букмекера ∩ 2\+ контекста:.*$",
        f"• Полное покрытие 2 линии ∩ 2 букмекера ∩ 2 контекста: "
        f"{c['a']}/{total}; B-cover: {c['b']}/{total}.",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    marker = "\n🏷️ A/B-tier публикация"
    if marker in text and "Покрытие считается только" not in text:
        shortfall = max(0, total - c["a"])
        text = text.replace(
            marker,
            "\n• Покрытие считается только по сохранённым ответам независимых API; "
            "fixture-id, alias и proxy не засчитываются."
            f" До полного 300/300 осталось: {shortfall}.\n" + marker,
            1,
        )
    return text


def _payload_time(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for key in ("created_at_utc", "updated_at_utc", "created_at", "updated_at"):
        raw = str(payload.get(key) or "").strip()
        if not raw:
            continue
        try:
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            return created.astimezone(UTC)
        except Exception:
            continue
    return None


def _fresh_runtime_payload(payload: Any, *, max_age_minutes: int = 90) -> bool:
    created = _payload_time(payload)
    if created is None:
        return False
    age = datetime.now(UTC) - created
    return timedelta(0) <= age <= timedelta(minutes=max_age_minutes)


def _sportlogic_runtime_evidence(payload: Any = None) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("status") != "run_failed":
        api = payload.get("api")
        sport = api.get("sportlogic") if isinstance(api, dict) else {}
        if isinstance(sport, dict) and (
            bool(sport.get("enabled")) or _int(sport.get("requests")) > 0
        ):
            return {
                "requests": _int(sport.get("requests")),
                "fixtures": _int(sport.get("fixtures")),
                "matched": _int(sport.get("matched")),
                "odds_requests": _int(sport.get("odds_requests")),
                "offers": _int(sport.get("offers")),
                "errors": _int(sport.get("errors")),
                "diagnosis": str(
                    sport.get("diagnosis") or "runtime_enabled"
                ),
            }
    probe = _load(EXPORT / "latest-sportlogic-coverage-probe.json", {})
    if not _fresh_runtime_payload(probe):
        return {}
    debug = _load(EXPORT / "latest-sportlogic-debug.json", {})
    stats = debug.get("stats") if isinstance(debug, dict) and isinstance(debug.get("stats"), dict) else {}
    requests = max(_int(stats.get("requests")), _int(probe.get("requests")))
    statuses = stats.get("http_statuses") if isinstance(stats.get("http_statuses"), list) else probe.get("http_statuses")
    if not (_int(stats.get("enabled")) > 0 or requests > 0 or bool(statuses)):
        return {}
    return {
        "requests": requests,
        "fixtures": max(
            _int(stats.get("fixtures_fetched")),
            _int(stats.get("games_fetched")),
            _int(probe.get("current_games")),
        ),
        "matched": max(_int(stats.get("events_matched")), _int(probe.get("matched_games"))),
        "odds_requests": _int(stats.get("odds_requests")),
        "offers": _int(stats.get("offers_parsed")),
        "errors": _int(stats.get("response_errors")),
        "diagnosis": str(stats.get("diagnosis") or probe.get("diagnosis") or "runtime_enabled"),
    }


def _repair_sportlogic_runtime_line(text: str, payload: Any = None) -> str:
    evidence = _sportlogic_runtime_evidence(payload)
    if not evidence:
        return text
    replacement = (
        "• SportLogic: enabled_runtime; "
        f"запросы {evidence['requests']}; fixtures {evidence['fixtures']}; "
        f"matched {evidence['matched']}; odds req {evidence['odds_requests']}; "
        f"offers {evidence['offers']}; ошибок {evidence['errors']}; "
        f"diag {evidence['diagnosis']}."
    )
    return re.sub(r"^• SportLogic:.*$", replacement, text, count=1, flags=re.MULTILINE)


def _bzzoiro_runtime_evidence() -> dict[str, int]:
    payload = _load(EXPORT / "latest-sstats-bzzoiro-odds-merge.json", {})
    if not _fresh_runtime_payload(payload):
        return {}
    bzz = payload.get("bzzoiro") if isinstance(payload.get("bzzoiro"), dict) else {}
    primary = bzz.get("v2_primary") if isinstance(bzz.get("v2_primary"), dict) else bzz
    evidence = {
        "offers": max(
            _int(bzz.get("offers_added_to_pool")),
            _int(bzz.get("offers_parsed")),
            _int(primary.get("offers_from_best")),
            _int(primary.get("offers_parsed")),
        ),
        "matches": max(_int(bzz.get("matches_with_offers")), _int(bzz.get("cached_matches"))),
        "requests": max(_int(primary.get("requests")), _int(primary.get("odds_best_requests"))),
        "rows": _int(primary.get("odds_best_rows")),
        "errors": max(_int(primary.get("response_errors")), _int(bzz.get("response_errors"))),
        "two_plus": _int(payload.get("after_2plus_sources")),
    }
    return evidence if any(evidence.values()) else {}


def _repair_bzzoiro_runtime_lines(text: str) -> str:
    evidence = _bzzoiro_runtime_evidence()
    if not evidence:
        return text

    def provider_replacement(match: re.Match[str]) -> str:
        prefix = match.group(1).rstrip("; ")
        return (
            f"• Bzzoiro: {prefix}; batch odds rows {evidence['rows']}; "
            f"matches with offers {evidence['matches']}; secondary offers {evidence['offers']}; "
            f"2+ source matches {evidence['two_plus']}; ошибок {evidence['errors']}."
        )

    text = re.sub(
        r"^• Bzzoiro: (.*?)(?:; v2 odds \d+; secondary offers \d+; "
        r"overlap odds-api\.io \d+; ошибок \d+\.)$",
        provider_replacement,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    diagnostic = (
        f"• Bzzoiro runtime merge: offers {evidence['offers']}; "
        f"matches with offers {evidence['matches']}; 2+ source matches {evidence['two_plus']}; "
        f"batch rows {evidence['rows']}; requests {evidence['requests']}; errors {evidence['errors']}."
    )
    return re.sub(
        r"^• Bzzoiro overlap bridge:.*$",
        diagnostic,
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _sstats_deep_runtime_evidence() -> dict[str, Any]:
    prepare = _load(EXPORT / "latest-runbot-discovery-first-prepare.json", {})
    steps = prepare.get("steps") if isinstance(prepare.get("steps"), list) else []
    step = next(
        (
            row
            for row in steps
            if isinstance(row, dict) and row.get("name") == "apply_sstats_deep_inventory_enrichment_v4"
        ),
        {},
    )
    step_status = str(step.get("status") or "").strip().lower()
    report = _load(EXPORT / "latest-sstats-deep-inventory-enrichment.json", {})
    fresh_report = _fresh_runtime_payload(report)
    if step_status == "ok" and fresh_report:
        return {"deep_enriched": _int(report.get("enriched_matches")), "status": "fresh"}
    if step_status:
        return {"deep_enriched": 0, "status": f"current_run_{step_status}"}
    if fresh_report:
        return {"deep_enriched": _int(report.get("enriched_matches")), "status": "fresh_untracked"}
    return {"deep_enriched": 0, "status": "no_fresh_report"}


def _normalize_sstats_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    evidence = _sstats_deep_runtime_evidence()
    api = payload.setdefault("api", {})
    if not isinstance(api, dict):
        return
    sstats = dict(api.get("sstats") or {})
    sstats["deep_enriched"] = _int(evidence.get("deep_enriched"))
    sstats["deep_status"] = str(evidence.get("status") or "")
    api["sstats"] = sstats


def _repair_sstats_runtime_line(text: str) -> str:
    evidence = _sstats_deep_runtime_evidence()
    count = _int(evidence.get("deep_enriched"))
    status = str(evidence.get("status") or "")
    note = ""
    if status == "current_run_skipped":
        note = " (текущий deep-step пропущен)"
    elif status == "current_run_error":
        note = " (ошибка текущего deep-step)"
    elif status == "no_fresh_report":
        note = " (нет свежего deep-report)"
    return re.sub(
        r"^(• SStats: .*?; deep-enriched )\d+(?: \([^\n;]*\))?(; team-form .*)$",
        rf"\g<1>{count}{note}\g<2>",
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _movement_runtime_evidence() -> dict[str, int]:
    payload = _load(EXPORT / "latest-line-movement-guard-report.json", {})
    if not _fresh_runtime_payload(payload):
        return {}
    waiting_reason_sets: list[list[str]] = []
    sampled_dropped = 0
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    for file_row in files:
        dropped = file_row.get("dropped_sample") if isinstance(file_row, dict) else []
        for candidate in dropped if isinstance(dropped, list) else []:
            sampled_dropped += 1
            guard = candidate.get("guard") if isinstance(candidate, dict) else {}
            reasons = guard.get("reasons") if isinstance(guard, dict) else []
            normalized = [str(reason) for reason in reasons] if isinstance(reasons, list) else []
            if "needs_next_cron_line_movement_recheck" in normalized:
                waiting_reason_sets.append(normalized)
    dropped_total = max(_int(payload.get("candidates_dropped")), sampled_dropped)
    waiting_total = len(waiting_reason_sets)
    movement_only = sum(reasons == ["needs_next_cron_line_movement_recheck"] for reasons in waiting_reason_sets)
    with_other = max(0, waiting_total - movement_only)
    removed_after_snapshot = max(0, dropped_total - waiting_total)
    if dropped_total <= 0:
        return {}
    return {
        "dropped_total": dropped_total,
        "waiting_total": waiting_total,
        "movement_only": movement_only,
        "with_other": with_other,
        "removed_after_snapshot": removed_after_snapshot,
    }


def _repair_movement_runtime_lines(text: str) -> str:
    evidence = _movement_runtime_evidence()
    if not evidence:
        return text
    waiting = evidence["waiting_total"]
    movement_only = evidence["movement_only"]
    with_other = evidence["with_other"]
    removed = evidence["removed_after_snapshot"]
    headline = (
        f"• Главная причина: второй снимок линии отсутствует у {waiting}; "
        f"только {movement_only} блокируются исключительно ожиданием, "
        f"у {with_other} есть дополнительные EV/edge-блокеры; "
        f"ещё {removed} сняты после имеющегося снимка"
    )
    text = re.sub(
        r"^• Главная причина: (?:кандидаты ждут следующий cron для второго снимка линии \(\d+\)|"
        r"второй снимок линии отсутствует у .*?)$",
        headline,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^• (?:кандидат ждёт следующий cron для|ожидание) второго снимка линии:.*$",
        f"• ожидание второго снимка линии: {waiting}; movement-only {movement_only}; "
        f"также ниже EV/edge {with_other}; после снимка снято {removed}.",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^(• Line guard: увидел \d+, оставил \d+, отложил )\d+"
        r"(?: до следующего cron)?(?:, снял \d+)?$",
        rf"\g<1>{waiting}, снял {removed}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^• (?:Есть кандидат по bookmaker-contract, но нужен второй снимок линии\. "
        r"Ждём следующий регулярный run\.|У \d+ кандидатов единственный текущий стопор.*)$",
        f"• У {movement_only} кандидатов единственный текущий стопор — второй снимок линии; "
        f"у {with_other} одновременно не пройдены EV/edge floors; "
        f"ещё {removed} сняты после доступного снимка.",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    return text


def _install(module: Any) -> None:
    base_render = module.render

    def render(payload: Any) -> str:
        _normalize_sstats_payload(payload)
        text = _render_verified(base_render(payload))
        text = _repair_sportlogic_runtime_line(text, payload)
        text = _repair_bzzoiro_runtime_lines(text)
        text = _repair_sstats_runtime_line(text)
        return _repair_movement_runtime_lines(text)

    module.render = render
    try:
        module.v9.v8.v7.v5.render = render
        module.v9.v8.v7.render = render
    except Exception:
        pass


if __name__ == "__main__":
    from scripts.send_harizon_telegram_run_report_v14 import main as v14_main

    raise SystemExit(v14_main())
