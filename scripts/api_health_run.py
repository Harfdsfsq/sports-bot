#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

import httpx

UTC = timezone.utc


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
        payload = asdict(self)
        payload["http_statuses"] = payload["http_statuses"] or []
        payload["details"] = payload["details"] or {}
        return payload


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
        for key in ("data", "results", "response", "events", "matches", "fixtures", "games", "result", "competitions", "articles", "news"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                for nested in ("data", "results", "events", "matches", "fixtures", "games"):
                    nested_value = value.get(nested)
                    if isinstance(nested_value, list):
                        return len(nested_value)
        if payload.get("success") in (1, "1", True):
            result = payload.get("result")
            if isinstance(result, list):
                return len(result)
    return 0


class HealthRunner:
    def __init__(self, *, mode: str, timeout: float) -> None:
        self.mode = mode
        self.timeout = timeout
        self.results: list[CheckResult] = []

    async def request(
        self,
        client: httpx.AsyncClient,
        provider: str,
        group: str,
        url: str,
        *,
        critical: bool,
        configured: bool,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        expected: set[int] | None = None,
        details: dict[str, Any] | None = None,
    ) -> CheckResult:
        if not configured:
            return CheckResult(provider, group, "missing_secret", critical, False, message="required secret is not configured", endpoint=redact(url), details=details or {})
        expected = expected or {200}
        start = now_utc()
        http_statuses: list[int] = []
        try:
            if method.upper() == "POST":
                response = await client.post(url, params=params, headers=headers)
            else:
                response = await client.get(url, params=params, headers=headers)
            latency_ms = int((now_utc() - start).total_seconds() * 1000)
            http_statuses.append(response.status_code)
            body_preview = redact(response.text[:1200])
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
            message = "ok" if status == "ok" else f"http_status={response.status_code}"
            merged_details = dict(details or {})
            merged_details.update({"body_preview": body_preview, "payload_shape": self.payload_shape(payload)})
            return CheckResult(provider, group, status, critical, True, 1, rows, http_statuses, latency_ms, message, self.safe_endpoint(url, params), merged_details)
        except Exception as exc:
            latency_ms = int((now_utc() - start).total_seconds() * 1000)
            return CheckResult(provider, group, "error", critical, True, 1, 0, http_statuses, latency_ms, f"{exc.__class__.__name__}: {exc}", self.safe_endpoint(url, params), details or {})

    @staticmethod
    def payload_shape(payload: Any) -> str:
        if isinstance(payload, list):
            return "list"
        if isinstance(payload, dict):
            return ",".join(sorted(map(str, payload.keys()))[:16])
        return type(payload).__name__

    @staticmethod
    def safe_endpoint(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return redact(url)
        safe_params = {}
        for key, value in params.items():
            if any(token in str(key).lower() for token in ("key", "token", "login", "apikey", "appid")):
                safe_params[key] = "***"
            else:
                safe_params[key] = value
        return redact(f"{url}?{urlencode(safe_params)}")

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    async def run(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            await asyncio.gather(
                self.check_odds_api_io(client),
                self.check_bookies_api(client),
                self.check_oddspapi(client),
                self.check_allsportsapi(client),
                self.check_sportlogic(client),
                self.check_sstats(client),
                self.check_bzzoiro(client),
                self.check_api_football(client),
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
        params = {
            "apiKey": key1,
            "sport": "football",
            "status": "pending,live",
            "from": current.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "to": (current + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "limit": 10,
            "page": 1,
        }
        res = await self.request(client, "odds_api_io_events", "odds", f"{base}/events", critical=True, configured=bool(key1), params=params, details={"bookmakers_account1": env("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet"), "bookmakers_account2": env("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet"), "key2_present": bool(key2)})
        self.add(res)
        if self.mode == "quick" or res.status not in {"ok", "degraded"}:
            return
        try:
            response = await client.get(f"{base}/events", params=params)
            payload = response.json()
            event_ids = [str(x.get("id")) for x in payload if isinstance(x, dict) and x.get("id")][:3] if isinstance(payload, list) else []
        except Exception:
            event_ids = []
        if not event_ids:
            return
        accounts = [
            ("odds_api_io_account1", key1, env("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet")),
            ("odds_api_io_account2", key2, env("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet")),
        ]
        for name, key, books in accounts:
            odds_params = {"apiKey": key, "eventIds": ",".join(event_ids), "bookmakers": books}
            self.add(await self.request(client, name, "odds", f"{base}/odds/multi", critical=name.endswith("account1"), configured=bool(key), params=odds_params, details={"requested_bookmakers": books, "event_sample": len(event_ids)}))

    async def check_bookies_api(self, client: httpx.AsyncClient) -> None:
        login = env("BOOKIES_API_LOGIN")
        token = env("BOOKIES_API_TOKEN") or env("BOOKIES_API_KEY")
        base = env("BOOKIES_API_BASE_URL", "https://bookiesapi.com/api/get.php")
        day = now_utc().strftime("%d.%m.%Y")
        params = {"login": login, "token": token, "task": "predatapage", "sport": "soccer", "day": day, "p": 1}
        self.add(await self.request(client, "bookies_api", "odds", base, critical=True, configured=bool(login and token), params=params))

    async def check_oddspapi(self, client: httpx.AsyncClient) -> None:
        key = env("ODDSPAPI_API_KEY")
        base = env("ODDSPAPI_BASE_URL", "https://api.oddspapi.io/v4").rstrip("/")
        current = now_utc()
        params = {"sportId": 10, "from": current.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "to": (current + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "statusId": 0, "hasOdds": "true", "apiKey": key}
        self.add(await self.request(client, "oddspapi", "odds", f"{base}/fixtures", critical=False, configured=bool(key), params=params))

    async def check_allsportsapi(self, client: httpx.AsyncClient) -> None:
        key = env("ALLSPORTSAPI_API_KEY")
        base = env("ALLSPORTSAPI_BASE_URL", "https://apiv2.allsportsapi.com/football").rstrip("/")
        current = now_utc()
        params = {"met": "Fixtures", "APIkey": key, "from": current.date().isoformat(), "to": (current + timedelta(days=1)).date().isoformat(), "timezone": "UTC"}
        self.add(await self.request(client, "allsportsapi", "odds_context", f"{base}/", critical=False, configured=bool(key), params=params))

    async def check_sportlogic(self, client: httpx.AsyncClient) -> None:
        key = env("SPORTLOGIC_API_KEY") or env("SPORTLOGIC_KEY") or env("SPORTLOGIC_TOKEN")
        base = env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
        header_name = env("SPORTLOGIC_HEADER_NAME", "X-API-Key")
        headers = {header_name: key} if key else {}
        params = {"sport": "football", "limit": 5}
        self.add(await self.request(client, "sportlogic", "odds", f"{base}/games", critical=True, configured=bool(key), params=params, headers=headers, details={"controlled_odds_enabled": truthy(env("SPORTLOGIC_CONTROLLED_ODDS_ENABLED"))}))

    async def check_sstats(self, client: httpx.AsyncClient) -> None:
        key = env("SSTATS_API_KEY")
        current = now_utc()
        params = {"apikey": key, "from": (current - timedelta(days=1)).date().isoformat(), "to": current.date().isoformat(), "limit": 5, "offset": 0}
        self.add(await self.request(client, "sstats", "context", "https://api.sstats.net/Games/list", critical=True, configured=bool(key), params=params))

    async def check_bzzoiro(self, client: httpx.AsyncClient) -> None:
        key = env("BZZOIRO_API_KEY")
        current = now_utc()
        headers = {"Authorization": f"Token {key}"} if key else {}
        params = {"date_from": current.date().isoformat(), "date_to": (current + timedelta(days=1)).date().isoformat(), "upcoming": "true", "tz": "UTC", "page": 1}
        self.add(await self.request(client, "bzzoiro", "context", "https://sports.bzzoiro.com/api/predictions/", critical=True, configured=bool(key), params=params, headers=headers))

    async def check_api_football(self, client: httpx.AsyncClient) -> None:
        key = env("API_FOOTBALL_KEY") or env("RAPIDAPI_KEY")
        base = env("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io").rstrip("/")
        headers = {"x-apisports-key": key} if key else {}
        self.add(await self.request(client, "api_football", "context", f"{base}/status", critical=False, configured=bool(key), headers=headers))

    async def check_football_data(self, client: httpx.AsyncClient) -> None:
        key = env("FOOTBALL_DATA_API_KEY")
        base = env("FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4").rstrip("/")
        headers = {"X-Auth-Token": key} if key else {}
        self.add(await self.request(client, "football_data", "context", f"{base}/competitions", critical=False, configured=bool(key), headers=headers))

    async def check_thesportsdb(self, client: httpx.AsyncClient) -> None:
        key = env("THESPORTSDB_API_KEY", "123")
        base = env("THESPORTSDB_BASE_URL", "https://www.thesportsdb.com/api/v1/json").rstrip("/")
        params = {"c": "England", "s": "Soccer"}
        self.add(await self.request(client, "thesportsdb", "context", f"{base}/{key}/search_all_leagues.php", critical=False, configured=bool(key), params=params, details={"free_key_defaulted": key == "123"}))

    async def check_highlightly(self, client: httpx.AsyncClient) -> None:
        key = env("HIGHLIGHTLY_API_KEY") or env("HIGHLIGHTLY_RAPIDAPI_KEY")
        base = env("HIGHLIGHTLY_BASE_URL", "https://highlightly.net/api").rstrip("/")
        path = env("HIGHLIGHTLY_FIXTURES_PATH", "/football/matches")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if env("HIGHLIGHTLY_RAPIDAPI_HOST"):
            headers = {"x-rapidapi-key": key, "x-rapidapi-host": env("HIGHLIGHTLY_RAPIDAPI_HOST")}
        self.add(await self.request(client, "highlightly", "context", f"{base}{path}", critical=False, configured=bool(key), params={"date": now_utc().date().isoformat()}, headers=headers))

    async def check_weatherapi(self, client: httpx.AsyncClient) -> None:
        key = env("WEATHERAPI_KEY")
        params = {"key": key, "q": "London", "days": 1, "aqi": "no", "alerts": "no"}
        self.add(await self.request(client, "weatherapi", "weather", "https://api.weatherapi.com/v1/forecast.json", critical=False, configured=bool(key), params=params))

    async def check_openweathermap(self, client: httpx.AsyncClient) -> None:
        key = env("OPENWEATHERMAP_API_KEY") or env("OPENWEATHERMAP_KEY") or env("OPENWEATHER_API_KEY")
        params = {"q": "London", "appid": key, "units": "metric"}
        self.add(await self.request(client, "openweathermap", "weather", "https://api.openweathermap.org/data/2.5/weather", critical=False, configured=bool(key), params=params))

    async def check_meteostat(self, client: httpx.AsyncClient) -> None:
        key = env("METEOSTAT_RAPIDAPI_KEY")
        host = env("METEOSTAT_RAPIDAPI_HOST", "meteostat.p.rapidapi.com")
        headers = {"x-rapidapi-key": key, "x-rapidapi-host": host} if key else {}
        params = {"lat": 51.5074, "lon": -0.1278, "limit": 1}
        self.add(await self.request(client, "meteostat", "weather", f"https://{host}/stations/nearby", critical=False, configured=bool(key), params=params, headers=headers))

    async def check_newsapi(self, client: httpx.AsyncClient) -> None:
        key = env("NEWSAPI_KEY")
        params = {"apiKey": key, "q": "football", "language": "en", "pageSize": 1, "sortBy": "publishedAt"}
        self.add(await self.request(client, "newsapi", "news", "https://newsapi.org/v2/everything", critical=False, configured=bool(key), params=params))

    async def check_currents(self, client: httpx.AsyncClient) -> None:
        key = env("CURRENTS_API_KEY") or env("CURRENTS_KEY")
        params = {"apiKey": key, "category": "sports", "language": "en"}
        self.add(await self.request(client, "currents", "news", "https://api.currentsapi.services/v1/latest-news", critical=False, configured=bool(key), params=params))

    async def check_gnews(self, client: httpx.AsyncClient) -> None:
        key = env("GNEWS_KEY")
        params = {"apikey": key, "q": "football", "lang": "en", "max": 1}
        self.add(await self.request(client, "gnews", "news", "https://gnews.io/api/v4/search", critical=False, configured=bool(key), params=params))

    async def check_newsdata(self, client: httpx.AsyncClient) -> None:
        key = env("NEWSDATA_API_KEY")
        params = {"apikey": key, "q": "football", "language": "en", "size": 1}
        self.add(await self.request(client, "newsdata", "news", "https://newsdata.io/api/1/news", critical=False, configured=bool(key), params=params))

    async def check_guardian(self, client: httpx.AsyncClient) -> None:
        key = env("GUARDIAN_API_KEY")
        params = {"api-key": key, "q": "football", "section": "football", "page-size": 1}
        self.add(await self.request(client, "guardian", "news", "https://content.guardianapis.com/search", critical=False, configured=bool(key), params=params))

    async def check_sharpapi(self, client: httpx.AsyncClient) -> None:
        key = env("SHARPAPI_API_KEY")
        base = env("SHARPAPI_BASE_URL", "https://sharpapi.com").rstrip("/")
        prefix = env("SHARPAPI_API_PREFIX", "/api/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.add(await self.request(client, "sharpapi_configured_base", "utility", f"{base}{prefix}/quota", critical=False, configured=bool(key), headers=headers, details={"note": "configured base; current runtime treats sharpapi.com as text-enrichment only"}))
        if self.mode == "deep" and key:
            headers2 = {"Authorization": f"Bearer {key}"}
            self.add(await self.request(client, "sharpapi_io_odds_candidate", "odds", "https://api.sharpapi.io/api/v1/sports/events", critical=False, configured=bool(key), headers=headers2, details={"note": "checks whether this key belongs to sharpapi.io odds API; non-200 means keep disabled"}))

    async def check_futrixmetrics(self, client: httpx.AsyncClient) -> None:
        key = env("FUTRIXMETRICS_API_KEY")
        base = env("FUTRIXMETRICS_BASE_URL")
        endpoint = env("FUTRIXMETRICS_HEALTH_ENDPOINT")
        if not key:
            self.add(CheckResult("futrixmetrics", "context", "missing_secret", False, False, message="required secret is not configured"))
            return
        if not base or not endpoint:
            self.add(CheckResult("futrixmetrics", "context", "config_only", False, True, message="key present but no documented health endpoint configured", details={"set": "FUTRIXMETRICS_BASE_URL and FUTRIXMETRICS_HEALTH_ENDPOINT for live probe"}))
            return
        headers = {"Authorization": f"Bearer {key}"}
        self.add(await self.request(client, "futrixmetrics", "context", f"{base.rstrip('/')}/{endpoint.lstrip('/')}", critical=False, configured=True, headers=headers))

    def report(self) -> dict[str, Any]:
        rows = [x.to_dict() for x in sorted(self.results, key=lambda r: (r.group, r.provider))]
        critical_failures = [r for r in rows if r["critical"] and r["status"] not in {"ok", "degraded", "config_only"}]
        by_status: dict[str, int] = {}
        by_group: dict[str, dict[str, int]] = {}
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_group.setdefault(row["group"], {})
            by_group[row["group"]][row["status"]] = by_group[row["group"]].get(row["status"], 0) + 1
        return {
            "created_at_utc": now_utc().isoformat(),
            "mode": self.mode,
            "summary": {
                "providers_checked": len(rows),
                "ok": by_status.get("ok", 0),
                "degraded": by_status.get("degraded", 0),
                "rate_limited": by_status.get("rate_limited", 0),
                "auth_error": by_status.get("auth_error", 0),
                "missing_secret": by_status.get("missing_secret", 0),
                "critical_failures": len(critical_failures),
            },
            "by_status": by_status,
            "by_group": by_group,
            "critical_failures": critical_failures,
            "results": rows,
            "recommendations": build_recommendations(rows),
        }


def build_recommendations(rows: list[dict[str, Any]]) -> list[str]:
    recs: list[str] = []
    by_provider = {row["provider"]: row for row in rows}
    odds = by_provider.get("odds_api_io_events")
    if odds and odds["status"] == "ok":
        details = odds.get("details") or {}
        if not details.get("key2_present"):
            recs.append("Add/fix ODDS_API_IO_KEY_2; dual-account coverage cannot reach four bookmakers without it.")
    for name in ("bookies_api", "sportlogic"):
        row = by_provider.get(name)
        if row and row["status"] in {"ok", "degraded"}:
            recs.append(f"{name}: usable as independent odds-source candidate; enable controlled shortlist odds after matching review.")
        elif row and row["status"] == "missing_secret":
            recs.append(f"{name}: missing credentials; cannot be used as independent odds source.")
        elif row and row["status"] in {"auth_error", "rate_limited", "server_error", "error"}:
            recs.append(f"{name}: not healthy ({row['status']}); keep out of production odds until fixed.")
    if by_provider.get("oddspapi", {}).get("status") == "rate_limited":
        recs.append("ODDSPAPI is rate-limited; use only cached/shortlist mode and keep monthly budget low.")
    if by_provider.get("thesportsdb", {}).get("status") == "ok":
        recs.append("TheSportsDB is reachable; use it for team/league identity enrichment and alias registry building.")
    if not recs:
        recs.append("No automatic recommendations; inspect provider rows for low useful_rows or degraded statuses.")
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
        f"- OK: **{report['summary']['ok']}**",
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
    parser = argparse.ArgumentParser(description="Quick health run for all configured sports-bot APIs.")
    parser.add_argument("--mode", choices=["quick", "deep"], default=os.getenv("API_HEALTH_MODE", "quick"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("API_HEALTH_TIMEOUT_SECONDS", "12") or 12))
    parser.add_argument("--output-dir", default=os.getenv("API_HEALTH_OUTPUT_DIR", ".data/exports"))
    parser.add_argument("--fail-on-critical", action="store_true", default=truthy(os.getenv("API_HEALTH_FAIL_ON_CRITICAL")))
    args = parser.parse_args()

    runner = HealthRunner(mode=args.mode, timeout=args.timeout)
    report = asyncio.run(runner.run())
    write_report(report, Path(args.output_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_critical and int(report["summary"].get("critical_failures") or 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
