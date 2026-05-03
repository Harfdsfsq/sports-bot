from __future__ import annotations

"""Fast provider smoke run.

This diagnostic entrypoint intentionally does not publish predictions and does
not mutate the betting ledger. It loads the normal runtime settings, collects a
small match sample, probes provider methods with tight limits, and exports a
compact machine-readable + Telegram-friendly report.
"""

import argparse
import asyncio
import json
import os
import traceback
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
ARTIFACT_DIR = ROOT / "artifacts" / "provider-smoke"
JSON_OUT = EXPORT_DIR / "latest-provider-smoke.json"
TXT_OUT = EXPORT_DIR / "latest-provider-smoke.txt"

PROVIDER_GROUPS: dict[str, list[str]] = {
    "all": [
        "odds_api_io",
        "sportlogic",
        "bookies_api",
        "oddspapi",
        "allsportsapi",
        "sstats",
        "bzzoiro",
        "api_football",
        "espn",
        "football_data",
        "thesportsdb",
        "openligadb",
        "openfootball",
        "futrixmetrics",
        "newsapi",
        "gnews",
        "weather",
    ],
    "core": ["odds_api_io", "sportlogic", "sstats", "bzzoiro", "football_data", "thesportsdb", "espn"],
    "odds": ["odds_api_io", "sportlogic", "bookies_api", "oddspapi", "allsportsapi"],
    "context": ["sstats", "bzzoiro", "api_football", "espn", "football_data", "thesportsdb", "openligadb", "openfootball", "futrixmetrics", "newsapi", "gnews", "weather"],
    "sportlogic": ["sportlogic"],
}

OFFER_PROVIDERS = {"odds_api_io", "bookies_api", "oddspapi", "allsportsapi", "sportlogic"}
CONTEXT_PROVIDERS = {"sstats", "bzzoiro", "api_football", "espn", "football_data", "thesportsdb", "openligadb", "openfootball", "futrixmetrics", "newsapi", "gnews", "sportlogic", "weather"}

SECRET_MARKERS = ("key", "token", "secret", "authorization", "password", "apikey", "api_key")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def safe_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 7:
        return "..."
    if is_dataclass(value):
        try:
            value = asdict(value)
        except Exception:
            return str(value)[:500]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            low = str(key).lower()
            if any(marker in low for marker in SECRET_MARKERS):
                out[str(key)] = "***"
            else:
                out[str(key)] = sanitize(item, depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value[:20]]
    if isinstance(value, tuple):
        return [sanitize(item, depth + 1) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return value[:1200]
        return value
    try:
        return str(value)[:500]
    except Exception:
        return f"<{type(value).__name__}>"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def data_count(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, dict):
        if not data:
            return 0
        values = list(data.values())
        if values and all(isinstance(value, list) for value in values):
            return sum(len(value) for value in values)
        return len(data)
    if isinstance(data, (list, tuple, set)):
        return len(data)
    return 1


def compact_stats(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    keep = [
        "enabled",
        "api_key_present",
        "credentials_present",
        "requests",
        "event_requests",
        "odds_requests",
        "games_fetched",
        "fixtures_fetched",
        "matches_built",
        "events_fetched",
        "events_matched",
        "contexts_built",
        "offers_parsed",
        "rows_before_parse",
        "odds_payload_rows",
        "empty_odds_payloads",
        "empty_fixture_attempts",
        "budget_exhausted",
        "response_errors",
        "auth_error",
        "rate_limited",
        "cooldown_active",
        "stop_reason",
        "odds_endpoint_used",
        "empty_games_reason",
        "last_body_preview",
    ]
    out = {key: sanitize(stats.get(key)) for key in keep if key in stats}
    for key in ("http_statuses", "event_http_statuses", "odds_http_statuses"):
        if key in stats:
            try:
                out[key] = dict(Counter(stats.get(key) or []))
            except Exception:
                out[key] = sanitize(stats.get(key))
    for key in ("attempted_paths", "fixture_query_attempts", "fixture_dates_requested", "fixture_rows_by_date", "top_level_keys", "payload_shapes", "parse_reject_reasons", "sportlogic_hardening_reject_reasons"):
        if key in stats:
            out[key] = sanitize(stats.get(key))
    return out


def verdict(provider: str, method: str, data: Any, stats: dict[str, Any], error: str | None = None) -> tuple[str, str]:
    if error:
        return "ERROR", error[:220]
    if stats.get("auth_error"):
        return "AUTH", "auth_error=true: проверь ключ/имя header/тариф"
    if stats.get("rate_limited"):
        return "RATE_LIMIT", "rate_limited=true: провайдер ответил 429"
    if stats.get("budget_exhausted") and data_count(data) <= 0:
        return "BUDGET", "бюджет закончился до полезных данных"
    count = data_count(data)
    if count > 0:
        return "OK", f"данные получены: {count}"
    if provider == "sportlogic":
        if safe_int(stats.get("fixtures_fetched"), 0) <= 0 and safe_int(stats.get("games_fetched"), 0) <= 0:
            return "EMPTY_FIXTURES", "SportLogic /games не дал fixtures; смотри attempted_paths/last_body_preview"
        if method == "fetch_offers" and safe_int(stats.get("events_matched"), 0) <= 0:
            return "NO_MATCH", "fixtures есть, но не сматчились с матчами odds_api_io"
        if method == "fetch_offers" and safe_int(stats.get("odds_requests"), 0) <= 0:
            return "NO_ODDS_REQUEST", "не дошёл до odds-запросов"
    if stats.get("response_errors"):
        return "HTTP_ERROR", f"response_errors={stats.get('response_errors')}"
    if method == "fetch_context":
        return "EMPTY_CONTEXT", "контекст не найден для тестовой выборки"
    if method == "fetch_offers":
        return "EMPTY_OFFERS", "линии не найдены для тестовой выборки"
    return "EMPTY", "нет данных"


def provider_secret_presence(provider: str) -> dict[str, bool]:
    mapping = {
        "odds_api_io": ["ODDS_API_IO_KEY", "ODDS_API_IO_KEY_2"],
        "sportlogic": ["SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"],
        "sstats": ["SSTATS_API_KEY"],
        "bzzoiro": ["BZZOIRO_API_KEY"],
        "football_data": ["FOOTBALL_DATA_API_KEY"],
        "thesportsdb": ["THESPORTSDB_API_KEY"],
        "api_football": ["API_FOOTBALL_KEY"],
        "oddspapi": ["ODDSPAPI_API_KEY"],
        "allsportsapi": ["ALLSPORTSAPI_API_KEY"],
        "futrixmetrics": ["FUTRIXMETRICS_API_KEY"],
        "newsapi": ["NEWSAPI_KEY", "CURRENTS_API_KEY", "CURRENTS_KEY"],
        "gnews": ["GNEWS_KEY"],
        "weather": ["WEATHERAPI_KEY", "OPENWEATHERMAP_API_KEY"],
    }
    return {key: bool(os.getenv(key)) for key in mapping.get(provider, [])}


async def maybe_call(provider: Any, method_name: str, *args: Any) -> tuple[Any, dict[str, Any], dict[str, Any], str | None]:
    if provider is None:
        return None, {"enabled": False, "loaded": False}, {}, "provider_not_loaded"
    method = getattr(provider, method_name, None)
    if not callable(method):
        return None, {"enabled": True, "loaded": True}, {}, f"method_missing:{method_name}"
    try:
        result = await method(*args)
        if isinstance(result, tuple) and len(result) == 3:
            data, stats, preview = result
            return data, dict(stats or {}), dict(preview or {}), None
        if isinstance(result, tuple) and len(result) == 2:
            data, stats = result
            return data, dict(stats or {}), {}, None
        return result, {}, {}, None
    except Exception as exc:
        return None, {"exception_type": type(exc).__name__, "traceback": traceback.format_exc()[-3000:]}, {}, f"{type(exc).__name__}: {exc}"


def match_sample_payload(matches: list[Any], limit: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in matches[:limit]:
        out.append({
            "source": getattr(match, "source", ""),
            "event_id": getattr(match, "source_event_id", ""),
            "league": getattr(match, "league_name", ""),
            "home": getattr(match, "home_team", ""),
            "away": getattr(match, "away_team", ""),
            "commence_time": getattr(getattr(match, "commence_time", None), "isoformat", lambda: "")(),
            "match_key": getattr(match, "match_key", ""),
        })
    return out


def resolve_providers(raw: str) -> list[str]:
    requested: list[str] = []
    for item in str(raw or "core").split(","):
        key = item.strip().lower()
        if not key:
            continue
        requested.extend(PROVIDER_GROUPS.get(key, [key]))
    out: list[str] = []
    seen: set[str] = set()
    for provider in requested:
        if provider not in seen:
            seen.add(provider)
            out.append(provider)
    return out


async def run(args: argparse.Namespace) -> dict[str, Any]:
    # These imports intentionally happen after startup/sitecustomize patches.
    from app.config import Settings
    from app.services.runner import PredictionRunner

    settings = Settings()
    runner = PredictionRunner(settings)
    now = datetime.now(UTC)
    providers = resolve_providers(args.providers)

    bootstrap_error: str | None = None
    bootstrap_meta: dict[str, Any] = {}
    matches: list[Any] = []
    try:
        raw_matches, raw_meta = await runner._fetch_matches()  # noqa: SLF001 - diagnostic script
        bootstrap_meta = sanitize(raw_meta or {})
        matches = list(raw_matches or [])
        try:
            matches = runner._dedupe_matches(matches)  # noqa: SLF001
        except Exception:
            pass
        try:
            filtered, filtering = runner._filter_matches(matches, now)  # noqa: SLF001
            matches = list(filtered or matches)
            bootstrap_meta["filtering"] = sanitize(filtering)
        except Exception as exc:
            bootstrap_meta["filter_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        bootstrap_error = f"{type(exc).__name__}: {exc}"
        bootstrap_meta["traceback"] = traceback.format_exc()[-3000:]

    match_limit = max(1, int(args.match_limit or 24))
    matches = matches[:match_limit]

    results: dict[str, Any] = {}
    offer_maps: dict[str, Any] = {}
    context_maps: dict[str, Any] = {}

    for provider_name in providers:
        provider = getattr(runner, provider_name, None)
        if provider_name == "weather":
            provider = getattr(runner, "weather", None)
        provider_result: dict[str, Any] = {
            "provider": provider_name,
            "loaded": provider is not None,
            "secrets_present": provider_secret_presence(provider_name),
            "checks": {},
        }

        if provider_name == "sportlogic" and provider is not None:
            data, stats, preview, error = await maybe_call(provider, "fetch_matches")
            status, reason = verdict(provider_name, "fetch_matches", data, stats, error)
            provider_result["checks"]["fetch_matches"] = {
                "status": status,
                "reason": reason,
                "data_count": data_count(data),
                "stats": compact_stats(stats),
                "preview": sanitize(preview),
            }

        if provider_name in OFFER_PROVIDERS:
            data, stats, preview, error = await maybe_call(provider, "fetch_offers", matches)
            status, reason = verdict(provider_name, "fetch_offers", data, stats, error)
            provider_result["checks"]["fetch_offers"] = {
                "status": status,
                "reason": reason,
                "data_count": data_count(data),
                "match_count": len(data or {}) if isinstance(data, dict) else 0,
                "stats": compact_stats(stats),
                "preview": sanitize(preview),
            }
            if isinstance(data, dict):
                offer_maps[provider_name] = data

        if provider_name in CONTEXT_PROVIDERS:
            target_matches = matches[: max(1, min(len(matches), int(args.context_match_limit or 12)))]
            data, stats, preview, error = await maybe_call(provider, "fetch_context", target_matches)
            status, reason = verdict(provider_name, "fetch_context", data, stats, error)
            provider_result["checks"]["fetch_context"] = {
                "status": status,
                "reason": reason,
                "data_count": data_count(data),
                "match_count": len(data or {}) if isinstance(data, dict) else 0,
                "stats": compact_stats(stats),
                "preview": sanitize(preview),
            }
            if isinstance(data, dict):
                context_maps[provider_name] = data

        results[provider_name] = provider_result

    ok_count = sum(1 for provider in results.values() for check in provider.get("checks", {}).values() if check.get("status") == "OK")
    warning_count = sum(1 for provider in results.values() for check in provider.get("checks", {}).values() if check.get("status") not in {"OK", "ERROR", "AUTH", "RATE_LIMIT"})
    error_count = sum(1 for provider in results.values() for check in provider.get("checks", {}).values() if check.get("status") in {"ERROR", "AUTH", "RATE_LIMIT"})

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_smoke",
        "providers_requested": providers,
        "settings": {
            "match_limit": match_limit,
            "context_match_limit": args.context_match_limit,
            "publish_dry_run": os.getenv("PUBLISH_DRY_RUN"),
            "prediction_publication_enabled": os.getenv("PREDICTION_PUBLICATION_ENABLED"),
            "sportlogic_odds_match_limit": os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT"),
            "sportlogic_budget_reason": os.getenv("SPORTLOGIC_REQUEST_BUDGET_REASON"),
        },
        "bootstrap": {
            "status": "ERROR" if bootstrap_error else "OK" if matches else "EMPTY",
            "error": bootstrap_error,
            "matches_count": len(matches),
            "meta": bootstrap_meta,
            "sample_matches": match_sample_payload(matches),
        },
        "summary": {
            "checks_ok": ok_count,
            "checks_warning": warning_count,
            "checks_error": error_count,
            "providers_loaded": sum(1 for provider in results.values() if provider.get("loaded")),
            "providers_total": len(results),
        },
        "providers": results,
    }
    write_json(JSON_OUT, payload)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(ARTIFACT_DIR / "provider-smoke.json", payload)
    report_text = build_text_report(payload)
    write_text(TXT_OUT, report_text)
    write_text(ARTIFACT_DIR / "provider-smoke.txt", report_text)
    return payload


def build_text_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    bootstrap = payload.get("bootstrap") or {}
    lines = [
        "🧪 Provider smoke run",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• bootstrap: {bootstrap.get('status')} | matches: {bootstrap.get('matches_count')}",
        f"• checks: OK {summary.get('checks_ok')} | warn {summary.get('checks_warning')} | errors {summary.get('checks_error')}",
        "",
        "📡 Providers",
    ]
    providers = payload.get("providers") or {}
    for name, info in providers.items():
        checks = info.get("checks") or {}
        if not checks:
            lines.append(f"• {name}: no checks | loaded={info.get('loaded')}")
            continue
        fragments = []
        for method, check in checks.items():
            status = check.get("status")
            count = check.get("data_count")
            reason = check.get("reason")
            fragments.append(f"{method}={status} data={count} ({reason})")
        lines.append(f"• {name}: " + "; ".join(fragments))
        if name == "sportlogic":
            for method, check in checks.items():
                stats = check.get("stats") or {}
                if stats.get("attempted_paths"):
                    lines.append(f"  attempted_paths: {json.dumps(stats.get('attempted_paths'), ensure_ascii=False)[:900]}")
                if stats.get("fixture_query_attempts"):
                    lines.append(f"  fixture_query_attempts: {json.dumps(stats.get('fixture_query_attempts'), ensure_ascii=False)[:900]}")
                if stats.get("last_body_preview"):
                    lines.append(f"  last_body_preview: {str(stats.get('last_body_preview'))[:500]}")
    lines.extend([
        "",
        "📁 Files",
        f"• JSON: {JSON_OUT.as_posix()}",
        f"• TXT: {TXT_OUT.as_posix()}",
    ])
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    chunks = [text[i:i + 3600] for i in range(0, len(text), 3600)] or [text]
    ok = True
    for chunk in chunks[:4]:
        data = parse.urlencode({"chat_id": chat_id, "text": chunk}).encode("utf-8")
        req = request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        try:
            with request.urlopen(req, timeout=20) as resp:  # nosec - CI diagnostic script
                ok = ok and 200 <= int(resp.status) < 300
        except Exception:
            ok = False
    return ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast provider smoke run")
    parser.add_argument("--providers", default=os.getenv("PROVIDER_SMOKE_PROVIDERS", "core"), help="Group/name list: all,core,odds,context,sportlogic or comma-separated providers")
    parser.add_argument("--match-limit", type=int, default=safe_int(os.getenv("PROVIDER_SMOKE_MATCH_LIMIT"), 24))
    parser.add_argument("--context-match-limit", type=int, default=safe_int(os.getenv("PROVIDER_SMOKE_CONTEXT_MATCH_LIMIT"), 12))
    parser.add_argument("--send-telegram", action="store_true", default=truthy(os.getenv("PROVIDER_SMOKE_SEND_TELEGRAM")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = asyncio.run(run(args))
    text = build_text_report(payload)
    print(text)
    if args.send_telegram:
        sent = send_telegram(text)
        payload["telegram_sent"] = sent
        write_json(JSON_OUT, payload)
        write_json(ARTIFACT_DIR / "provider-smoke.json", payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
