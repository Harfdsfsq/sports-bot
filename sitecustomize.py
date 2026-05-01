"""Safe repository-wide startup hook for Harizon sports-bot."""

from __future__ import annotations

import json
import re
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


def _num(value: str) -> float:
    return float(str(value or "0").replace(",", "."))


def _format_stake_percent_in_text(text: str) -> str:
    if "💰 Сумма ставки:" not in text or "% банка" in text:
        return text
    bank_match = re.search(r"💼\s*Банк:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    if not bank_match:
        return text
    try:
        bank = _num(bank_match.group(1))
    except Exception:
        return text
    if bank <= 0:
        return text

    pattern = re.compile(r"💰\s*Сумма ставки:\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*\(([^)]*)\))?")

    def repl(match: re.Match[str]) -> str:
        try:
            stake = _num(match.group(1))
        except Exception:
            return match.group(0)
        pct = stake * 100.0 / bank
        note = str(match.group(2) or "").strip()
        if note:
            return f"💰 Сумма ставки: {stake:.2f} ({pct:.2f}% банка, {note})"
        return f"💰 Сумма ставки: {stake:.2f} ({pct:.2f}% банка)"

    return pattern.sub(repl, text)


def _install_telegram_stake_percent_formatter() -> None:
    try:
        from urllib import parse
    except Exception:
        return
    if getattr(parse, "_harizon_stake_percent_patch", False):
        return
    original_urlencode = parse.urlencode

    def urlencode_patched(query, doseq=False, safe="", encoding=None, errors=None, quote_via=parse.quote_plus):
        try:
            if isinstance(query, dict) and isinstance(query.get("text"), str):
                query = dict(query)
                query["text"] = _format_stake_percent_in_text(query["text"])
            elif isinstance(query, (list, tuple)):
                updated = []
                changed = False
                for item in query:
                    if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "text" and isinstance(item[1], str):
                        updated.append((item[0], _format_stake_percent_in_text(item[1])))
                        changed = True
                    else:
                        updated.append(item)
                if changed:
                    query = updated
        except Exception:
            pass
        return original_urlencode(query, doseq=doseq, safe=safe, encoding=encoding, errors=errors, quote_via=quote_via)

    parse.urlencode = urlencode_patched
    parse._harizon_stake_percent_patch = True


try:
    _apply_provider_aliases()
    _install_telegram_stake_percent_formatter()
except Exception:
    pass
