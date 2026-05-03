"""Safe repository-wide startup hook for Harizon sports-bot."""

from __future__ import annotations

import builtins
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


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


def _apply_sportlogic_env_defaults() -> None:
    # SportLogic is currently verified as API-alive but stale-inventory for the
    # active runtime window.  Defaults must therefore be cheap probe-only values.
    # Existing explicit env values are preserved unless they are empty/disabled.
    defaults = {
        "ENABLE_SPORTLOGIC": "true",
        "SPORTLOGIC_ENABLED": "true",
        "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
        "SPORTLOGIC_HEADER_NAME": "X-API-Key",
        "SPORTLOGIC_PER_RUN_MAX": "2",
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "2",
        "SPORTLOGIC_MATCH_LIMIT": "8",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
        "SPORTLOGIC_BOOKMAKERS": "__probe_only__",
        "SPORTLOGIC_ODDS_DISABLED_REASON": "stale_inventory_probe_only",
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "2",
        "SPORTLOGIC_REQUEST_BUDGET_REASON": "stale_inventory_probe_only",
        "SPORTLOGIC_FIXTURE_CURSOR_SCAN_MAX": "1",
        "SPORTLOGIC_INVENTORY_LOOKBACK_HOURS": "48",
        "SPORTLOGIC_INVENTORY_LOOKAHEAD_DAYS": "4",
    }
    for key, value in defaults.items():
        raw = str(os.getenv(key) or "").strip()
        if not raw or raw == "0" or raw.lower() in {"false"}:
            os.environ[key] = value


def _install_sportlogic_hardening() -> None:
    try:
        from app.providers import sportlogic_hardening
        sportlogic_hardening.install()
    except Exception:
        return
    try:
        from app.providers import sportlogic_fixture_discovery_v9
        sportlogic_fixture_discovery_v9.install()
    except Exception:
        return


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


def _iter_pick_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in str(text or "").splitlines():
        if re.match(r"^\d+\.\s+", line.strip()) and current:
            blocks.append("\n".join(current))
            current = [line]
        elif current or re.match(r"^\d+\.\s+", line.strip()):
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    if not blocks and text:
        blocks = [str(text)]
    return blocks


def _suspicious_total_price_reasons(text: str) -> list[str]:
    reasons: list[str] = []
    if "Ставка: Тотал" not in text or "Коэффициент:" not in text:
        return reasons
    for block in _iter_pick_blocks(text):
        if "Ставка: Тотал" not in block:
            continue
        line_match = re.search(r"Ставка:\s*Тотал\s*[—-]\s*(?:Больше|Меньше|Over|Under)\s*\(([0-9]+(?:[.,][0-9]+)?)\)", block, re.IGNORECASE)
        odds_match = re.search(r"Коэффициент:\s*([0-9]+(?:[.,][0-9]+)?)", block)
        xg_match = re.search(r"xG-проверка:\s*ориентир\s*([0-9]+(?:[.,][0-9]+)?)%", block)
        if not (line_match and odds_match and xg_match):
            continue
        try:
            point = _num(line_match.group(1))
            odds = _num(odds_match.group(1))
            xg_probability = _num(xg_match.group(1)) / 100.0
        except Exception:
            continue
        if odds <= 1.0 or xg_probability <= 0.0:
            continue
        fair_xg_odds = 1.0 / xg_probability
        implied = 1.0 / odds
        ratio = odds / fair_xg_odds
        implied_gap_pp = (xg_probability - implied) * 100.0
        if point <= 2.0 and ratio >= 1.35 and implied_gap_pp >= 18.0:
            reasons.append(f"total_line_price_mismatch:point={point:g},odds={odds:.2f},xg_fair={fair_xg_odds:.2f},gap={implied_gap_pp:.1f}pp")
    return reasons


def _market_structure_reasons(text: str) -> list[str]:
    reasons: list[str] = []
    if not str(text or "").strip():
        return reasons
    for block in _iter_pick_blocks(text):
        block_low = block.lower()
        looks_like_pick = any(token in block for token in ("Коэффициент:", "Ставка:", "🎯", "✅ Опубликовано"))
        if not looks_like_pick:
            continue
        odds_sources_match = re.search(r"odds\s+sources\s*:?\s*(\d+)", block, re.IGNORECASE)
        if odds_sources_match:
            try:
                odds_sources = int(odds_sources_match.group(1))
            except Exception:
                odds_sources = 0
            if odds_sources < int(os.getenv("TELEGRAM_MIN_ODDS_SOURCES", "2") or 2):
                reasons.append(f"single_odds_source:{odds_sources}/2")
            continue
        books_match = re.search(r"(?:линии|линий|букмекер(?:ов|а)?|books?)\s*[: ]\s*(\d+)", block, re.IGNORECASE)
        if books_match:
            try:
                books = int(books_match.group(1))
            except Exception:
                books = 0
            min_books = int(os.getenv("TELEGRAM_SINGLE_SOURCE_MIN_BOOKS", "3") or 3)
            if books < min_books and ("контрол" in block_low or "резерв" in block_low or "коэффициент:" in block_low or "🎯" in block):
                reasons.append(f"bookmaker_lines_below_market_guard:{books}/{min_books}")
    return reasons


def _assert_publishable_market_structure(text: str) -> None:
    reasons = _suspicious_total_price_reasons(text) + _market_structure_reasons(text)
    if reasons:
        raise RuntimeError("blocked suspicious Telegram pick: " + "; ".join(reasons))


def _extract_text_from_payload_bytes(data) -> str:
    if data is None:
        return ""
    try:
        raw = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            return payload["text"]
    except Exception:
        pass
    try:
        from urllib import parse
        parsed = parse.parse_qs(raw)
        text_values = parsed.get("text") or []
        return str(text_values[0] or "") if text_values else ""
    except Exception:
        return ""


def _filter_quarantined_offer_families(offers):
    if not offers:
        return offers
    disable_spreads = _truthy(os.getenv("DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED", "true")) or not _truthy(os.getenv("SPREADS_PUBLICATION_ENABLED", "false"))
    disable_team_totals = not _truthy(os.getenv("TEAM_TOTALS_PUBLICATION_ENABLED", "false"))
    if not disable_spreads and not disable_team_totals:
        return offers
    filtered = []
    for offer in offers:
        family = str(getattr(offer, "family", "") or "").strip().lower()
        if disable_spreads and family == "spreads":
            continue
        if disable_team_totals and family == "teamtotals":
            continue
        filtered.append(offer)
    return filtered


def _patch_odds_api_io_provider() -> None:
    try:
        import app.providers.odds_api_io as odds_module
    except Exception:
        return
    cls = getattr(odds_module, "OddsApiIoProvider", None)
    if cls is None or getattr(cls, "_harizon_market_integrity_patch", False):
        return
    original = getattr(cls, "_parse_event_odds", None)
    if not callable(original):
        return

    def parse_event_odds_patched(self, payload, match):
        offers = original(self, payload, match)
        return _filter_quarantined_offer_families(offers)

    cls._parse_event_odds = parse_event_odds_patched
    cls._harizon_market_integrity_patch = True


def _patch_sportlogic_provider() -> None:
    _install_sportlogic_hardening()


def _install_provider_import_hook() -> None:
    if getattr(builtins, "_harizon_provider_integrity_import_hook", False):
        _patch_odds_api_io_provider()
        _patch_sportlogic_provider()
        return
    original_import = builtins.__import__

    def import_patched(name, globals=None, locals=None, fromlist=(), level=0):
        module = original_import(name, globals, locals, fromlist, level)
        if name == "app.providers.odds_api_io" or name.startswith("app.providers.odds_api_io"):
            _patch_odds_api_io_provider()
        if name == "app.providers.sportlogic_provider" or name.startswith("app.providers.sportlogic_provider"):
            _patch_sportlogic_provider()
        return module

    builtins.__import__ = import_patched
    builtins._harizon_provider_integrity_import_hook = True
    _patch_odds_api_io_provider()
    _patch_sportlogic_provider()


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
                _assert_publishable_market_structure(query["text"])
            elif isinstance(query, (list, tuple)):
                updated = []
                changed = False
                for item in query:
                    if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "text" and isinstance(item[1], str):
                        new_text = _format_stake_percent_in_text(item[1])
                        _assert_publishable_market_structure(new_text)
                        updated.append((item[0], new_text))
                        changed = True
                    else:
                        updated.append(item)
                if changed:
                    query = updated
        except RuntimeError:
            raise
        except Exception:
            pass
        return original_urlencode(query, doseq=doseq, safe=safe, encoding=encoding, errors=errors, quote_via=quote_via)

    parse.urlencode = urlencode_patched
    parse._harizon_stake_percent_patch = True


def _install_telegram_request_guard() -> None:
    try:
        from urllib import request
    except Exception:
        return
    if getattr(request, "_harizon_total_price_guard_patch", False):
        return
    original_urlopen = request.urlopen

    def _extract_text_from_request(obj) -> str:
        data = getattr(obj, "data", None)
        if data is None and isinstance(obj, (bytes, bytearray, str)):
            data = obj
        return _extract_text_from_payload_bytes(data)

    def urlopen_patched(url, data=None, timeout=None, *args, **kwargs):
        target = getattr(url, "full_url", url)
        if isinstance(target, str) and "api.telegram.org" in target and "sendMessage" in target:
            text = _extract_text_from_request(url)
            if not text and data is not None:
                text = _extract_text_from_request(data)
            if text:
                _assert_publishable_market_structure(text)
        return original_urlopen(url, data=data, timeout=timeout, *args, **kwargs)

    request.urlopen = urlopen_patched
    request._harizon_total_price_guard_patch = True


def _install_httpx_telegram_guard() -> None:
    try:
        import httpx
    except Exception:
        return
    if getattr(httpx.AsyncClient, "_harizon_total_price_guard_patch", False):
        return
    original_post = httpx.AsyncClient.post

    async def post_patched(self, url, *args, **kwargs):
        try:
            target = str(url or "")
            if "api.telegram.org" in target and "sendMessage" in target:
                text = ""
                json_payload = kwargs.get("json")
                if isinstance(json_payload, dict) and isinstance(json_payload.get("text"), str):
                    text = _format_stake_percent_in_text(json_payload["text"])
                    json_payload = dict(json_payload)
                    json_payload["text"] = text
                    kwargs["json"] = json_payload
                elif "data" in kwargs:
                    text = _extract_text_from_payload_bytes(kwargs.get("data"))
                if text:
                    _assert_publishable_market_structure(text)
        except RuntimeError:
            raise
        except Exception:
            pass
        return await original_post(self, url, *args, **kwargs)

    httpx.AsyncClient.post = post_patched
    httpx.AsyncClient._harizon_total_price_guard_patch = True


def _install_telegram_runtime_safety() -> None:
    try:
        from app.services import telegram_runtime_safety
        telegram_runtime_safety.install()
    except Exception:
        return


try:
    _apply_provider_aliases()
    _apply_sportlogic_env_defaults()
    _install_sportlogic_hardening()
    _install_provider_import_hook()
    _install_telegram_runtime_safety()
    _install_telegram_stake_percent_formatter()
    _install_telegram_request_guard()
    _install_httpx_telegram_guard()
except Exception:
    pass
