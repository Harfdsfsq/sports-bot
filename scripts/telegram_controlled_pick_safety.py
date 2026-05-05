from __future__ import annotations

"""Last-mile Telegram safety for controlled fallback picks.

This guard is intentionally narrow: it is active for publish_controlled_fallback.py
only.  It validates the exact Telegram payload, so a controlled pick with
`odds sources: 1` cannot be sent even if upstream candidate filtering/regression
misses it.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(__file__).resolve().parents[1] / ".data" / "exports" / "latest-telegram-controlled-pick-safety.json"
TARGET_SCRIPT = "publish_controlled_fallback.py"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    return _as_int(os.getenv(name), default)


def _env_float(name: str, default: float) -> float:
    return _as_float(os.getenv(name), default)


def _write_audit(payload: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _is_controlled_pick_text(text: str) -> bool:
    low = str(text or "").lower()
    return (
        "контролируемый прогноз" in low
        or "контролируемый резерв" in low
        or "контрольная ценность" in low
        or "controlled fallback" in low
    )


def _format_stake_percent(text: str) -> str:
    if "% банка" in text:
        return text
    if "💰" not in text or "Сумма ставки" not in text:
        return text
    bank_match = re.search(r"💼\s*Банк:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    stake_match = re.search(r"(💰\s*Сумма ставки:\s*)([0-9]+(?:[.,][0-9]+)?)(?:\s*\(([^)]*)\))?", text)
    if not bank_match or not stake_match:
        return text
    bank = _as_float(bank_match.group(1))
    stake = _as_float(stake_match.group(2))
    if bank <= 0 or stake <= 0:
        return text
    pct = stake * 100.0 / bank
    note = str(stake_match.group(3) or "").strip()
    if note:
        repl = f"{stake_match.group(1)}{stake:.2f} ({pct:.2f}% банка, {note})"
    else:
        repl = f"{stake_match.group(1)}{stake:.2f} ({pct:.2f}% банка)"
    return text[: stake_match.start()] + repl + text[stake_match.end() :]


def _extract_text_from_any_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, dict):
        text = payload.get("text")
        return str(text or "") if isinstance(text, str) else ""
    if isinstance(payload, (bytes, bytearray)):
        try:
            raw = payload.decode("utf-8")
        except Exception:
            return ""
    else:
        raw = str(payload or "")
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"]
    except Exception:
        pass
    try:
        from urllib import parse
        parsed = parse.parse_qs(raw, keep_blank_values=True)
        values = parsed.get("text") or []
        return str(values[0] or "") if values else ""
    except Exception:
        return ""


def _guard_reasons(text: str) -> list[str]:
    reasons: list[str] = []
    if not _is_controlled_pick_text(text):
        return reasons

    min_odds_sources = max(2, _env_int("TELEGRAM_CONTROLLED_MIN_ODDS_SOURCES", 2))
    odds_sources_found = [int(m.group(1)) for m in re.finditer(r"odds\s+sources\s*:?\s*(\d+)", text, re.IGNORECASE)]
    for value in odds_sources_found:
        if value < min_odds_sources:
            reasons.append(f"controlled_pick_odds_sources_below_min:{value}/{min_odds_sources}")

    # Fallback for text variants that may not expose an explicit odds-sources line.
    if not odds_sources_found:
        one_source_hints = (
            " / odds_api_io" in text
            and not any(book in text for book in ("Betfair Exchange / odds_api_io", "Sbobet / odds_api_io", "Unibet,", "Bet365,"))
        )
        if one_source_hints and "Подтверждения:" in text:
            reasons.append("controlled_pick_price_confirmation_unclear_single_odds_api_io")

    # Specific historical failure mode: total over 1.5 at an outlier price.
    total_over_15 = re.search(
        r"Ставка:\s*Тотал\s*[—-]\s*(?:Больше|Over)\s*\(?1[,.]5\)?",
        text,
        re.IGNORECASE,
    )
    odds_match = re.search(r"Коэффициент:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    if total_over_15 and odds_match:
        odds = _as_float(odds_match.group(1))
        max_reasonable = _env_float("TELEGRAM_TOTAL_OVER15_MAX_ODDS", 1.65)
        min_over15_sources = max(3, _env_int("TELEGRAM_TOTAL_OVER15_MIN_ODDS_SOURCES", 3))
        actual_sources = max(odds_sources_found or [0])
        if odds > max_reasonable and actual_sources < min_over15_sources:
            reasons.append(f"controlled_total_over_1_5_suspicious_price:{odds:.2f}>{max_reasonable:.2f};sources={actual_sources}/{min_over15_sources}")

    return reasons


def _validate_and_format_text(text: str) -> str:
    formatted = _format_stake_percent(str(text or ""))
    reasons = _guard_reasons(formatted)
    if reasons:
        _write_audit({"active": True, "blocked": True, "reasons": reasons, "text_preview": formatted[:1200]})
        raise RuntimeError("blocked controlled Telegram pick: " + "; ".join(reasons))
    return formatted


def install() -> None:
    if Path(sys.argv[0] or "").name != TARGET_SCRIPT:
        return

    _write_audit({"active": True, "blocked": False, "target_script": TARGET_SCRIPT})

    # urllib/form payloads.
    try:
        from urllib import parse, request
        if not getattr(parse, "_harizon_controlled_pick_safety", False):
            original_urlencode = parse.urlencode

            def urlencode_patched(query, doseq=False, safe="", encoding=None, errors=None, quote_via=parse.quote_plus):
                if isinstance(query, dict) and isinstance(query.get("text"), str):
                    query = dict(query)
                    query["text"] = _validate_and_format_text(query["text"])
                elif isinstance(query, (list, tuple)):
                    updated = []
                    for item in query:
                        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "text" and isinstance(item[1], str):
                            updated.append((item[0], _validate_and_format_text(item[1])))
                        else:
                            updated.append(item)
                    query = updated
                return original_urlencode(query, doseq=doseq, safe=safe, encoding=encoding, errors=errors, quote_via=quote_via)

            parse.urlencode = urlencode_patched
            parse._harizon_controlled_pick_safety = True

        if not getattr(request, "_harizon_controlled_pick_safety", False):
            original_urlopen = request.urlopen

            def urlopen_patched(url, data=None, timeout=None, *args, **kwargs):
                target = getattr(url, "full_url", url)
                if isinstance(target, str) and "api.telegram.org" in target and "sendMessage" in target:
                    text = _extract_text_from_any_payload(getattr(url, "data", None)) or _extract_text_from_any_payload(data)
                    if text:
                        _validate_and_format_text(text)
                return original_urlopen(url, data=data, timeout=timeout, *args, **kwargs)

            request.urlopen = urlopen_patched
            request._harizon_controlled_pick_safety = True
    except Exception:
        pass

    # httpx JSON payloads, used by app/services/telegram.py.
    try:
        import httpx
        if not getattr(httpx.AsyncClient, "_harizon_controlled_pick_safety", False):
            original_post = httpx.AsyncClient.post

            async def post_patched(self, url, *args, **kwargs):
                target = str(url or "")
                if "api.telegram.org" in target and "sendMessage" in target:
                    json_payload = kwargs.get("json")
                    if isinstance(json_payload, dict) and isinstance(json_payload.get("text"), str):
                        json_payload = dict(json_payload)
                        json_payload["text"] = _validate_and_format_text(json_payload["text"])
                        kwargs["json"] = json_payload
                    else:
                        text = _extract_text_from_any_payload(kwargs.get("data"))
                        if text:
                            _validate_and_format_text(text)
                return await original_post(self, url, *args, **kwargs)

            httpx.AsyncClient.post = post_patched
            httpx.AsyncClient._harizon_controlled_pick_safety = True
    except Exception:
        pass

    try:
        print("telegram controlled pick safety active")
    except Exception:
        pass
