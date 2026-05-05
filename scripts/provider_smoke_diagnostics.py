from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provider_smoke_all_v2 as smoke

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
ART_FAST_DIR = Path("artifacts/provider-smoke-fast")
ART_DIAG_DIR = Path("artifacts/provider-smoke-diagnostics")
JSON_OUT = OUT_DIR / "latest-provider-smoke-fast.json"
TXT_OUT = OUT_DIR / "latest-provider-smoke-fast.txt"
DIAG_JSON_OUT = OUT_DIR / "latest-provider-smoke-diagnostics.json"
DIAG_TXT_OUT = OUT_DIR / "latest-provider-smoke-diagnostics.txt"

READY_STATUSES = {"OK"}
FIXABLE_WARNING_STATUSES = {"EMPTY", "ENDPOINT_CONFIG", "AUTH_HEADERS", "TIMEOUT"}
BLOCKED_STATUSES = {"MISSING_SECRET", "MISSING_CONFIG", "AUTH", "RATE_LIMIT"}
HARD_ERROR_STATUSES = {"ERROR", "HTTP_ERROR"}

INTEGRATION_ROLE = {
    "odds": "price_source",
    "context": "football_context",
    "weather": "weather_context",
    "news": "news_context",
    "mapping": "mapping_aliases",
    "csv": "historical_csv",
    "rapidapi": "rapidapi_probe",
}


def _status_rank(status: str) -> int:
    if status in READY_STATUSES:
        return 0
    if status in FIXABLE_WARNING_STATUSES:
        return 1
    if status in BLOCKED_STATUSES:
        return 2
    if status in HARD_ERROR_STATUSES:
        return 3
    return 2


def _weakness_for(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    reason = str(row.get("reason") or "")
    body = str(row.get("body_preview") or "")
    params = row.get("params") or {}
    note = str(row.get("note") or "")
    if status == "OK":
        rows = int(row.get("rows_count") or 0)
        if rows <= 1:
            return "working_but_low_rows"
        return "ready_for_adapter_or_runtime_mapping"
    if status == "MISSING_SECRET":
        return "secret_missing"
    if status == "MISSING_CONFIG":
        return "base_url_or_required_config_missing"
    if status == "AUTH_HEADERS":
        return "auth_header_contract_unknown"
    if status == "AUTH":
        return "bad_key_or_wrong_auth_scheme"
    if status == "RATE_LIMIT":
        return "quota_exhausted_or_plan_too_small"
    if status == "ENDPOINT_CONFIG":
        return "wrong_endpoint_path_or_required_params"
    if status == "TIMEOUT":
        return "slow_or_unstable_provider"
    if status == "EMPTY":
        if params:
            return "query_too_narrow_or_empty_date_window"
        return "empty_payload_shape_or_no_accessible_data"
    if status in {"ERROR", "HTTP_ERROR"}:
        if "ssl" in (reason + body).lower():
            return "tls_or_provider_edge_error"
        if "json" in (reason + body).lower():
            return "unexpected_payload_format"
        return "runtime_or_http_failure"
    if "endpoint" in (reason + body + note).lower():
        return "wrong_endpoint_path_or_required_params"
    return "unclassified"


def _recommendation_for(row: dict[str, Any]) -> str:
    weakness = _weakness_for(row)
    provider = str(row.get("provider") or "")
    group = str(row.get("group") or "")
    if weakness == "ready_for_adapter_or_runtime_mapping":
        if group == "odds":
            return "Можно готовить odds-adapter, но перед публикацией считать exact price sources/bookmakers и timestamp."
        if group == "context":
            return "Можно интегрировать как контекстный источник; не засчитывать в подтверждение цены."
        if group == "weather":
            return "Можно интегрировать как погодный fallback с кэшем по стадиону/городу и часу матча."
        if group in {"mapping", "csv"}:
            return "Интегрировать через daily cache, не дергать на каждый матч."
        if group == "news":
            return "Интегрировать как news fallback с маленьким per-run лимитом и кэшем."
        return "Источник отвечает; следующий шаг — адаптер и маппинг payload."
    if weakness == "working_but_low_rows":
        return "Источник отвечает, но строк мало; проверить query params, дату, спорт/лигу и доступность free-плана."
    if weakness == "secret_missing":
        return "Добавить правильный GitHub Secret или alias env; без ключа интеграцию не начинать."
    if weakness == "base_url_or_required_config_missing":
        return "Добавить base URL/path из dashboard/docs в GitHub Secrets или прописать безопасный default."
    if weakness == "auth_header_contract_unknown":
        return "Открыть docs/dashboard провайдера и уточнить обязательные headers; smoke уже пробует common variants."
    if weakness == "bad_key_or_wrong_auth_scheme":
        return "Проверить валидность ключа, имя header, Bearer/Token scheme и активность подписки."
    if weakness == "quota_exhausted_or_plan_too_small":
        return "Сделать fallback-only, снизить частоту или дождаться reset/сменить тариф."
    if weakness == "wrong_endpoint_path_or_required_params":
        if "rapidapi" in provider:
            return "В RapidAPI playground взять рабочий endpoint и прописать *_RAPIDAPI_HOST / *_RAPIDAPI_PATH."
        return "Проверить endpoint/path и обязательные параметры; добавить второй probe с альтернативным route."
    if weakness == "slow_or_unstable_provider":
        return "Увеличить timeout, добавить retry/cache и не делать источник критичным для ранa."
    if weakness == "query_too_narrow_or_empty_date_window":
        return "Добавить broad fallback или расширить дату/статус/league filters; не считать источник сломанным."
    if weakness == "empty_payload_shape_or_no_accessible_data":
        return "Проверить права free-плана и payload keys; возможно нужен другой endpoint."
    if weakness == "unexpected_payload_format":
        return "Сохранить sample payload и расширить parser/row extractor."
    if weakness == "tls_or_provider_edge_error":
        return "Проверить домен/base URL и TLS; попробовать официальный http/https endpoint из docs."
    return "Проверить body_preview/sample и добавить точный диагностический probe."


def _integration_status(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "not_tested"
    statuses = [str(row.get("status") or "") for row in rows]
    if all(status == "OK" for status in statuses):
        return "ready"
    if any(status in {"OK", "EMPTY"} for status in statuses) and any(status in FIXABLE_WARNING_STATUSES for status in statuses):
        return "usable_with_fallback_or_parser_work"
    if any(status in {"MISSING_SECRET", "MISSING_CONFIG"} for status in statuses):
        return "blocked_by_configuration"
    if any(status == "RATE_LIMIT" for status in statuses):
        return "blocked_by_quota"
    if any(status in {"AUTH", "AUTH_HEADERS"} for status in statuses):
        return "blocked_by_auth_contract"
    if any(status in HARD_ERROR_STATUSES for status in statuses):
        return "blocked_by_runtime_error"
    return "needs_endpoint_or_query_tuning"


def _aggregate(results: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[str(row.get("provider") or "unknown")].append(row)

    providers: list[dict[str, Any]] = []
    for provider, rows in sorted(grouped.items()):
        status_counts = Counter(str(row.get("status") or "") for row in rows)
        best = sorted(rows, key=lambda item: _status_rank(str(item.get("status") or "")))[0]
        weakest = sorted(rows, key=lambda item: _status_rank(str(item.get("status") or "")), reverse=True)[0]
        ok_runs = status_counts.get("OK", 0)
        tested_runs = len(rows)
        rows_counts = [int(row.get("rows_count") or 0) for row in rows]
        durations = [float(row.get("duration_ms") or 0.0) for row in rows]
        weakness_rows = []
        for row in rows:
            weakness_rows.append(
                {
                    "status": row.get("status"),
                    "weakness": _weakness_for(row),
                    "recommendation": _recommendation_for(row),
                    "http_status": row.get("http_status"),
                    "rows_count": row.get("rows_count"),
                    "reason": row.get("reason"),
                    "url": row.get("url"),
                    "params": row.get("params"),
                    "body_preview": row.get("body_preview"),
                }
            )
        providers.append(
            {
                "provider": provider,
                "group": best.get("group"),
                "role": INTEGRATION_ROLE.get(str(best.get("group") or ""), "unknown"),
                "integration_status": _integration_status(rows),
                "tested_runs": tested_runs,
                "requested_repeats": repeats,
                "ok_runs": ok_runs,
                "stability_pct": round((ok_runs / tested_runs) * 100.0, 1) if tested_runs else 0.0,
                "status_counts": dict(status_counts),
                "max_rows": max(rows_counts) if rows_counts else 0,
                "min_rows": min(rows_counts) if rows_counts else 0,
                "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0.0,
                "primary_weakness": _weakness_for(weakest),
                "primary_recommendation": _recommendation_for(weakest),
                "sample": best.get("sample"),
                "attempts": weakness_rows,
            }
        )
    return {
        "providers_total": len(providers),
        "ready": sum(1 for p in providers if p["integration_status"] == "ready"),
        "usable_with_work": sum(1 for p in providers if p["integration_status"] == "usable_with_fallback_or_parser_work"),
        "blocked": sum(1 for p in providers if str(p["integration_status"]).startswith("blocked")),
        "needs_tuning": sum(1 for p in providers if p["integration_status"] == "needs_endpoint_or_query_tuning"),
        "providers": providers,
    }


def _render_diagnostics(payload: dict[str, Any]) -> str:
    diag = payload["diagnostics"]
    lines = [
        "🧪 Provider Smoke Diagnostics",
        f"• UTC: {payload.get('created_at_utc')}",
        f"• providers: {payload.get('providers_arg')}",
        f"• repeats: {payload.get('repeats')}",
        f"• duration: {payload.get('duration_seconds')}s / limit {payload.get('max_seconds')}s",
        f"• providers total: {diag['providers_total']} | ready {diag['ready']} | usable_with_work {diag['usable_with_work']} | blocked {diag['blocked']} | needs_tuning {diag['needs_tuning']}",
        "",
        "📡 Provider weak spots",
    ]
    for item in diag["providers"]:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(item["status_counts"].items()))
        lines.append(
            f"• [{item['group']}/{item['role']}] {item['provider']}: {item['integration_status']} | "
            f"stable={item['stability_pct']}% | rows={item['min_rows']}-{item['max_rows']} | {counts}"
        )
        lines.append(f"  слабое место: {item['primary_weakness']}")
        lines.append(f"  что делать: {item['primary_recommendation']}")
        first = item["attempts"][0] if item.get("attempts") else {}
        if first.get("reason") and item["integration_status"] != "ready":
            lines.append(f"  reason: {first.get('reason')}")
        if first.get("url") and item["integration_status"] != "ready":
            lines.append(f"  url: {first.get('url')}")
    lines += ["", "📁 Attach these files to ChatGPT:", str(DIAG_JSON_OUT), str(DIAG_TXT_OUT), str(JSON_OUT), str(TXT_OUT)]
    return "\n".join(lines)


def _write_all(payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ART_FAST_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIAG_DIR.mkdir(parents=True, exist_ok=True)
    diag_text = _render_diagnostics(payload)
    raw_smoke_text = smoke._render_text(payload["raw_smoke_payload"])

    JSON_OUT.write_text(json.dumps(payload["raw_smoke_payload"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(raw_smoke_text + "\n", encoding="utf-8")
    DIAG_JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    DIAG_TXT_OUT.write_text(diag_text + "\n", encoding="utf-8")

    (ART_FAST_DIR / "provider-smoke-fast.json").write_text(JSON_OUT.read_text(encoding="utf-8"), encoding="utf-8")
    (ART_FAST_DIR / "provider-smoke-fast.txt").write_text(TXT_OUT.read_text(encoding="utf-8"), encoding="utf-8")
    (ART_FAST_DIR / "provider-smoke-diagnostics.json").write_text(DIAG_JSON_OUT.read_text(encoding="utf-8"), encoding="utf-8")
    (ART_FAST_DIR / "provider-smoke-diagnostics.txt").write_text(DIAG_TXT_OUT.read_text(encoding="utf-8"), encoding="utf-8")

    (ART_DIAG_DIR / "provider-smoke-diagnostics.json").write_text(DIAG_JSON_OUT.read_text(encoding="utf-8"), encoding="utf-8")
    (ART_DIAG_DIR / "provider-smoke-diagnostics.txt").write_text(DIAG_TXT_OUT.read_text(encoding="utf-8"), encoding="utf-8")


def _list_providers() -> str:
    probes = smoke.build_probes()
    lines = ["Available provider probes:"]
    for probe in sorted(probes, key=lambda p: (p.group, p.name)):
        lines.append(f"- {probe.name} [{probe.group}] {probe.url}")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    if args.list_providers:
        print(_list_providers())
        return 0

    started = time.perf_counter()
    probes = smoke._select(smoke.build_probes(), args.providers)
    timeout = httpx.Timeout(float(args.timeout), connect=min(5.0, float(args.timeout)))
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for run_index in range(max(1, int(args.repeats))):
            tasks = [smoke._run_probe(client, sem, probe) for probe in probes]
            try:
                batch = await asyncio.wait_for(asyncio.gather(*tasks), timeout=float(args.max_seconds))
            except asyncio.TimeoutError:
                batch = []
            for row in batch:
                row["diagnostic_run_index"] = run_index + 1
                results.append(row)

    duration = round(time.perf_counter() - started, 2)
    raw_smoke_payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_smoke_fast_diagnostics_raw",
        "providers_arg": args.providers,
        "max_seconds": args.max_seconds,
        "timeout_seconds": args.timeout,
        "duration_seconds": duration,
        "summary": smoke._summary(results),
        "results": results,
    }
    payload = {
        "created_at_utc": raw_smoke_payload["created_at_utc"],
        "mode": "provider_smoke_diagnostics",
        "providers_arg": args.providers,
        "repeats": max(1, int(args.repeats)),
        "max_seconds": args.max_seconds,
        "timeout_seconds": args.timeout,
        "duration_seconds": duration,
        "raw_smoke_payload": raw_smoke_payload,
        "diagnostics": _aggregate(results, max(1, int(args.repeats))),
    }
    _write_all(payload)
    print(_render_diagnostics(payload))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provider smoke diagnostics with weak-spot report")
    parser.add_argument("--providers", default=os.getenv("PROVIDER_SMOKE_FAST_PROVIDERS", "all"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT", "18")))
    parser.add_argument("--max-seconds", type=float, default=float(os.getenv("PROVIDER_SMOKE_FAST_MAX_SECONDS", "180")))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PROVIDER_SMOKE_FAST_CONCURRENCY", "8")))
    parser.add_argument("--repeats", type=int, default=int(os.getenv("PROVIDER_SMOKE_REPEATS", "1")))
    parser.add_argument("--list-providers", action="store_true", default=os.getenv("PROVIDER_SMOKE_LIST_PROVIDERS", "").lower() in {"1", "true", "yes"})
    return parser.parse_args()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
