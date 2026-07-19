from __future__ import annotations

"""HARIZON report v13: render only verified per-match coverage counts."""

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


def _load_v12():
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
    line = (
        f"• 1+ линия: {c['line1']}/{total} ({_pct(c['line1'], total)}%) | "
        f"1+ контекст: {c['context1']}/{total} ({_pct(c['context1'], total)}%)"
    )
    text = _replace_line(text, r"1\+ линия:", line)
    text = _replace_line(
        text,
        r"2\+ букмекера:",
        f"• 2+ букмекера: {c['books2']}/{total} ({_pct(c['books2'], total)}%)",
    )
    text = _replace_line(
        text,
        r"2\+ independent odds-source:",
        f"• 2+ независимых источника линий: {c['odds2']}/{total} "
        f"({_pct(c['odds2'], total)}%) — strict metric для A/B-tier.",
    )
    text = _replace_line(
        text,
        r"2\+ независимых источника линий:",
        f"• 2+ независимых источника линий: {c['odds2']}/{total} "
        f"({_pct(c['odds2'], total)}%) — strict metric для A/B-tier.",
    )
    text = _replace_line(
        text,
        r"2\+ контекста:",
        f"• 2+ независимых контекста: {c['context2']}/{total} ({_pct(c['context2'], total)}%)",
    )
    text = _replace_line(
        text,
        r"2\+ независимых контекста:",
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
        note = (
            "\n• Покрытие считается только по сохранённым ответам независимых API; "
            "fixture-id, alias и proxy не засчитываются."
            f" До полного 300/300 осталось: {shortfall}.\n"
        )
        text = text.replace(marker, note + marker, 1)
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


def _sportlogic_runtime_evidence() -> dict[str, Any]:
    probe = _load(EXPORT / "latest-sportlogic-coverage-probe.json", {})
    if not _fresh_runtime_payload(probe):
        return {}
    debug = _load(EXPORT / "latest-sportlogic-debug.json", {})
    stats = debug.get("stats") if isinstance(debug, dict) and isinstance(debug.get("stats"), dict) else {}
    requests = max(_int(stats.get("requests")), _int(probe.get("requests")))
    statuses = stats.get("http_statuses") if isinstance(stats.get("http_statuses"), list) else probe.get("http_statuses")
    enabled = _int(stats.get("enabled")) > 0 or requests > 0 or bool(statuses)
    if not enabled:
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


def _repair_sportlogic_runtime_line(text: str) -> str:
    evidence = _sportlogic_runtime_evidence()
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
    offers = max(
        _int(bzz.get("offers_added_to_pool")),
        _int(bzz.get("offers_parsed")),
        _int(primary.get("offers_from_best")),
        _int(primary.get("offers_parsed")),
    )
    matches = max(_int(bzz.get("matches_with_offers")), _int(bzz.get("cached_matches")))
    requests = max(_int(primary.get("requests")), _int(primary.get("odds_best_requests")))
    rows = _int(primary.get("odds_best_rows"))
    errors = max(_int(primary.get("response_errors")), _int(bzz.get("response_errors")))
    two_plus = _int(payload.get("after_2plus_sources"))
    if not any((offers, matches, requests, rows, errors, two_plus)):
        return {}
    return {
        "offers": offers,
        "matches": matches,
        "requests": requests,
        "rows": rows,
        "errors": errors,
        "two_plus": two_plus,
    }


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
    text = re.sub(
        r"^• Bzzoiro overlap bridge:.*$",
        diagnostic,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    return text


def _movement_runtime_evidence() -> dict[str, int]:
    payload = _load(EXPORT / "latest-line-movement-guard-report.json", {})
    if not _fresh_runtime_payload(payload):
        return {}
    reason_sets: list[list[str]] = []
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    for file_row in files:
        dropped = file_row.get("dropped_sample") if isinstance(file_row, dict) else []
        for candidate in dropped if isinstance(dropped, list) else []:
            guard = candidate.get("guard") if isinstance(candidate, dict) else {}
            reasons = guard.get("reasons") if isinstance(guard, dict) else []
            if isinstance(reasons, list) and "needs_next_cron_line_movement_recheck" in reasons:
                reason_sets.append([str(reason) for reason in reasons])
    total = max(_int(payload.get("candidates_dropped")), len(reason_sets))
    movement_only = sum(reasons == ["needs_next_cron_line_movement_recheck"] for reasons in reason_sets)
    with_other = sum(len(reasons) > 1 for reasons in reason_sets)
    if total <= 0:
        return {}
    return {"total": total, "movement_only": movement_only, "with_other": with_other}


def _repair_movement_runtime_lines(text: str) -> str:
    evidence = _movement_runtime_evidence()
    if not evidence:
        return text
    total = evidence["total"]
    movement_only = evidence["movement_only"]
    with_other = evidence["with_other"]
    headline = (
        f"• Главная причина: второй снимок линии отсутствует у {total}; "
        f"только {movement_only} блокируются исключительно ожиданием, "
        f"у {with_other} есть дополнительные EV/edge-блокеры"
    )
    text = re.sub(
        r"^• Главная причина: кандидаты ждут следующий cron для второго снимка линии \(\d+\)$",
        headline,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^• кандидат ждёт следующий cron для второго снимка линии: \d+ \(\d+%\)$",
        f"• ожидание второго снимка линии: {total}; movement-only {movement_only}; "
        f"также ниже EV/edge {with_other}.",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^(• Line guard: увидел \d+, оставил \d+, отложил )\d+( до следующего cron)$",
        rf"\g<1>{total}\g<2>; movement-only {movement_only}, с другими блокерами {with_other}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^• Есть кандидат по bookmaker-contract, но нужен второй снимок линии\. "
        r"Ждём следующий регулярный run\.$",
        f"• У {movement_only} кандидатов единственный текущий стопор — второй снимок линии; "
        f"у остальных {with_other} одновременно не пройдены EV/edge floors.",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    return text


def _install(module) -> None:
    base_render = module.render

    def render(payload):
        text = _render_verified(base_render(payload))
        text = _repair_sportlogic_runtime_line(text)
        text = _repair_bzzoiro_runtime_lines(text)
        return _repair_movement_runtime_lines(text)

    module.render = render
    try:
        module.v9.v8.v7.v5.render = render
        module.v9.v8.v7.render = render
    except Exception:
        pass


if __name__ == "__main__":
    _refresh_truth()
    module = _load_v12()
    _install(module)
    raise SystemExit(module.v9.v8.v7.v5.main())
