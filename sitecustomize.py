"""Safe repository-wide startup hook for Harizon sports-bot."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _apply_provider_aliases() -> None:
    try:
        from app import utils
    except Exception:
        return
    if getattr(utils, "_provider_aliases_applied", False):
        return

    path = ROOT / "config" / "provider_aliases.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    for canonical, aliases in dict(payload.get("teams") or {}).items():
        canonical_key = str(utils.canonicalize_team_name(str(canonical)))
        values = aliases if isinstance(aliases, list) else []
        for value in [canonical, *values]:
            alias_key = str(utils.normalize_text(str(value)))
            if alias_key:
                utils.TEAM_ALIAS_MAP[alias_key] = canonical_key

    base_league_normalizer = utils.canonicalize_league_name
    league_lookup: dict[str, str] = {}
    for canonical, aliases in dict(payload.get("leagues") or {}).items():
        canonical_key = str(base_league_normalizer(str(canonical)))
        values = aliases if isinstance(aliases, list) else []
        for value in [canonical, *values]:
            alias_key = str(base_league_normalizer(str(value)))
            if alias_key:
                league_lookup[alias_key] = canonical_key

    if league_lookup and not getattr(utils, "_provider_alias_league_patch_applied", False):
        def canonicalize_league_name_with_aliases(name: str) -> str:
            key = str(base_league_normalizer(str(name or "")))
            return league_lookup.get(key, key)

        utils.canonicalize_league_name = canonicalize_league_name_with_aliases
        utils._provider_alias_league_lookup = league_lookup
        utils._provider_alias_league_patch_applied = True

    utils._provider_aliases_applied = True


try:
    _apply_provider_aliases()
    try:
        from scripts.runtime_startup_patches import install_import_hook

        install_import_hook()
    except Exception:
        pass
except Exception:
    pass
