#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

import httpx

UTC = timezone.utc


ACTIVE_PROVIDER_CHECKS = (
    "odds_api_io",
    "allsportsapi",
    "sportlogic",
    "sstats",
    "bzzoiro",
    "football_data",
    "thesportsdb",
    "highlightly",
    "weatherapi",
    "openweathermap",
    "meteostat",
    "newsapi",
    "currents",
    "gnews",
    "newsdata",
    "guardian",
    "sharpapi",
    "futrixmetrics",
)

REMOVED_PROVIDERS = {"bookies_api", "api_football", "oddspapi"}
NON_FAILURE_STATUSES = {"ok", "degraded", "config_only"}


@dataclass(slots=True)
class CheckResult:
    provider: str
    group: str
    status: str
    critical: bool
    configured: bool
    requests: int = 0
    useful_rows: int = 0
    http_statuses: list[int] | None = None
    latency_ms: int | None = None
    message: str = ""
    endpoint: str = ""
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["http_statuses"] = data["http_statuses"] or []
        data["details"] = data["details"] or {}
        return data


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def now_utc() -> datetime:
    return datetime.now(UTC)


def redact(text: Any) -> str:
    raw = str(text or "")
    for key, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if any(token in key.upper() for token in ("KEY", "TOKEN", "LOGIN", "SECRET", "CHAT_ID")):
            raw = raw.replace(str(value), "***")
    raw = re.sub(r"(apiKey|apikey|key|token|login|appid|X-API-Key|x-rapidapi-key)=([^&\s]+)", r"\1=***", raw, flags=re.I)
    raw = re.sub(r"(Bearer|Token)\s+[A-Za-z0-9._\-]+", r"\1 ***", raw)
    return raw[:1800]


def count_rows(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "results", "response", "events", "matches", "fixtures", "games", "result", "competitions", "articles", "news", "countries", "leagues"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                for nested in ("data", "results", "events", "matches", "fixtures", "games", "countries", "leagues"):
                    nested_value = value.get(nested)
                    if isinstance(nested_value, list):
                        return len(nested_value)
        forecast = payload.get("forecast")
        if isinstance(forecast, dict) and isinstance(forecast.get("forecastday"), list):
            return len(forecast["forecastday"])
        if payload.get("success") in (1, "1", True):
            result = payload.get("result")
            if isinstance(result, list):
                return len(result)
    return 0


def payload_shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "list"
    if isinstance(payload, dict):
        return ",".join(sorted(map(str, payload.keys()))[:16])
    return type(payload).__name__


def non_empty_error(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        return any(non_empty_error(v) for v in value.values())
    if isinstance(value, list):
        return any(non_empty_error(v) for v in value)
    return True


def auth_like(text: str) -> bool:
    low = str(text or "").casefold()
    return any(token in low for token in ("invalid api key", "missing api key", "missing application key", "unauthorized", "forbidden", "invalid token", "auth"))


def semantic_status(payload: Any, body_text: str) -> tuple[str | None, str | None]:
    text = str(body_text or "")
    low = text.casefold().strip()
    if text.lstrip().startswith("<!doctype") or text.lstrip().startswith("<html"):
        return "degraded", "html_error_page"
    if "invalid api key" in low or "missing application key" in low:
        return "auth_error", "provider_body_auth_error:invalid_or_missing_key"
    if "404 page not found" in low:
        return "degraded", "provider_body_not_found"
    if isinstance(payload, dict):
        for key in ("errors", "error"):
            value = payload.get(key)
            if non_empty_error(value):
                serialized = json.dumps(value, ensure_ascii=False)[:500]
                return ("auth_error", "provider_payload_auth_error") if auth_like(serialized) else ("degraded", f"provider_payload_{key}")
        if payload.get("success") in (False, 0, "0", "false", "False"):
            serialized = json.dumps(payload, ensure_ascii=False)[:500]
            return ("auth_error", "provider_payload_success_false_auth") if auth_like(serialized) else ("degraded", "provider_payload_success_false")
    return None, None


class HealthRunner:
    def __init__(self, *, mode: str, timeout: float) -> None:
        self.mode = mode
        self.timeout = timeout
        self.results: list[CheckResult] = []

    @staticmethod
    def safe_endpoint(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return redact(url)
        safe_params = {}
        for key, value in params.items():
            safe_params[key] = "***" if any(token in str(key).lower() for token in ("key", "token", "login", "apikey", "appid")) else value
        return redact(f"{url}?{urlencode(safe_params)}")

    async def request(self, client: httpx.AsyncClient, provider: str, group: str, url: str, *, critical: bool, configured: bool, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, expected: set[int] | None = None, details: dict[str, Any] | None = None) -> CheckResult:
        if provider in REMOVED_PROVIDERS:
            return CheckResult(provider, group, "removed", False, False, message="provider removed by policy", endpoint=redact(url), details=details or {})
        if not configured:
            return CheckResult(provider, group, "missing_secret", critical, False, message="required secret is not configured", endpoint=redact(url), details=details or {})
        expected = expected or {200}
        start = now_utc()
        statuses: list[int] = []
        try:
            response = await client.get(url, params=params, headers=headers)
            latency_ms = int((now_utc() - start).total_seconds() * 1000)
            statuses.append(response.status_code)
            try:
                payload = response.json()
            except Exception:
                payload = None
            rows = count_rows(payload)
            status = "ok" if response.status_code in expected else "degraded"
            if response.status_code == 429:
                status = "rate_limited"
            elif response.status_code in {401, 403}:
                status = "auth_error"
            elif response.status_code >= 500:
                status = "server_error"
            semantic, semantic_message = semantic_status(payload, response.text)
            if response.status_code in expected and semantic:
                status = semantic
            message = "ok" if status == "ok" else (semantic_message or f"http_status={response.status_code}")
            merged = dict(details or {})
            merged.update({"body_preview": redact(response.text[:1200]), "payload_shape": payload_shape(payload)})
            if semantic_message:
                merged["semantic_message"] = semantic_message
            return CheckResult(provider, group, status, critical, True, 1, rows, statuses, latency_ms, message, self.safe_endpoint(url, params), merged)
        except Exception as exc:
            return CheckResult(provider, group, "error", critical, True, 1, 0, statuses, int((now_utc() - start).total_seconds() * 1000), f"{exc.__class__.__name__}: {exc}", self.safe_endpoint(url, params), details or {})

    def add(self, result: CheckResult) -> None:
        if result.provider not in REMOVED_PROVIDERS:
            self.results.append(result)

    async def run(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            await asyncio.gather(
                self.check_odds_api_io(client),
                self.check_allsportsapi(client),
                self.check_sportlogic(client),
                self.check_sstats(client),
                self.check_bzzoiro(client),
                self.check_football_data(client),
                self.check_thesportsdb(client),
                self.check_highlightly(client),
                self.check_weatherapi(client),
                self.check_openweathermap(client),
                self.check_meteostat(client),
                self.check_newsapi(client),
                self.check_currents(client),
                self.check_gnews(client),
                self.check_newsdata(client),
                self.check_guardian(client),
                self.check_sharpapi(client),
                self.check_futrixmetrics(client),
            )
        return self.report()

    async def check_odds_api_io(self, client: httpx.AsyncClient) -> None:
        key1 = env("ODDS_API_IO_KEY")
        key2 = env("ODDS_API_IO_KEY_2") or env("ODDS_API_IO_KEY2")
        base = env("ODDS_API_IO_BASE_URL", "https://api.odds-api.io/v3").rstrip("/")
        current = now_utc()
        params = {"apiKey": key1, "sport": "football", "status": "pending,live", "from": current.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "to": (current + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "limit": 10, "page": 1}
        res = await self.request(client, "odds_api_io_events", "odds", f"{base}/events", critical=True, configured=bool(key1), params=params, details={"key2_present": bool(key2), "bookmakers_account1": env("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet"), "bookmakers_account2": env("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet")})
        self.add(res)
        if self.mode != "deep" or res.status not in {"ok", "degraded"}:
            return
        try:
            response = await client.get(f"{base}/events", params=params)
            payload = response.json()
            event_ids = [str(x.get("id")) for x in payload if isinstance(x, dict) and x.get("id")][:3] if isinstance(payload, list) else []
        except Exception:
            event_ids = []
        for name, key, books in (("odds_api_io_account1", key1, env("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet")), ("odds_api_io_account2", key2, env("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet"))):
            if event_ids:
                self.add(await self.request(client, name, "odds", f"{base}/odds/multi", critical=name.endswith("account1"), configured=bool(key), params={"apiKey": key, "eventIds": ",".join(event_ids), "bookmakers": books}, details={"requested_bookmakers": books, "event_sample": len(event_ids)}))

    async def check_allsportsapi(self, client: httpx.AsyncClient) -> None:
        key = env("ALLSPORTSAPI_API_KEY")
        base = env("ALLSPORTSAPI_BASE_URL", "https://apiv2.allsportsapi.com/football").rstrip("/")
        d = now_utc().date()
        self.add(await self.request(client, "allsportsapi", "odds_context", f"{base}/", critical=False, configured=bool(key), params={"met": "Fixtures", "APIkey": key, "from": d.isoformat(), "to": (d + timedelta(days=1)).isoformat(), "timezone": "UTC"}))

    async def check_sportlogic(self, client: httpx.AsyncClient) -> None:
        key = env("SPORTLOGIC_API_KEY") or env("SPORTLOGIC_KEY") or env("SPORTLOGIC_TOKEN")
        base = env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
        header = env("SPORTLOGIC_HEADER_NAME", "X-API-Key")
        self.add(await self.request(client, "sportlogic", "odds", f"{base}/games", critical=True, configured=bool(key), params={"sport": "football", "limit": 5}, headers={header: key} if key else {}, details={"controlled_odds_enabled": truthy(env("SPORTLOGIC_CONTROLLED_ODDS_ENABLED"))}))

    async def check_sstats(self, client: httpx.AsyncClient) -> None:
        key = env("SSTATS_API_KEY")
        d = now_utc().date()
        self.add(await self.request(client, "sstats", "context", "https://api.sstats.net/Games/list", critical=True, configured=bool(key), params={"apikey": key, "from": (d - timedelta(days=1)).isoformat(), "to": d.isoformat(), "limit": 5, "offset": 0}))

    async def check_bzzoiro(self, client: httpx.AsyncClient) -> None:
        key = env("BZZOIRO_API_KEY")
        d = now_utc().date()
        self.add(await self.request(client, "bzzoiro", "context", "https://sports.bzzoiro.com/api/predictions/", critical=True, configured=bool(key), params={"date_from": d.isoformat(), "date_to": (d + timedelta(days=1)).isoformat(), "upcoming": "true", "tz": "UTC", "page": 1}, headers={"Authorization": f"Token {key}"} if key else {}))

    async def check_football_data(self, client: httpx.AsyncClient) -> None:
        key = env("FOOTBALL_DATA_API_KEY")
        base = env("FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4").rstrip("/")
        self.add(await self.request(client, "football_data", "context", f"{base}/competitions", critical=False, configured=bool(key), headers={"X-Auth-Token": key} if key else {}))

    async def check_thesportsdb(self, client: httpx.AsyncClient) -> None:
        key = env("THESPORTSDB_API_KEY", "123")
        base = env("THESPORTSDB_BASE_URL", "https://www.thesportsdb.com/api/v1/json").rstrip("/")
        self.add(await self.request(client, "thesportsdb", "context", f"{base}/{key}/search_all_leagues.php", critical=False, configured=bool(key), params={"c": "England", "s": "Soccer"}, details={"free_key_defaulted": key == "123"}))

    async def check_highlightly(self, client: httpx.AsyncClient) -> None:
        key = env("HIGHLIGHTLY_API_KEY") or env("HIGHLIGHTLY_RAPIDAPI_KEY")
        base = env("HIGHLIGHTLY_BASE_URL", "https://soccer.highlightly.net").rstrip("/")
        host = env("HIGHLIGHTLY_RAPIDAPI_HOST")
        headers = {"x-rapidapi-key": key} if key else {}
        if host:
            headers["x-rapidapi-host"] = host
        self.add(await self.request(client, "highlightly", "context", f"{base}/matches", critical=False, configured=bool(key), params={"date": now_utc().date().isoformat(), "limit": 5}, headers=headers, details={"expected_base": "https://soccer.highlightly.net", "rapidapi_base": "https://football-highlights-api.p.rapidapi.com"}))

    async def check_weatherapi(self, client: httpx.AsyncClient) -> None:
        key = env("WEATHERAPI_KEY")
        self.add(await self.request(client, "weatherapi", "weather", "https://api.weatherapi.com/v1/forecast.json", critical=False, configured=bool(key), params={"key": key, "q": "London", "days": 1, "aqi": "no", "alerts": "no"}))

    async def check_openweathermap(self, client: httpx.AsyncClient) -> None:
        key = env("OPENWEATHERMAP_API_KEY") or env("OPENWEATHERMAP_KEY") or env("OPENWEATHER_API_KEY")
        self.add(await self.request(client, "openweathermap", "weather", "https://api.openweathermap.org/data/2.5/weather", critical=False, configured=bool(key), params={"q": "London", "appid": key, "units": "metric"}))

    async def check_meteostat(self, client: httpx.AsyncClient) -> None:
        key = env("METEOSTAT_RAPIDAPI_KEY")
        host = env("METEOSTAT_RAPIDAPI_HOST", "meteostat.p.rapidapi.com")
        self.add(await self.request(client, "meteostat", "weather", f"https://{host}/stations/nearby", critical=False, configured=bool(key), params={"lat": 51.5074, "lon": -0.1278, "limit": 1}, headers={"x-rapidapi-key": key, "x-rapidapi-host": host} if key else {}))

    async def check_newsapi(self, client: httpx.AsyncClient) -> None:
        key = env("NEWSAPI_KEY")
        self.add(await self.request(client, "newsapi", "news", "https://newsapi.org/v2/everything", critical=False, configured=bool(key), params={"apiKey": key, "q": "football", "language": "en", "pageSize": 1, "sortBy": "publishedAt"}))

    async def check_currents(self, client: httpx.AsyncClient) -> None:
        key = env("CURRENTS_API_KEY") or env("CURRENTS_KEY")
        self.add(await self.request(client, "currents", "news", "https://api.currentsapi.services/v1/latest-news", critical=False, configured=bool(key), params={"apiKey": key, "category": "sports", "language": "en"}))

    async def check_gnews(self, client: httpx.AsyncClient) -> None:
        key = env("GNEWS_KEY")
        self.add(await self.request(client, "gnews", "news", "https://gnews.io/api/v4/search", critical=False, configured=bool(key), params={"apikey": key, "q": "football", "lang": "en", "max": 1}))

    async def check_newsdata(self, client: httpx.AsyncClient) -> None:
        key = env("NEWSDATA_API_KEY")
        self.add(await self.request(client, "newsdata", "news", "https://newsdata.io/api/1/news", critical=False, configured=bool(key), params={"apikey": key, "q": "football", "language": "en", "size": 1}))

    async def check_guardian(self, client: httpx.AsyncClient) -> None:
        key = env("GUARDIAN_API_KEY")
        self.add(await self.request(client, "guardian", "news", "https://content.guardianapis.com/search", critical=False, configured=bool(key), params={"api-key": key, "q": "football", "section": "football", "page-size": 1}))

    async def check_sharpapi(self, client: httpx.AsyncClient) -> None:
        key = env("SHARPAPI_API_KEY")
        base = env("SHARPAPI_BASE_URL", "https://sharpapi.com").rstrip("/")
        prefix = env("SHARPAPI_API_PREFIX", "/api/v1").rstrip("/")
        self.add(await self.request(client, "sharpapi_configured_base", "utility", f"{base}{prefix}/quota", critical=False, configured=bool(key), headers={"Authorization": f"Bearer {key}"} if key else {}, details={"note": "configured base; current runtime treats SharpAPI as text-enrichment only"}))

    async def check_futrixmetrics(self, client: httpx.AsyncClient) -> None:
        key = env("FUTRIXMETRICS_API_KEY")
        base_url = env("FUTRIXMETRICS_BASE_URL")
        endpoint = env("FUTRIXMETRICS_HEALTH_ENDPOINT")
        if not key:
            self.add(CheckResult("futrixmetrics", "context", "missing_secret", False, False, message="required secret is not configured"))
            return
        if not base_url or not endpoint:
            self.add(CheckResult("futrixmetrics", "context", "config_only", False, True, message="key present; live probe skipped because FUTRIXMETRICS_BASE_URL/FUTRIXMETRICS_HEALTH_ENDPOINT are not configured", details={"set": "FUTRIXMETRICS_BASE_URL and FUTRIXMETRICS_HEALTH_ENDPOINT for live probe", "runtime_effect": "not treated as health failure"}))
            return
        self.add(await self.request(client, "futrixmetrics", "context", f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}", critical=False, configured=True, headers={"Authorization": f"Bearer {key}"}))

    def report(self) -> dict[str, Any]:
        rows = [x.to_dict() for x in sorted(self.results, key=lambda r: (r.group, r.provider))]
        critical_failures = [r for r in rows if r["critical"] and r["status"] not in NON_FAILURE_STATUSES]
        by_status: dict[str, int] = {}
        by_group: dict[str, dict[str, int]] = {}
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_group.setdefault(row["group"], {})
            by_group[row["group"]][row["status"]] = by_group[row["group"]].get(row["status"], 0) + 1
        summary = {
            "providers_checked": len(rows),
            "ok": by_status.get("ok", 0),
            "config_only": by_status.get("config_only", 0),
            "degraded": by_status.get("degraded", 0),
            "rate_limited": by_status.get("rate_limited", 0),
            "auth_error": by_status.get("auth_error", 0),
            "missing_secret": by_status.get("missing_secret", 0),
            "critical_failures": len(critical_failures),
            "healthy_or_config_only": by_status.get("ok", 0) + by_status.get("config_only", 0),
        }
        return {"created_at_utc": now_utc().isoformat(), "mode": self.mode, "removed_providers": sorted(REMOVED_PROVIDERS), "active_provider_checks": list(ACTIVE_PROVIDER_CHECKS), "summary": summary, "by_status": by_status, "by_group": by_group, "critical_failures": critical_failures, "results": rows, "recommendations": build_recommendations(rows)}


def build_recommendations(rows: list[dict[str, Any]]) -> list[str]:
    recs: list[str] = []
    by_provider = {row.get("provider"): row for row in rows}
    odds = by_provider.get("odds_api_io_events")
    if odds and odds.get("status") == "ok":
        recs.append("odds-api.io inventory is healthy; keep dual-account bookmaker split active.")
        if not ((odds.get("details") or {}).get("key2_present")):
            recs.append("ODDS_API_IO_KEY_2 is missing; four-bookmaker coverage will not work.")
    if by_provider.get("sportlogic", {}).get("status") == "ok":
        recs.append("SportLogic is reachable; use controlled shortlist mode only after fixture freshness/matching checks.")
    if by_provider.get("highlightly", {}).get("status") != "ok":
        recs.append("Highlightly is not healthy on the current endpoint; keep it as optional context until endpoint/key is verified.")
    if by_provider.get("futrixmetrics", {}).get("status") == "config_only":
        recs.append("FutrixMetrics key is present, but live probe is skipped until FUTRIXMETRICS_BASE_URL and FUTRIXMETRICS_HEALTH_ENDPOINT are configured; this is not a runtime failure.")
    if by_provider.get("thesportsdb", {}).get("status") == "ok":
        recs.append("TheSportsDB is reachable; use it for team/league alias enrichment.")
    recs.append("Removed providers are intentionally excluded: bookies_api, api_football, oddspapi.")
    return recs


def write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest-api-health-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# API Health Run",
        "",
        f"- Created UTC: `{report['created_at_utc']}`",
        f"- Mode: `{report['mode']}`",
        f"- Providers checked: **{report['summary']['providers_checked']}**",
        f"- Removed providers: `{', '.join(report['removed_providers'])}`",
        f"- OK: **{report['summary']['ok']}**",
        f"- Config-only: **{report['summary']['config_only']}**",
        f"- Healthy or config-only: **{report['summary']['healthy_or_config_only']}**",
        f"- Degraded: **{report['summary']['degraded']}**",
        f"- Rate-limited: **{report['summary']['rate_limited']}**",
        f"- Auth errors: **{report['summary']['auth_error']}**",
        f"- Missing secrets: **{report['summary']['missing_secret']}**",
        f"- Critical failures: **{report['summary']['critical_failures']}**",
        "",
        "## Recommendations",
        "",
    ]
    lines.extend([f"- {item}" for item in report.get("recommendations", [])])
    lines.extend(["", "## Provider results", "", "| Provider | Group | Status | Requests | Useful rows | Message |", "|---|---|---:|---:|---:|---|"])
    for row in report["results"]:
        lines.append(f"| `{row['provider']}` | `{row['group']}` | `{row['status']}` | {row['requests']} | {row['useful_rows']} | {str(row['message']).replace('|', '/')} |")
    (out_dir / "latest-api-health-run.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Health run for active sports-bot APIs only.")
    parser.add_argument("--mode", choices=["quick", "deep"], default=os.getenv("API_HEALTH_MODE", "quick"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("API_HEALTH_TIMEOUT_SECONDS", "12") or 12))
    parser.add_argument("--output-dir", default=os.getenv("API_HEALTH_OUTPUT_DIR", ".data/exports"))
    parser.add_argument("--fail-on-critical", action="store_true", default=truthy(os.getenv("API_HEALTH_FAIL_ON_CRITICAL")))
    args = parser.parse_args()
    report = asyncio.run(HealthRunner(mode=args.mode, timeout=args.timeout).run())
    write_report(report, Path(args.output_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_critical and int(report["summary"].get("critical_failures") or 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
