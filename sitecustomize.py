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
        # Guard against line/price mismatches: e.g. ТБ 1.5 @1.98 while xG fair is ~1.31.
        # It only blocks when the published price is far richer than the xG-derived fair price.
        if point <= 2.0 and ratio >= 1.35 and implied_gap_pp >= 18.0:
            reasons.append(
                f"total_line_price_mismatch:point={point:g},odds={odds:.2f},xg_fair={fair_xg_odds:.2f},gap={implied_gap_pp:.1f}pp"
            )
    return reasons


def _assert_no_suspicious_total_price(text: str) -> None:
    reasons = _suspicious_total_price_reasons(text)
    if reasons:
        raise RuntimeError("blocked suspicious Telegram pick: " + "; ".join(reasons))


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
                _assert_no_suspicious_total_price(query["text"])
            elif isinstance(query, (list, tuple)):
                updated = []
                changed = False
                for item in query:
                    if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "text" and isinstance(item[1], str):
                        new_text = _format_stake_percent_in_text(item[1])
                        _assert_no_suspicious_total_price(new_text)
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
        from urllib import parse, request
    except Exception:
        return
    if getattr(request, "_harizon_total_price_guard_patch", False):
        return
    original_urlopen = request.urlopen

    def _extract_text_from_request(obj) -> str:
        data = getattr(obj, "data", None)
        if data is None and isinstance(obj, (bytes, bytearray)):
            data = obj
        if data is None:
            return ""
        try:
            raw = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
            parsed = parse.parse_qs(raw)
            text_values = parsed.get("text") or []
            return str(text_values[0] or "") if text_values else ""
        except Exception:
            return ""

    def urlopen_patched(url, data=None, timeout=None, *args, **kwargs):
        target = getattr(url, "full_url", url)
        if isinstance(target, str) and "api.telegram.org" in target and "sendMessage" in target:
            text = _extract_text_from_request(url)
            if not text and data is not None:
                text = _extract_text_from_request(data)
            if text:
                _assert_no_suspicious_total_price(text)
        return original_urlopen(url, data=data, timeout=timeout, *args, **kwargs)

    request.urlopen = urlopen_patched
    request._harizon_total_price_guard_patch = True


try:
    _apply_provider_aliases()
    _install_telegram_stake_percent_formatter()
    _install_telegram_request_guard()
except Exception:
    pass
