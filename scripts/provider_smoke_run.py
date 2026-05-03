from __future__ import annotations

"""Fast provider smoke run.

Safe diagnostics only: no predictions are published and no betting ledger is
mutated. The script always writes JSON/TXT outputs, even when one provider
crashes. SportLogic runtime patches are installed explicitly here so diagnostics
never depend only on sitecustomize/import-hook timing.
"""

import argparse
import asyncio
import json
import os
import subprocess
import traceback
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
ARTIFACT_DIR = Path("artifacts/provider-smoke")
JSON_OUT = EXPORT_DIR / "latest-provider-smoke.json"
TXT_OUT = EXPORT_DIR / "latest-provider-smoke.txt"

GROUPS = {
    "core": ["odds_api_io", "sportlogic", "sstats", "bzzoiro", "football_data", "thesportsdb", "espn"],
    "odds": ["odds_api_io", "sportlogic", "bookies_api", "oddspapi", "allsportsapi"],
    "context": ["sstats", "bzzoiro", "api_football", "espn", "football_data", "thesportsdb", "openligadb", "openfootball", "futrixmetrics", "newsapi", "gnews"],
    "sportlogic": ["sportlogic"],
}
GROUPS["all"] = list(dict.fromkeys(GROUPS["odds"] + GROUPS["context"]))

OFFER_PROVIDERS = {"odds_api_io", "bookies_api", "oddspapi", "allsportsapi", "sportlogic"}
CONTEXT_PROVIDERS = {"sstats", "bzzoiro", "api_football", "espn", "football_data", "thesportsdb", "openligadb", "openfootball", "futrixmetrics", "newsapi", "gnews", "sportlogic"}
SECRET_MARKERS = ("key", "token", "secret", "authorization", "password", "apikey", "api_key")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def safe_int(value: object, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return os.getenv("GITHUB_SHA", "")


def install_runtime_patches() -> dict[str, Any]:
    result: dict[str, Any] = {"sportlogic_hardening": False, "sportlogic_fixture_discovery": False, "errors": []}
    try:
        from app.providers import sportlogic_hardening
        result["sportlogic_hardening"] = bool(sportlogic_hardening.install())
    except Exception as exc:
        result["errors"].append(f"sportlogic_hardening:{type(exc).__name__}:{exc}")
    try:
        from app.providers import sportlogic_fixture_discovery_v8
        result["sportlogic_fixture_discovery"] = bool(sportlogic_fixture_discovery_v8.install())
        result["sportlogic_fixture_patch_marker"] = getattr(sportlogic_fixture_discovery_v8, "PATCH_MARKER", "")
    except Exception as exc:
        result["errors"].append(f"sportlogic_fixture_discovery_v8:{type(exc).__name__}:{exc}")
    return result


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "..."
    if is_dataclass(value):
        try:
            value = asdict(value)
        except Exception:
            return str(value)[:400]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            low = str(key).lower()
            out[str(key)] = "***" if any(marker in low for marker in SECRET_MARKERS) else sanitize(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:1400]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        return str(value)[:400]
    except Exception:
        return f"<{type(value).__name__}>"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def count_data(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, dict):
        if not data:
            return 0
        values = list(data.values())
        if values and all(isinstance(item, list) for item in values):
            return sum(len(item) for item in values)
        return len(data)
    if isinstance(data, (list, tuple, set)):
        return len(data)
    return 1


def compact_stats(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    out: dict[str, Any] = {}
    keep = [
        "enabled", "api_key_present", "credentials_present", "requests", "event_requests", "odds_requests",
        "games_fetched", "fixtures_fetched", "fixtures_skipped", "matches_built", "events_fetched", "events_matched",
        "contexts_built", "offers_parsed", "rows_before_parse", "odds_payload_rows", "empty_odds_payloads",
        "empty_fixture_attempts", "budget_exhausted", "response_errors", "auth_error", "rate_limited",
        "cooldown_active", "stop_reason", "odds_endpoint_used", "empty_games_reason", "last_body_preview",
        "fixture_out_of_window", "fixture_parse_rejects", "sample_fixture_keys", "fixture_stale_rows_filtered",
        "fixture_window_start", "fixture_window_end", "fixture_page_scan_max", "fixture_cursor_scan_max",
    ]
    for key in keep:
        if key in stats:
            out[key] = sanitize(stats.get(key))
    for key in ("http_statuses", "event_http_statuses", "odds_http_statuses"):
        if key in stats:
            try:
                out[key] = dict(Counter(stats.get(key) or []))
            except Exception:
                out[key] = sanitize(stats.get(key))
    for key in (
        "attempted_paths", "fixture_query_attempts", "fixture_dates_requested", "fixture_rows_by_date",
        "top_level_keys", "payload_shapes", "parse_reject_reasons", "sportlogic_hardening_reject_reasons",
    ):
        if key in stats:
            out[key] = sanitize(stats.get(key))
    return out


def verdict(provider: str, method: str, data: Any, stats: dict[str, Any], error: str | None) -> tuple[str, str]:
    if error:
        if error.startswith("method_missing") or error == "provider_not_loaded":
            return "SKIP", error
        return "ERROR", error[:220]
    if stats.get("auth_error"):
        return "AUTH", "auth_error=true"
    if stats.get("rate_limited"):
        return "RATE_LIMIT", "rate_limited=true"
    if count_data(data) > 0:
        return "OK", f"data={count_data(data)}"
    if provider == "sportlogic":
        fixtures = safe_int(stats.get("fixtures_fetched") or stats.get("games_fetched"), 0)
        if fixtures <= 0:
            return "EMPTY_FIXTURES", "SportLogic /games returned no fixtures inside target window"
        if method == "fetch_matches" and safe_int(stats.get("matches_built"), 0) <= 0:
            return "NO_MATCHES_BUILT", f"fixtures={fixtures}, rejects={stats.get('fixture_parse_rejects')}, out_of_window={stats.get('fixture_out_of_window')}"
        if method == "fetch_offers" and safe_int(stats.get("events_matched"), 0) <= 0:
            return "NO_MATCH", "fixtures did not match bootstrap matches"
        if method == "fetch_offers" and safe_int(stats.get("odds_requests"), 0) <= 0:
            return "NO_ODDS_REQUEST", "provider did not reach odds endpoint"
    if stats.get("response_errors"):
        return "HTTP_ERROR", f"response_errors={stats.get('response_errors')}"
    return "EMPTY", "no data for smoke sample"


def provider_secrets(provider: str) -> dict[str, bool]:
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
    }
    return {name: bool(os.getenv(name)) for name in mapping.get(provider, [])}


async def call_provider(provider: Any, method_name: str, *args: Any) -> tuple[Any, dict[str, Any], dict[str, Any], str | None]:
    if provider is None:
        return None, {"loaded": False, "enabled": False}, {}, "provider_not_loaded"
    method = getattr(provider, method_name, None)
    if not callable(method):
        return None, {"loaded": True, "enabled": True}, {}, f"method_missing:{method_name}"
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


def resolve_provider_list(raw: str) -> list[str]:
    out: list[str] = []
    for item in str(raw or "core").split(","):
        key = item.strip().lower()
        if not key:
            continue
        out.extend(GROUPS.get(key, [key]))
    return list(dict.fromkeys(out))


def match_sample(matches: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches[:limit]:
        dt = getattr(match, "commence_time", None)
        rows.append({
            "source": getattr(match, "source", ""),
            "event_id": getattr(match, "source_event_id", ""),
            "league": getattr(match, "league_name", ""),
            "home": getattr(match, "home_team", ""),
            "away": getattr(match, "away_team", ""),
            "commence_time": dt.isoformat() if hasattr(dt, "isoformat") else "",
            "match_key": getattr(match, "match_key", ""),
        })
    return rows


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    runtime_patches = install_runtime_patches()
    from app.config import Settings
    from app.services.runner import PredictionRunner

    settings = Settings()
    runner = PredictionRunner(settings)
    providers = resolve_provider_list(args.providers)
    now = datetime.now(UTC)

    bootstrap: dict[str, Any] = {"status": "EMPTY", "matches_count": 0, "error": None, "meta": {}, "sample_matches": []}
    matches: list[Any] = []
    try:
        raw_matches, meta = await runner._fetch_matches()  # noqa: SLF001
        matches = list(raw_matches or [])
        try:
            matches = runner._dedupe_matches(matches)  # noqa: SLF001
        except Exception:
            pass
        try:
            filtered, filtering = runner._filter_matches(matches, now)  # noqa: SLF001
            matches = list(filtered or matches)
            meta = dict(meta or {})
            meta["filtering"] = filtering
        except Exception as exc:
            meta = dict(meta or {})
            meta["filter_error"] = f"{type(exc).__name__}: {exc}"
        matches = matches[: max(1, int(args.match_limit or 24))]
        bootstrap.update({"status": "OK" if matches else "EMPTY", "matches_count": len(matches), "meta": sanitize(meta), "sample_matches": match_sample(matches)})
    except Exception as exc:
        bootstrap.update({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}", "meta": {"traceback": traceback.format_exc()[-3000:]}})

    results: dict[str, Any] = {}
    for provider_name in providers:
        provider = getattr(runner, provider_name, None)
        result = {"provider": provider_name, "loaded": provider is not None, "secrets_present": provider_secrets(provider_name), "checks": {}}

        if provider_name == "sportlogic":
            data, stats, preview, error = await call_provider(provider, "fetch_matches")
            status, reason = verdict(provider_name, "fetch_matches", data, stats, error)
            result["checks"]["fetch_matches"] = {"status": status, "reason": reason, "data_count": count_data(data), "stats": compact_stats(stats), "preview": sanitize(preview)}

        if provider_name in OFFER_PROVIDERS:
            data, stats, preview, error = await call_provider(provider, "fetch_offers", matches)
            status, reason = verdict(provider_name, "fetch_offers", data, stats, error)
            result["checks"]["fetch_offers"] = {"status": status, "reason": reason, "data_count": count_data(data), "match_count": len(data or {}) if isinstance(data, dict) else 0, "stats": compact_stats(stats), "preview": sanitize(preview)}

        if provider_name in CONTEXT_PROVIDERS:
            target = matches[: max(1, min(len(matches), int(args.context_match_limit or 12)))]
            data, stats, preview, error = await call_provider(provider, "fetch_context", target)
            status, reason = verdict(provider_name, "fetch_context", data, stats, error)
            result["checks"]["fetch_context"] = {"status": status, "reason": reason, "data_count": count_data(data), "match_count": len(data or {}) if isinstance(data, dict) else 0, "stats": compact_stats(stats), "preview": sanitize(preview)}

        results[provider_name] = result

    all_checks = [check for info in results.values() for check in (info.get("checks") or {}).values()]
    summary = {
        "checks_ok": sum(1 for check in all_checks if check.get("status") == "OK"),
        "checks_warning": sum(1 for check in all_checks if check.get("status") in {"EMPTY", "EMPTY_CONTEXT", "EMPTY_OFFERS", "EMPTY_FIXTURES", "NO_MATCHES_BUILT", "NO_MATCH", "NO_ODDS_REQUEST", "HTTP_ERROR", "SKIP"}),
        "checks_error": sum(1 for check in all_checks if check.get("status") in {"ERROR", "AUTH", "RATE_LIMIT"}),
        "providers_loaded": sum(1 for info in results.values() if info.get("loaded")),
        "providers_total": len(results),
    }
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "github_sha": os.getenv("GITHUB_SHA", ""),
        "mode": "provider_smoke",
        "runtime_patches": runtime_patches,
        "providers_requested": providers,
        "settings": {
            "providers_arg": args.providers,
            "match_limit": args.match_limit,
            "context_match_limit": args.context_match_limit,
            "publish_dry_run": os.getenv("PUBLISH_DRY_RUN"),
            "prediction_publication_enabled": os.getenv("PREDICTION_PUBLICATION_ENABLED"),
            "sportlogic_odds_match_limit": os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT"),
            "sportlogic_budget_reason": os.getenv("SPORTLOGIC_REQUEST_BUDGET_REASON"),
        },
        "bootstrap": bootstrap,
        "summary": summary,
        "providers": results,
    }
    write_outputs(payload)
    return payload


def build_text(payload: dict[str, Any]) -> str:
    bootstrap = payload.get("bootstrap") or {}
    summary = payload.get("summary") or {}
    lines = [
        "🧪 Provider smoke run",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• git: {str(payload.get('git_sha') or '')[:12]}",
        f"• runtime patches: {payload.get('runtime_patches')}",
        f"• bootstrap: {bootstrap.get('status')} | matches: {bootstrap.get('matches_count')}",
        f"• checks: OK {summary.get('checks_ok')} | warn {summary.get('checks_warning')} | errors {summary.get('checks_error')}",
        "",
        "📡 Providers",
    ]
    for name, info in (payload.get("providers") or {}).items():
        checks = info.get("checks") or {}
        if not checks:
            lines.append(f"• {name}: no checks | loaded={info.get('loaded')}")
            continue
        pieces = []
        for method, check in checks.items():
            pieces.append(f"{method}={check.get('status')} data={check.get('data_count')} ({check.get('reason')})")
        lines.append(f"• {name}: " + "; ".join(pieces))
        if name == "sportlogic":
            for check in checks.values():
                stats = check.get("stats") or {}
                preview = check.get("preview") or {}
                for key in ("fixtures_fetched", "matches_built", "fixture_parse_rejects", "fixture_out_of_window", "fixture_window_start", "fixture_window_end", "fixture_page_scan_max", "fixture_cursor_scan_max", "fixture_stale_rows_filtered", "sample_fixture_keys", "attempted_paths", "fixture_query_attempts", "last_body_preview"):
                    if stats.get(key) not in (None, "", [], {}):
                        value = stats.get(key)
                        lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)[:1200] if key != 'last_body_preview' else str(value)[:500]}")
                for key in ("sample_fixture_parse", "sample_fixture_parse_kept", "sample_matches"):
                    if preview.get(key):
                        lines.append(f"  {key}: {json.dumps(preview.get(key), ensure_ascii=False)[:1400]}")
    lines += ["", "📁 Files", str(JSON_OUT), str(TXT_OUT)]
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any]) -> None:
    text = build_text(payload)
    write_json(JSON_OUT, payload)
    write_text(TXT_OUT, text)
    write_json(ARTIFACT_DIR / "provider-smoke.json", payload)
    write_text(ARTIFACT_DIR / "provider-smoke.txt", text)


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    ok = True
    for chunk in [text[i:i + 3600] for i in range(0, len(text), 3600)][:6] or [text]:
        try:
            req = request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=parse.urlencode({"chat_id": chat_id, "text": chunk}).encode("utf-8"))
            with request.urlopen(req, timeout=20) as resp:  # nosec - CI-only diagnostic script
                ok = ok and 200 <= int(resp.status) < 300
        except Exception:
            ok = False
    return ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast provider smoke run")
    parser.add_argument("--providers", default=os.getenv("PROVIDER_SMOKE_PROVIDERS", "core"))
    parser.add_argument("--match-limit", type=int, default=safe_int(os.getenv("PROVIDER_SMOKE_MATCH_LIMIT"), 24))
    parser.add_argument("--context-match-limit", type=int, default=safe_int(os.getenv("PROVIDER_SMOKE_CONTEXT_MATCH_LIMIT"), 12))
    parser.add_argument("--send-telegram", action="store_true", default=truthy(os.getenv("PROVIDER_SMOKE_SEND_TELEGRAM")))
    return parser.parse_args()


def failure_payload(exc: BaseException) -> dict[str, Any]:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "mode": "provider_smoke",
        "fatal_error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc()[-5000:],
        "summary": {"checks_ok": 0, "checks_warning": 0, "checks_error": 1, "providers_loaded": 0, "providers_total": 0},
        "providers": {},
        "bootstrap": {"status": "ERROR", "matches_count": 0, "error": f"{type(exc).__name__}: {exc}"},
    }
    write_outputs(payload)
    return payload


def main() -> int:
    args = parse_args()
    try:
        payload = asyncio.run(run_smoke(args))
    except BaseException as exc:
        payload = failure_payload(exc)
    text = build_text(payload)
    print(text)
    if args.send_telegram:
        payload["telegram_sent"] = send_telegram(text)
        write_outputs(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
