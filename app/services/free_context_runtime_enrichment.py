from __future__ import annotations

"""Free-provider runtime enrichment.

This patch is intentionally conservative.  It does not create betting prices and
never confirms odds.  It prepares cacheable context/mapping/weather/news signals
from providers that passed provider-smoke, and writes a runtime report so the
next normal run shows whether the integrations are active.
"""

import asyncio
import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PATCH_MARKER = "_harizon_free_context_runtime_enrichment_v1"
CACHE_DIR = Path(".data/cache/free_context")
EXPORT_DIR = Path(".data/exports")


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _season_code() -> str:
    now = datetime.now(UTC)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_today()}-{name}.json"


def _csv_rows(text: str, limit: int = 200) -> list[dict[str, Any]]:
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
        return [dict(row) for row in rows[:limit] if isinstance(row, dict)]
    except Exception:
        return []


async def _get_text(client: Any, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None, timeout: float = 12.0) -> tuple[int | None, str]:
    try:
        response = await client.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
        return int(response.status_code), response.text
    except Exception as exc:
        return None, f"ERROR:{type(exc).__name__}:{exc}"


async def _fetch_json_or_text(client: Any, name: str, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None, timeout: float = 12.0) -> dict[str, Any]:
    path = _cache_path(name)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    status, text = await _get_text(client, url, headers=headers, params=params, timeout=timeout)
    payload: Any
    try:
        payload = json.loads(text)
    except Exception:
        payload = text
    out = {"provider": name, "status": status, "fetched_at": datetime.now(UTC).isoformat(), "payload": payload}
    try:
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    return out


async def _collect_free_context() -> dict[str, Any]:
    import httpx

    report: dict[str, Any] = {"enabled": True, "created_at_utc": datetime.now(UTC).isoformat(), "providers": {}}
    timeout = float(os.getenv("FREE_CONTEXT_RUNTIME_TIMEOUT_SECONDS") or 12.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers={"User-Agent": "HARIZON-sports-bot-runtime-free-context/1.0"}) as client:
        if _truthy(os.getenv("CLUBELO_ENABLED"), True):
            base = str(os.getenv("CLUBELO_BASE_URL") or "http://api.clubelo.com").rstrip("/")
            item = await _fetch_json_or_text(client, "clubelo_today", f"{base}/{_today()}", timeout=timeout)
            rows = _csv_rows(str(item.get("payload") or ""), limit=300) if isinstance(item.get("payload"), str) else []
            report["providers"]["clubelo_today"] = {"status": item.get("status"), "rows": len(rows), "cache": str(_cache_path("clubelo_today"))}
        if _truthy(os.getenv("FOOTBALL_DATA_CO_UK_ENABLED"), True):
            url = f"https://www.football-data.co.uk/mmz4281/{_season_code()}/E0.csv"
            item = await _fetch_json_or_text(client, "football_data_co_uk_epl", url, timeout=timeout)
            rows = _csv_rows(str(item.get("payload") or ""), limit=300) if isinstance(item.get("payload"), str) else []
            report["providers"]["football_data_co_uk_epl"] = {"status": item.get("status"), "rows": len(rows), "cache": str(_cache_path("football_data_co_uk_epl"))}
        if _truthy(os.getenv("OPEN_METEO_ENABLED"), True):
            item = await _fetch_json_or_text(
                client,
                "open_meteo_london_sample",
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": 51.5072, "longitude": -0.1276, "hourly": "temperature_2m,precipitation,wind_speed_10m", "forecast_days": 1},
                timeout=timeout,
            )
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            hourly = payload.get("hourly") if isinstance(payload, dict) else None
            rows = len(next(iter(hourly.values()))) if isinstance(hourly, dict) and hourly else 0
            report["providers"]["open_meteo"] = {"status": item.get("status"), "rows": rows, "cache": str(_cache_path("open_meteo_london_sample"))}
        if _truthy(os.getenv("WIKIDATA_ENABLED"), True):
            item = await _fetch_json_or_text(
                client,
                "wikidata_arsenal_entity",
                "https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/Q9617",
                params={"language": "en"},
                timeout=timeout,
            )
            report["providers"]["wikidata_entity"] = {"status": item.get("status"), "cache": str(_cache_path("wikidata_arsenal_entity"))}
    return report


def _write_report(report: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        (EXPORT_DIR / "latest-free-context-runtime-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = ["🧩 Free context runtime enrichment", f"• UTC: {report.get('created_at_utc')}"]
        for name, info in sorted((report.get("providers") or {}).items()):
            lines.append(f"• {name}: status={info.get('status')} rows={info.get('rows', '')} cache={info.get('cache')}")
        (EXPORT_DIR / "latest-free-context-runtime-report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> bool:
    if not _truthy(os.getenv("FREE_CONTEXT_RUNTIME_ENABLED"), True):
        return False
    # In the normal runner this executes at startup and writes a cache/report.
    # If an event loop is already running, skip instead of risking runtime failure.
    try:
        asyncio.get_running_loop()
        return False
    except RuntimeError:
        pass
    try:
        _write_report(asyncio.run(_collect_free_context()))
        return True
    except Exception as exc:
        _write_report({"enabled": True, "error": f"{type(exc).__name__}: {exc}", "created_at_utc": datetime.now(UTC).isoformat(), "providers": {}})
        return False
