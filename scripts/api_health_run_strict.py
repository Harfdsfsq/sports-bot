#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

import httpx

BASE_PATH = Path(__file__).with_name("api_health_run.py")
spec = importlib.util.spec_from_file_location("api_health_run_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load base health runner from {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

UTC = timezone.utc


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def count_rows_strict(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in (
            "data", "results", "response", "events", "matches", "fixtures", "games", "result",
            "competitions", "articles", "news", "countries", "leagues", "forecastday",
        ):
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


def _non_empty_error(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        return any(_non_empty_error(v) for v in value.values())
    if isinstance(value, list):
        return any(_non_empty_error(v) for v in value)
    return True


def _auth_like(text: str) -> bool:
    low = str(text or "").casefold()
    tokens = [
        "invalid api key", "missing application key", "missing api key", "unauthorized", "forbidden",
        "invalid token", "login", "not found", "auth", "application key", "provided api key is invalid",
    ]
    return any(token in low for token in tokens)


def semantic_status(payload: Any, body_text: str, provider: str) -> tuple[str | None, str | None]:
    text = str(body_text or "")
    low = text.casefold().strip()

    if text.lstrip().startswith("<!doctype") or text.lstrip().startswith("<html"):
        return "degraded", "html_error_page"
    if "login" in low and "not found" in low:
        return "auth_error", "provider_body_auth_error:login_not_found"
    if "invalid api key" in low or "missing application key" in low:
        return "auth_error", "provider_body_auth_error:invalid_or_missing_key"
    if "404 page not found" in low:
        return "degraded", "provider_body_not_found"

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if _non_empty_error(errors):
            serialized = json.dumps(errors, ensure_ascii=False)[:500]
            if _auth_like(serialized):
                return "auth_error", "provider_payload_auth_error"
            return "degraded", "provider_payload_errors"
        error = payload.get("error")
        if _non_empty_error(error):
            serialized = json.dumps(error, ensure_ascii=False)[:500]
            if _auth_like(serialized):
                return "auth_error", "provider_payload_auth_error"
            return "degraded", "provider_payload_error"
        success = payload.get("success")
        if success in (False, 0, "0", "false", "False"):
            serialized = json.dumps(payload, ensure_ascii=False)[:500]
            if _auth_like(serialized):
                return "auth_error", "provider_payload_success_false_auth"
            return "degraded", "provider_payload_success_false"

    return None, None


class StrictHealthRunner(base.HealthRunner):
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
    ):
        if not configured:
            return base.CheckResult(provider, group, "missing_secret", critical, False, message="required secret is not configured", endpoint=base.redact(url), details=details or {})
        expected = expected or {200}
        start = datetime.now(UTC)
        http_statuses: list[int] = []
        try:
            if method.upper() == "POST":
                response = await client.post(url, params=params, headers=headers)
            else:
                response = await client.get(url, params=params, headers=headers)
            latency_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
            http_statuses.append(response.status_code)
            body_preview = base.redact(response.text[:1200])
            try:
                payload = response.json()
            except Exception:
                payload = None
            rows = count_rows_strict(payload)
            status = "ok" if response.status_code in expected else "degraded"
            if response.status_code == 429:
                status = "rate_limited"
            elif response.status_code in {401, 403}:
                status = "auth_error"
            elif response.status_code >= 500:
                status = "server_error"
            semantic, semantic_message = semantic_status(payload, response.text, provider)
            if response.status_code in expected and semantic:
                status = semantic
            message = "ok" if status == "ok" else (semantic_message or f"http_status={response.status_code}")
            merged_details = dict(details or {})
            merged_details.update({"body_preview": body_preview, "payload_shape": self.payload_shape(payload)})
            if semantic_message:
                merged_details["semantic_message"] = semantic_message
            return base.CheckResult(provider, group, status, critical, True, 1, rows, http_statuses, latency_ms, message, self.safe_endpoint(url, params), merged_details)
        except Exception as exc:
            latency_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
            return base.CheckResult(provider, group, "error", critical, True, 1, 0, http_statuses, latency_ms, f"{exc.__class__.__name__}: {exc}", self.safe_endpoint(url, params), details or {})

    def report(self) -> dict[str, Any]:
        report = super().report()
        report["recommendations"] = build_recommendations_strict(report.get("results") or [])
        report["strict_semantic_validation"] = True
        return report


def build_recommendations_strict(rows: list[dict[str, Any]]) -> list[str]:
    recs: list[str] = []
    by_provider = {row.get("provider"): row for row in rows}

    odds = by_provider.get("odds_api_io_events")
    if odds and odds.get("status") == "ok":
        if not ((odds.get("details") or {}).get("key2_present")):
            recs.append("ODDS_API_IO_KEY_2 is missing; dual-account bookmaker coverage cannot reach 4 books.")
        else:
            recs.append("odds-api.io account1/account2 are configured; keep dual-account split active for 4-book coverage.")

    for name in ("odds_api_io_account1", "odds_api_io_account2"):
        row = by_provider.get(name)
        if row and row.get("status") != "ok":
            recs.append(f"{name}: not healthy ({row.get('status')}); check key/bookmaker entitlement.")

    api_football = by_provider.get("api_football")
    if api_football and api_football.get("status") == "auth_error":
        recs.append("api_football: current secret is not accepted by v3.football.api-sports.io. Add a real API_FOOTBALL_KEY / API-Sports key; RAPIDAPI_KEY is not enough for this endpoint.")

    bookies = by_provider.get("bookies_api")
    if bookies and bookies.get("status") == "auth_error":
        recs.append("bookies_api: login/token/base URL are not valid for the checked endpoint; fix BOOKIES_API_LOGIN/TOKEN/BASE_URL before using it as independent odds source.")
    elif bookies and bookies.get("status") in {"ok", "degraded"}:
        recs.append("bookies_api: endpoint is reachable; after matching review it can be used as independent shortlist odds source.")

    sportlogic = by_provider.get("sportlogic")
    if sportlogic and sportlogic.get("status") == "ok":
        recs.append("sportlogic: reachable and returned games; next step is controlled odds fetch on matched future fixtures.")

    oddspapi = by_provider.get("oddspapi")
    if oddspapi and oddspapi.get("status") == "auth_error":
        recs.append("oddspapi: key is invalid, not just rate-limited. Replace ODDSPAPI_API_KEY or disable provider.")
    elif oddspapi and oddspapi.get("status") == "rate_limited":
        recs.append("oddspapi: rate-limited; keep cached/shortlist-only mode.")

    highlightly = by_provider.get("highlightly")
    if highlightly and highlightly.get("status") == "degraded":
        recs.append("highlightly: configured endpoint returns HTML/error page. Fix HIGHLIGHTLY_BASE_URL/HIGHLIGHTLY_FIXTURES_PATH or RapidAPI host before using it.")

    sharp_io = by_provider.get("sharpapi_io_odds_candidate")
    if sharp_io and sharp_io.get("status") != "ok":
        recs.append("sharpapi.io odds candidate endpoint is not available with current key/path; keep SharpAPI disabled as odds source and use text enrichment only.")

    tdb = by_provider.get("thesportsdb")
    if tdb and tdb.get("status") == "ok":
        recs.append("TheSportsDB is reachable; use it for league/team identity enrichment and alias registry building.")

    if not recs:
        recs.append("No automatic recommendations; inspect low useful_rows/degraded rows manually.")
    return recs


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict health run for all configured sports-bot APIs.")
    parser.add_argument("--mode", choices=["quick", "deep"], default=os.getenv("API_HEALTH_MODE", "quick"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("API_HEALTH_TIMEOUT_SECONDS", "12") or 12))
    parser.add_argument("--output-dir", default=os.getenv("API_HEALTH_OUTPUT_DIR", ".data/exports"))
    parser.add_argument("--fail-on-critical", action="store_true", default=truthy(os.getenv("API_HEALTH_FAIL_ON_CRITICAL")))
    args = parser.parse_args()

    runner = StrictHealthRunner(mode=args.mode, timeout=args.timeout)
    report = asyncio.run(runner.run())
    base.write_report(report, Path(args.output_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_critical and int(report["summary"].get("critical_failures") or 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
