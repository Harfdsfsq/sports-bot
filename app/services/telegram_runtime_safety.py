from __future__ import annotations

"""Telegram payload normalizer and send-state guard.

This module is installed from sitecustomize and intentionally covers every
Telegram sending path: app/services/telegram.py, urllib-based scripts and any
standalone httpx script. It fixes three production issues:

1. outgoing pick messages can mix English team/league names with Russian market
   text;
2. market labels can lose the family name ("Больше" instead of "Тотал — Больше")
   or print a team name for handicaps instead of side 1/2;
3. a blocked/failed Telegram send can still leave fallback reports saying that
   the pick was published.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
SEND_STATE = ROOT / ".data" / "exports" / "latest-telegram-send-state.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_state(status: str, text: str = "", error: str = "", transport: str = "") -> None:
    try:
        SEND_STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": _now(),
            "status": status,
            "transport": transport,
            "error": str(error or "")[:1200],
            "message_preview": str(text or "")[:1200],
            "looks_like_pick": _looks_like_pick_message(text),
        }
        SEND_STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _read_recent_state(max_age_minutes: int = 45) -> dict[str, Any]:
    try:
        payload = json.loads(SEND_STATE.read_text(encoding="utf-8"))
        created = str(payload.get("created_at") or "")
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        if dt.astimezone(UTC) < datetime.now(UTC) - timedelta(minutes=max_age_minutes):
            return {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _looks_like_pick_message(text: Any) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    if "Подробный отчёт run" in value or "API / квоты" in value:
        return False
    return (
        "🎯" in value
        and ("Коэффициент:" in value or re.search(r"@\s*\d", value) is not None)
        and ("Ставка" in value or "лучш" in value.lower() or "контрол" in value.lower() or "резерв" in value.lower())
    )


def _normalize_i18n(text: str) -> str:
    try:
        from app.services.telegram_i18n import normalize_telegram_text
        return normalize_telegram_text(text)
    except Exception:
        return text


def _translate_team(text: Any) -> str:
    try:
        from app.services.telegram_i18n import translate_team_name
        return translate_team_name(text)
    except Exception:
        return str(text or "")


def _translate_league(text: Any) -> str:
    try:
        from app.services.telegram_i18n import translate_league_name
        return translate_league_name(text)
    except Exception:
        return str(text or "")


def _split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in str(text or "").splitlines():
        if re.match(r"^\s*\d+\.\s+", line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _extract_match_teams(block: str) -> tuple[str, str, str, str]:
    for line in block.splitlines():
        match = re.match(r"^\s*\d+\.\s+(.+?)\s+[—-]\s+(.+?)\s*$", line.strip())
        if match:
            home_raw = match.group(1).strip()
            away_raw = match.group(2).strip()
            return home_raw, away_raw, _translate_team(home_raw), _translate_team(away_raw)
    return "", "", "", ""


def _replace_known_teams(block: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str) -> str:
    result = block
    pairs = [(home_raw, home_ru), (away_raw, away_ru)]
    # Also handle already-transliterated forms produced by the base i18n layer.
    for raw, ru in pairs:
        if not raw or not ru:
            continue
        for candidate in {raw, _normalize_i18n(raw), _translate_team(raw)}:
            if candidate and candidate != ru:
                result = re.sub(re.escape(candidate), ru, result, flags=re.IGNORECASE)
    return result


def _point_text(value: Any, default: str = "0") -> str:
    raw = str(value if value not in (None, "") else default).strip()
    raw = raw.replace(",", ".")
    try:
        number = float(raw)
        if abs(number) < 1e-9:
            return "0"
        text = f"{number:g}"
        return text if text.startswith("-") else f"+{text}"
    except Exception:
        return raw or default


def _extract_point(selection: str, fallback: str = "") -> str:
    match = re.search(r"\(([+\-]?\d+(?:[.,]\d+)?)\)", selection)
    if match:
        return _point_text(match.group(1), default=fallback or "0")
    match = re.search(r"\b([+\-]?\d+(?:[.,]\d+)?)\b", selection)
    if match and fallback:
        return _point_text(match.group(1), default=fallback)
    return fallback


def _side_from_selection(selection: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str) -> str:
    low = selection.lower()
    for token in ("ф1", "п1", "home", "хозя", " 1", "1 "):
        if token in low:
            return "1"
    for token in ("ф2", "п2", "away", "гост", " 2", "2 "):
        if token in low:
            return "2"
    for home in [home_ru, home_raw, _normalize_i18n(home_raw), _translate_team(home_raw)]:
        if home and home.lower() in low:
            return "1"
    for away in [away_ru, away_raw, _normalize_i18n(away_raw), _translate_team(away_raw)]:
        if away and away.lower() in low:
            return "2"
    return ""


def _total_selection(selection: str) -> str:
    low = selection.lower()
    point = _extract_point(selection, fallback="")
    suffix = f" ({point.lstrip('+')})" if point else ""
    if any(token in low for token in ("больше", "over", "тб")):
        return f"Тотал — Больше{suffix}"
    if any(token in low for token in ("меньше", "under", "тм")):
        return f"Тотал — Меньше{suffix}"
    return f"Тотал — {selection.strip()}"


def _handicap_selection(selection: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str) -> str:
    side = _side_from_selection(selection, home_raw, away_raw, home_ru, away_ru)
    point = _extract_point(selection, fallback="0")
    if side:
        return f"Фора — Ф{side}({point})"
    return f"Фора — {selection.strip()}"


def _btts_selection(selection: str) -> str:
    low = selection.lower()
    if any(token in low for token in ("yes", "да")):
        return "Обе забьют — Да"
    if any(token in low for token in ("no", "нет")):
        return "Обе забьют — Нет"
    return f"Обе забьют — {selection.strip()}"


def _h2h_selection(selection: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str) -> str:
    side = _side_from_selection(selection, home_raw, away_raw, home_ru, away_ru)
    low = selection.lower().strip()
    if side == "1":
        return "Исход — П1"
    if side == "2":
        return "Исход — П2"
    if low in {"draw", "x", "ничья"} or "нич" in low:
        return "Исход — Ничья"
    return f"Исход — {selection.strip()}"


def _repair_market_phrase(selection: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str, market_hint: str = "") -> str:
    value = str(selection or "").strip()
    hint = f"{market_hint} {value}".lower()
    if not value:
        return value
    if "тотал" in hint or "over" in hint or "under" in hint or "больше" in hint or "меньше" in hint or "тб" in hint or "тм" in hint:
        return _total_selection(value)
    if "фора" in hint or "spread" in hint or "draw no bet" in hint or "dnb" in hint or re.search(r"\([+\-]?\d", value):
        # Prefer handicap if the selection points to one of the teams.
        if _side_from_selection(value, home_raw, away_raw, home_ru, away_ru):
            return _handicap_selection(value, home_raw, away_raw, home_ru, away_ru)
    if "обе" in hint or "btts" in hint or "both teams" in hint:
        return _btts_selection(value)
    if "исход" in hint or "winner" in hint or "moneyline" in hint or "h2h" in hint:
        return _h2h_selection(value, home_raw, away_raw, home_ru, away_ru)
    return value


def _repair_market_line(line: str, home_raw: str, away_raw: str, home_ru: str, away_ru: str) -> str:
    original = line
    # Full forecast form: "🎯 Ставка: Фора — Team (0)".
    match = re.match(r"^(\s*🎯\s*Ставка:\s*)(.*?)(?:\s+[—-]\s+)(.+?)\s*$", line)
    if match:
        prefix, market, selection = match.groups()
        repaired = _repair_market_phrase(selection, home_raw, away_raw, home_ru, away_ru, market_hint=market)
        return f"{prefix}{repaired}"

    # Detailed report/near-miss form: "🎯 Team (0) @2.25" or "🎯 Больше @1.88".
    match = re.match(r"^(\s*🎯\s*)(.+?)(\s*@\s*\d+(?:[.,]\d+)?\s*)$", line)
    if match:
        prefix, selection, suffix = match.groups()
        repaired = _repair_market_phrase(selection, home_raw, away_raw, home_ru, away_ru)
        return f"{prefix}{repaired}{suffix}"

    # Compact form without price in detailed report: "🎯 Больше".
    if "🎯" in line:
        prefix, _, rest = line.partition("🎯")
        repaired = _repair_market_phrase(rest.strip(), home_raw, away_raw, home_ru, away_ru)
        if repaired != rest.strip():
            return f"{prefix}🎯 {repaired}"
    return original


def _repair_block(block: str) -> str:
    home_raw, away_raw, home_ru, away_ru = _extract_match_teams(block)
    block = _replace_known_teams(block, home_raw, away_raw, home_ru, away_ru)
    lines: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        team_line = re.match(r"^(\s*\d+\.\s+)(.+?)\s+[—-]\s+(.+?)\s*$", line)
        if team_line:
            lines.append(f"{team_line.group(1)}{home_ru or _translate_team(team_line.group(2))} — {away_ru or _translate_team(team_line.group(3))}")
            continue
        if stripped.startswith("🏆 Турнир:"):
            prefix, _, league = line.partition(":")
            lines.append(f"{prefix}: {_translate_league(league.strip())}")
            continue
        if stripped.startswith("🏆 "):
            prefix = line[: line.index("🏆") + 2]
            league = line[line.index("🏆") + 2 :].strip()
            lines.append(f"{prefix} {_translate_league(league)}")
            continue
        if "🎯" in line:
            lines.append(_repair_market_line(line, home_raw, away_raw, home_ru, away_ru))
            continue
        lines.append(line)
    return "\n".join(lines)


def _apply_send_state_to_report(text: str) -> str:
    if "Подробный отчёт run" not in text:
        return text
    state = _read_recent_state()
    if not state:
        return text
    status = str(state.get("status") or "")
    if status not in {"blocked", "send_failed"}:
        return text
    reason = str(state.get("error") or status).strip()
    result = text.replace("🧾 Подробный отчёт run — прогноз опубликован", "🧾 Подробный отчёт run — прогноз выбран, но не отправлен")
    result = result.replace("✅ Опубликовано", "⚠️ Выбрано, но не отправлено")
    result = result.replace("• Система штатно опубликовала контролируемый прогноз", "• Прогноз был выбран, но Telegram-отправка не подтверждена")
    note = f"\n\n⚠️ Telegram-публикация прогноза не подтверждена: {reason[:500]}"
    if "Telegram-публикация прогноза не подтверждена" not in result:
        result += note
    return result


def normalize_outgoing_telegram_text(text: Any) -> str:
    value = str(text or "")
    if not value.strip():
        return ""
    value = _normalize_i18n(value)
    blocks = [_repair_block(block) for block in _split_blocks(value)]
    value = "\n".join(blocks)
    value = _apply_send_state_to_report(value)
    return value.strip()


def _extract_text_from_bytes(data: Any) -> str:
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
            return str(payload.get("text") or "")
    except Exception:
        pass
    try:
        parsed = urlparse.parse_qs(raw)
        values = parsed.get("text") or []
        return str(values[0] or "") if values else ""
    except Exception:
        return ""


def _replace_text_in_encoded_data(data: Any, new_text: str) -> Any:
    if data is None:
        return data
    is_bytes = isinstance(data, (bytes, bytearray))
    raw = data.decode("utf-8") if is_bytes else str(data)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and "text" in payload:
            payload["text"] = new_text
            out = json.dumps(payload, ensure_ascii=False)
            return out.encode("utf-8") if is_bytes else out
    except Exception:
        pass
    parsed = urlparse.parse_qs(raw, keep_blank_values=True)
    if "text" not in parsed:
        return data
    parsed["text"] = [new_text]
    out = urlparse.urlencode(parsed, doseq=True)
    return out.encode("utf-8") if is_bytes else out


def _is_telegram_send_url(url: Any) -> bool:
    target = getattr(url, "full_url", url)
    return isinstance(target, str) and "api.telegram.org" in target and "sendMessage" in target


def install() -> None:
    try:
        from urllib import parse, request
        import httpx
    except Exception:
        return

    if not getattr(parse, "_harizon_runtime_i18n_patch", False):
        original_urlencode = parse.urlencode

        def urlencode_patched(query, doseq=False, safe="", encoding=None, errors=None, quote_via=parse.quote_plus):
            try:
                if isinstance(query, dict) and isinstance(query.get("text"), str):
                    query = dict(query)
                    query["text"] = normalize_outgoing_telegram_text(query["text"])
                elif isinstance(query, (list, tuple)):
                    updated = []
                    changed = False
                    for item in query:
                        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "text" and isinstance(item[1], str):
                            updated.append((item[0], normalize_outgoing_telegram_text(item[1])))
                            changed = True
                        else:
                            updated.append(item)
                    if changed:
                        query = updated
                return original_urlencode(query, doseq=doseq, safe=safe, encoding=encoding, errors=errors, quote_via=quote_via)
            except RuntimeError as exc:
                _write_state("blocked", str(query), str(exc), "urlencode")
                raise

        parse.urlencode = urlencode_patched
        parse._harizon_runtime_i18n_patch = True

    if not getattr(request, "_harizon_runtime_i18n_patch", False):
        original_urlopen = request.urlopen

        def urlopen_patched(url, data=None, timeout=None, *args, **kwargs):
            text = ""
            try:
                if _is_telegram_send_url(url):
                    text = _extract_text_from_bytes(getattr(url, "data", None)) or _extract_text_from_bytes(data)
                    if text:
                        normalized = normalize_outgoing_telegram_text(text)
                        if getattr(url, "data", None) is not None:
                            try:
                                url.data = _replace_text_in_encoded_data(getattr(url, "data", None), normalized)
                            except Exception:
                                pass
                        elif data is not None:
                            data = _replace_text_in_encoded_data(data, normalized)
                        text = normalized
                response = original_urlopen(url, data=data, timeout=timeout, *args, **kwargs)
                if text and _looks_like_pick_message(text):
                    _write_state("sent_success", text, "", "urllib")
                return response
            except RuntimeError as exc:
                if text and _looks_like_pick_message(text):
                    _write_state("blocked", text, str(exc), "urllib")
                raise
            except Exception as exc:
                if text and _looks_like_pick_message(text):
                    _write_state("send_failed", text, f"{type(exc).__name__}: {exc}", "urllib")
                raise

        request.urlopen = urlopen_patched
        request._harizon_runtime_i18n_patch = True

    if not getattr(httpx.AsyncClient, "_harizon_runtime_i18n_patch", False):
        original_post = httpx.AsyncClient.post

        async def post_patched(self, url, *args, **kwargs):
            text = ""
            try:
                if _is_telegram_send_url(str(url or "")):
                    json_payload = kwargs.get("json")
                    if isinstance(json_payload, dict) and isinstance(json_payload.get("text"), str):
                        text = normalize_outgoing_telegram_text(json_payload.get("text") or "")
                        json_payload = dict(json_payload)
                        json_payload["text"] = text
                        kwargs["json"] = json_payload
                    elif "data" in kwargs:
                        raw_text = _extract_text_from_bytes(kwargs.get("data"))
                        if raw_text:
                            text = normalize_outgoing_telegram_text(raw_text)
                            kwargs["data"] = _replace_text_in_encoded_data(kwargs.get("data"), text)
                response = await original_post(self, url, *args, **kwargs)
                if text and _looks_like_pick_message(text):
                    if int(getattr(response, "status_code", 0) or 0) >= 400:
                        _write_state("send_failed", text, f"http_status={getattr(response, 'status_code', None)}", "httpx")
                    else:
                        _write_state("sent_success", text, "", "httpx")
                return response
            except RuntimeError as exc:
                if text and _looks_like_pick_message(text):
                    _write_state("blocked", text, str(exc), "httpx")
                raise
            except Exception as exc:
                if text and _looks_like_pick_message(text):
                    _write_state("send_failed", text, f"{type(exc).__name__}: {exc}", "httpx")
                raise

        httpx.AsyncClient.post = post_patched
        httpx.AsyncClient._harizon_runtime_i18n_patch = True
