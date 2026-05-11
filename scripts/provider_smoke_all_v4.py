from __future__ import annotations

from scripts import provider_smoke_all_v3 as base

Probe = base.Probe
OPTIONAL_BLOCKED_IN_ALL = {"sportapi7_rapidapi"}
OPTIONAL_PATH_PROVIDERS = {"oddsfeed_rapidapi", "sportsbook_rapidapi"}
SPORTLOGIC_PROBES = {"sportlogic_games_dated", "sportlogic_games_broad", "sportlogic_leagues"}


def _env(name: str) -> str:
    return str(base.base.os.getenv(name) or "").strip()


def _truthy(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def build_probes() -> list[Probe]:
    return base.build_probes()


def _is_pathless_optional(probe: Probe) -> bool:
    if probe.name not in OPTIONAL_PATH_PROVIDERS:
        return False
    required = tuple(getattr(probe, "required_envs", ()) or ())
    if not required:
        return False
    return any(not _env(name) for name in required)


def _is_blocked_broad_probe(probe: Probe) -> bool:
    if probe.name in OPTIONAL_BLOCKED_IN_ALL:
        return True
    if probe.name in SPORTLOGIC_PROBES and not _truthy("PROVIDER_SMOKE_SPORTLOGIC_ENABLED", False):
        return True
    return False


def _select(probes: list[Probe], raw: str) -> list[Probe]:
    tokens = [part.strip().lower() for part in str(raw or "all").split(",") if part.strip()]
    if not tokens or tokens == ["all"]:
        return [
            probe
            for probe in probes
            if not _is_blocked_broad_probe(probe) and not _is_pathless_optional(probe)
        ]
    if tokens == ["core"] and not _truthy("PROVIDER_SMOKE_SPORTLOGIC_ENABLED", False):
        selected = base._select(probes, raw)
        return [probe for probe in selected if probe.name not in SPORTLOGIC_PROBES]
    return base._select(probes, raw)


_run_probe = base._run_probe
_summary = base._summary
_render_text = base._render_text
_write = base._write
_status = base._status
main = base.main
main_async = base.main_async
parse_args = base.parse_args
