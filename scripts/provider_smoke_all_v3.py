from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts import provider_smoke_all_v2 as base

UTC = timezone.utc
Probe = base.Probe

# Wikimedia requires a descriptive bot/user agent. Keep it on the underlying
# v2 module because _apply_key reads base.USER_AGENT at request time.
base.USER_AGENT = "HARIZON-sports-bot-provider-smoke/3.0 (https://github.com/Harfdsfsq/sports-bot; provider diagnostics)"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _yesterday() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _tomorrow() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def _env(name: str, default: str = "") -> str:
    return str(base.os.getenv(name) or default).strip()


def _rapid_single(name: str, host_env: str, default_host: str, key_envs: tuple[str, ...], path_env: str, default_path: str, group: str) -> Probe:
    host = _env(host_env, default_host)
    path = _env(path_env, default_path)
    if not path:
        return Probe(
            name=name,
            group=group,
            url="about:blank",
            required_secret=False,
            required_envs=(path_env,),
            note=f"Set {path_env} from the provider docs/RapidAPI playground before smoke/integration.",
        )
    if not path.startswith("/"):
        path = "/" + path
    return Probe(
        name=name,
        group=group,
        url=f"https://{host}{path}",
        key_envs=key_envs,
        headers={"x-rapidapi-key": "${KEY}", "x-rapidapi-host": host},
        note=f"Configured RapidAPI endpoint. Host={host}; path_env={path_env}",
    )


def _rapid_discovery(name: str, host_env: str, default_host: str, key_envs: tuple[str, ...], path: str, group: str) -> Probe:
    host = _env(host_env, default_host)
    if not path.startswith("/"):
        path = "/" + path
    return Probe(
        name=name,
        group=group,
        url=f"https://{host}{path}",
        key_envs=key_envs,
        headers={"x-rapidapi-key": "${KEY}", "x-rapidapi-host": host},
        note=f"manual path-discovery probe; host={host}",
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
            params={"date_from": today, "date_to": tomorrow, "tz": "UTC", "page": 1, "page_size": 3, "limit": 3},
            note="reduced page_size/limit to avoid intermittent provider timeout",
        )
    return Probe(
        "bzzoiro_predictions",
        "context",
        "https://sports.bzzoiro.com/api/predictions/",
        key_envs=("BZZOIRO_API_KEY",),
        headers={"Authorization": "Token ${KEY}"},
        params={"upcoming": "true", "date_from": today, "date_to": tomorrow, "tz": "UTC", "page": 1, "page_size": 3, "limit": 3},
        note="reduced page_size/limit to avoid intermittent provider timeout",
    )


def _clubelo_probes() -> list[Probe]:
    today = _today()
    yesterday = _yesterday()
    base_url = _env("CLUBELO_BASE_URL", "http://api.clubelo.com").rstrip("/")
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


def _oddsfeed_single_probe() -> Probe:
    return _rapid_single(
        "oddsfeed_rapidapi",
        "ODDS_FEED_RAPIDAPI_HOST",
        "odds-feed.p.rapidapi.com",
        ("ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY"),
        "ODDS_FEED_RAPIDAPI_PATH",
        "",
        "odds",
    )


def _sportsbook_single_probe() -> Probe:
    return _rapid_single(
        "sportsbook_rapidapi",
        "SPORTSBOOK_RAPIDAPI_HOST",
        "sportsbook-api2.p.rapidapi.com",
        ("SPORTSBOOK_RAPIDAPI_KEY", "RAPIDAPI_KEY"),
        "SPORTSBOOK_RAPIDAPI_PATH",
        "",
        "odds",
    )


def _oddsfeed_discovery_probes() -> list[Probe]:
    paths = ["/sports", "/v1/sports", "/events", "/v1/events", "/fixtures", "/v1/fixtures", "/odds", "/v1/odds", "/prematch", "/v1/prematch"]
    return [
        _rapid_discovery(f"oddsfeed_discovery_{idx}", "ODDS_FEED_RAPIDAPI_HOST", "odds-feed.p.rapidapi.com", ("ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY"), path, "odds")
        for idx, path in enumerate(paths, start=1)
    ]


def _sportsbook_discovery_probes() -> list[Probe]:
    paths = ["/sports", "/v1/sports", "/events", "/v1/events", "/games", "/v1/games", "/odds", "/v1/odds", "/bookmakers", "/v1/bookmakers"]
    return [
        _rapid_discovery(f"sportsbook_discovery_{idx}", "SPORTSBOOK_RAPIDAPI_HOST", "sportsbook-api2.p.rapidapi.com", ("SPORTSBOOK_RAPIDAPI_KEY", "RAPIDAPI_KEY"), path, "odds")
        for idx, path in enumerate(paths, start=1)
    ]


def build_probes() -> list[Probe]:
    replaced: list[Probe] = []
    skip = {"bzzoiro_events", "bzzoiro_predictions", "clubelo_today", "clubelo_team", "oddsfeed_rapidapi", "sportsbook_rapidapi"}
    for probe in base.build_probes():
        if probe.name in skip:
            continue
        replaced.append(probe)
    replaced.extend([_replace_bzzoiro("bzzoiro_events"), _replace_bzzoiro("bzzoiro_predictions")])
    replaced.extend(_clubelo_probes())
    replaced.extend([_oddsfeed_single_probe(), _sportsbook_single_probe()])
    return replaced


def _select(probes: list[Probe], raw: str) -> list[Probe]:
    groups = {"all", "core", "odds", "context", "weather", "news", "mapping", "csv", "rapidapi"}
    core = {
        "odds_api_io_account1", "odds_api_io_account2", "bzzoiro_events", "bzzoiro_predictions",
        "sstats", "football_data", "thesportsdb", "weatherapi", "openweathermap", "allsportsapi",
        "sportlogic_games_broad", "highlightly",
    }
    tokens = [part.strip().lower() for part in str(raw or "all").split(",") if part.strip()]
    wanted: set[str] = set()
    discovery: list[Probe] = []
    for token in tokens or ["all"]:
        if token == "all":
            return probes
        if token == "core":
            wanted.update(core)
        elif token in groups:
            wanted.update(p.name for p in probes if p.group == token)
        elif token == "oddsfeed_discovery":
            discovery.extend(_oddsfeed_discovery_probes())
        elif token == "sportsbook_discovery":
            discovery.extend(_sportsbook_discovery_probes())
        else:
            wanted.add(token)
    selected = [p for p in probes if p.name.lower() in wanted or p.name.lower().replace("_account1", "") in wanted]
    return selected + discovery


_run_probe = base._run_probe
_summary = base._summary
_render_text = base._render_text
_write = base._write
_status = base._status
main = base.main
main_async = base.main_async
parse_args = base.parse_args
