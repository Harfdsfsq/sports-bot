from __future__ import annotations

from scripts import provider_smoke_all_v3 as base

Probe = base.Probe
OPTIONAL_BLOCKED_IN_ALL = {"sportapi7_rapidapi"}
OPTIONAL_PATH_PROVIDERS = {"oddsfeed_rapidapi", "sportsbook_rapidapi"}


def _env(name: str) -> str:
    return str(base.base.os.getenv(name) or "").strip()


def build_probes() -> list[Probe]:
    return base.build_probes()


def _is_pathless_optional(probe: Probe) -> bool:
    if probe.name not in OPTIONAL_PATH_PROVIDERS:
        return False
    required = tuple(getattr(probe, "required_envs", ()) or ())
    if not required:
        return False
    return any(not _env(name) for name in required)


def _select(probes: list[Probe], raw: str) -> list[Probe]:
    tokens = [part.strip().lower() for part in str(raw or "all").split(",") if part.strip()]
    if not tokens or tokens == ["all"]:
        return [
            probe
            for probe in probes
            if probe.name not in OPTIONAL_BLOCKED_IN_ALL and not _is_pathless_optional(probe)
        ]
    return base._select(probes, raw)


_run_probe = base._run_probe
_summary = base._summary
_render_text = base._render_text
_write = base._write
_status = base._status
main = base.main
main_async = base.main_async
parse_args = base.parse_args
