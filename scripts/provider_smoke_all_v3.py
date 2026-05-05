from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from scripts import provider_smoke_all_v2 as base

UTC = timezone.utc
Probe = base.Probe


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _yesterday() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _tomorrow() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def _env(name: str, default: str = "") -> str:
    return str(base.os.getenv(name) or default).strip()


def _rapid(name: str, host_env: str, default_host: str, key_envs: tuple[str, ...], path: str, group: str) -> Probe:
    host = _env(host_env, default_host)
    if not path.startswith("/"):
        path = "/" + path
    return Probe(
        name=name,
        group=group,
        url=f"https://{host}{path}",
        key_envs=key_envs,
        headers={"x-rapidapi-key": "${KEY}", "x-rapidapi-host": host},
        note=f"path-discovery probe; host={host}; set {host_env} and matching *_PATH after one path is OK",
    )


def _replace_bzzoiro(provider: str) -> Probe:
    today = _today()
    tomorrow = _tomorrow()
    if provider == "bzzoiro_events":
        return Probe(
            "bzzoiro_events",
            "context",
            "https://sports.bzzoiro.com/api/events/",
            key_envs=("BZZOIRO_API_KEY",),
            headers={"Authorization": "Token ${KEY}"},
            params={"date_from": today, "date_to": tomorrow, "tz": "UTC", "page": 1, "page_size": 5, "limit": 5},
            note="reduced page_size/limit to avoid intermittent provider timeout",
        )
    return Probe(
        "bzzoiro_predictions",
        "context",
        "https://sports.bzzoiro.com/api/predictions/",
        key_envs=("BZZOIRO_API_KEY",),
        headers={"Authorization": "Token ${KEY}"},
        params={"upcoming": "true", "date_from": today, "date_to": tomorrow, "tz": "UTC", "page": 1, "page_size": 5, "limit": 5},
        note="reduced page_size/limit to avoid intermittent provider timeout",
    )


def _clubelo_probes() -> list[Probe]:
    today = _today()
    yesterday = _yesterday()
    base_url = _env("CLUBELO_BASE_URL", "https://api.clubelo.com").rstrip("/")
    return [
        Probe(
            "clubelo_today",
            "mapping",
            f"{base_url}/{today}",
            required_secret=False,
            note="daily CSV rating; use daily cache in runtime because provider can be slow",
        ),
        Probe(
            "clubelo_team",
            "mapping",
            f"{base_url}/Arsenal",
            required_secret=False,
            params={"from": yesterday, "to": today},
            note="narrow team history query to reduce timeout risk",
        ),
    ]


def _oddsfeed_path_probes() -> list[Probe]:
    key_envs = ("ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY")
    host = "odds-feed.p.rapidapi.com"
    configured = _env("ODDS_FEED_RAPIDAPI_PATH")
    paths = [configured] if configured else [
        "/sports",
        "/v1/sports",
        "/events",
        "/v1/events",
        "/fixtures",
        "/v1/fixtures",
        "/odds",
        "/v1/odds",
        "/prematch",
        "/v1/prematch",
    ]
    return [
        _rapid(f"oddsfeed_rapidapi_{idx}", "ODDS_FEED_RAPIDAPI_HOST", host, key_envs, path, "odds")
        for idx, path in enumerate(paths, start=1)
        if path
    ]


def _sportsbook_path_probes() -> list[Probe]:
    key_envs = ("SPORTSBOOK_RAPIDAPI_KEY", "RAPIDAPI_KEY")
    host = "sportsbook-api2.p.rapidapi.com"
    configured = _env("SPORTSBOOK_RAPIDAPI_PATH")
    paths = [configured] if configured else [
        "/sports",
        "/v1/sports",
        "/events",
        "/v1/events",
        "/games",
        "/v1/games",
        "/odds",
        "/v1/odds",
        "/bookmakers",
        "/v1/bookmakers",
    ]
    return [
        _rapid(f"sportsbook_rapidapi_{idx}", "SPORTSBOOK_RAPIDAPI_HOST", host, key_envs, path, "odds")
        for idx, path in enumerate(paths, start=1)
        if path
    ]


def build_probes() -> list[Probe]:
    replaced: list[Probe] = []
    skip = {
        "bzzoiro_events",
        "bzzoiro_predictions",
        "clubelo_today",
        "clubelo_team",
        "oddsfeed_rapidapi",
        "sportsbook_rapidapi",
    }
    for probe in base.build_probes():
        if probe.name in skip:
            continue
        replaced.append(probe)
    replaced.extend([_replace_bzzoiro("bzzoiro_events"), _replace_bzzoiro("bzzoiro_predictions")])
    replaced.extend(_clubelo_probes())
    replaced.extend(_oddsfeed_path_probes())
    replaced.extend(_sportsbook_path_probes())
    return replaced


def _select(probes: list[Probe], raw: str) -> list[Probe]:
    groups = {"all", "core", "odds", "context", "weather", "news", "mapping", "csv", "rapidapi"}
    core = {
        "odds_api_io_account1",
        "odds_api_io_account2",
        "bzzoiro_events",
        "bzzoiro_predictions",
        "sstats",
        "football_data",
        "thesportsdb",
        "weatherapi",
        "openweathermap",
        "allsportsapi",
        "sportlogic_games_broad",
        "highlightly",
    }
    tokens = [part.strip().lower() for part in str(raw or "all").split(",") if part.strip()]
    wanted: set[str] = set()
    for token in tokens or ["all"]:
        if token == "all":
            return probes
        if token == "core":
            wanted.update(core)
        elif token in groups:
            wanted.update(p.name for p in probes if p.group == token)
        else:
            wanted.add(token)
            if token == "oddsfeed_rapidapi":
                wanted.update(p.name for p in probes if p.name.startswith("oddsfeed_rapidapi_"))
            if token == "sportsbook_rapidapi":
                wanted.update(p.name for p in probes if p.name.startswith("sportsbook_rapidapi_"))
    return [p for p in probes if p.name.lower() in wanted or p.name.lower().replace("_account1", "") in wanted]


# Re-export runtime helpers used by diagnostics.
_run_probe = base._run_probe
_summary = base._summary
_render_text = base._render_text
_write = base._write
_status = base._status
main = base.main
main_async = base.main_async
parse_args = base.parse_args
