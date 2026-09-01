from __future__ import annotations

"""Last-mile Telegram safety for HARIZON picks.

Keeps weak borderline candidates in artifacts, but prevents weak or single-source
picks from being published as real Telegram bets. Blocking a Telegram message
must never crash the whole run; blocked sends are converted to successful no-op
Telegram responses and written to the audit file.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import parse as url_parse
from urllib import request as url_request

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".data" / "exports" / "latest-telegram-controlled-pick-safety.json"
TARGET_SCRIPT_NAMES = {"publish_controlled_fallback.py", "publish_controlled_fallback_guarded.py", "-"}
_ORIGINAL_URLOPEN = url_request.urlopen


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _ef(name: str, default: float) -> float:
    return _f(os.getenv(name), default)


def _ei(name: str, default: int) -> int:
    return _i(os.getenv(name), default)


def _write(event: dict[str, Any]) -> None:
    try:
        payload = json.loads(AUDIT_PATH.read_text(encoding="utf-8")) if AUDIT_PATH.exists() else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("active", True)
        payload.setdefault("events", [])
        if isinstance(payload["events"], list):
            payload["events"].append(event)
            payload["events"] = payload["events"][-160:]
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _summary(candidate: Any) -> dict[str, Any]:
    value = candidate.get("source_summary") if isinstance(candidate, dict) else getattr(candidate, "source_summary", None)
    return value if isinstance(value, dict) else {}


def _metrics(candidate: Any) -> dict[str, Any]:
    value = candidate.get("metrics") if isinstance(candidate, dict) else getattr(candidate, "metrics", None)
    return value if isinstance(value, dict) else {}


def _get(candidate: Any, name: str, default: Any = None) -> Any:
    if isinstance(candidate, dict) and candidate.get(name) not in (None, ""):
        return candidate.get(name)
    if hasattr(candidate, name):
        value = getattr(candidate, name)
        if value not in (None, ""):
            return value
    metrics = _metrics(candidate)
    if metrics.get(name) not in (None, ""):
        return metrics.get(name)
    summary = _summary(candidate)
    return summary.get(name, default) if summary.get(name) not in (None, "") else default


def _count_from_list(value: Any) -> int:
    if isinstance(value, list):
        return len({str(item).strip().lower() for item in value if str(item).strip()})
    if isinstance(value, str):
        return len({item.strip().lower() for item in re.split(r"[,+;/|]+", value) if item.strip()})
    return 0


def _sources(candidate: Any) -> int:
    s = _summary(candidate)
    m = _metrics(candidate)
    # Strict price-source count: prefer explicit odds-source fields and never let
    # context source counters (sstats/bzzoiro/espn/weather) satisfy Telegram odds-source requirements.
    explicit = [
        _get(candidate, "odds_sources_count", 0),
        _get(candidate, "independent_odds_sources_count", 0),
        s.get("odds_sources_count"),
        s.get("independent_odds_sources_count"),
        m.get("odds_sources_count"),
        m.get("independent_odds_sources_count"),
        _count_from_list(s.get("odds_sources")),
        _count_from_list(s.get("price_sources")),
        _count_from_list(s.get("selected_odds_sources")),
        _count_from_list(m.get("odds_sources")),
        _count_from_list(m.get("price_sources")),
    ]
    explicit_count = max(_i(v, 0) for v in explicit)
    if explicit_count > 0:
        return explicit_count
    # Fallback only when no explicit odds-source metadata exists.
    if s.get("selected_source") or s.get("source"):
        return 1
    return 0


def _books(candidate: Any) -> int:
    s = _summary(candidate)
    m = _metrics(candidate)
    values = [
        _get(candidate, "books_count", 0), _get(candidate, "bookmakers_count", 0),
        _get(candidate, "bookmaker_count", 0), _get(candidate, "lines_count", 0),
        s.get("books_count"), s.get("bookmakers_count"), s.get("bookmaker_count"),
        s.get("lines_count"), m.get("books_count"), m.get("bookmakers_count"),
        m.get("bookmaker_count"), m.get("lines_count"),
        _count_from_list(s.get("selected_bookmakers")), _count_from_list(s.get("bookmakers")),
        _count_from_list(s.get("books")), _count_from_list(m.get("bookmakers")),
    ]
    if s.get("selected_bookmaker") or s.get("bookmaker"):
        values.append(1)
    return max(_i(v, 0) for v in values)


def _edge(candidate: Any) -> float:
    direct = _get(candidate, "edge_pct", None)
    if direct not in (None, ""):
        return _f(direct)
    canonical = _get(candidate, "canonical_edge_pp", None)
    if canonical not in (None, ""):
        return _f(canonical)
    model = _f(_get(candidate, "adjusted_probability", 0.0))
    market = _f(_get(candidate, "consensus_probability", 0.0)) or _f(_get(candidate, "market_probability", 0.0))
    return (model - market) * 100.0 if 0 < model < 1 and 0 < market < 1 else 0.0


def _quality(candidate: Any) -> float:
    return _f(_get(candidate, "quality_score", None), _f(_summary(candidate).get("quality_score"), 0.0))


def _reasons(candidate: Any) -> list[str]:
    sources = _sources(candidate)
    books = _books(candidate)
    edge = _edge(candidate)
    ev = _f(_get(candidate, "ev_pct", _get(candidate, "canonical_ev_pct", 0.0)))
    conf = _f(_get(candidate, "confidence", 0.0))
    quality = _quality(candidate)
    reasons: list[str] = []

    min_edge = _ef("TELEGRAM_MAIN_PICK_MIN_EDGE_PP", 3.0)
    if edge < min_edge:
        reasons.append(f"main_pick_edge_below_min:{edge:.2f}/{min_edge:.2f}")

    min_sources = max(1, _ei("TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES", 1), _ei("TELEGRAM_CONTROLLED_MIN_ODDS_SOURCES", 1))
    if sources < min_sources:
        reasons.append(f"main_pick_odds_sources_below_min:{sources}/{min_sources}")
        if _b("TELEGRAM_MAIN_PICK_STRICT_SINGLE_SOURCE", True):
            min_books = _ei("TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_BOOKS", 3)
            min_single_edge = _ef("TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EDGE_PP", 4.0)
            min_ev = _ef("TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EV_PCT", 8.0)
            min_conf = _ef("TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_CONFIDENCE", 78.0)
            min_quality = _ef("TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_QUALITY", 78.0)
            ok = books >= min_books and edge >= min_single_edge and ev >= min_ev and conf >= min_conf and quality >= min_quality
            if not ok:
                reasons.append(
                    "main_pick_single_source_below_exception:"
                    f"sources={sources}/{min_sources},books={books}/{min_books},edge={edge:.2f}/{min_single_edge:.2f},"
                    f"ev={ev:.2f}/{min_ev:.2f},conf={conf:.2f}/{min_conf:.2f},quality={quality:.2f}/{min_quality:.2f}"
                )
    return reasons


def _label(candidate: Any) -> str:
    return (
        f"{_get(candidate, 'home_team', '?')} — {_get(candidate, 'away_team', '?')} | "
        f"{_get(candidate, 'family', '?')} {_get(candidate, 'selection', '?')} {_get(candidate, 'point', '')} @{_get(candidate, 'odds', '')}"
    )


def _filter_bets(bets: Any) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(bets, list) or not bets:
        return bets, []
    kept: list[Any] = []
    rejected: list[dict[str, Any]] = []
    for bet in bets:
        reasons = _reasons(bet)
        if reasons:
            rejected.append({
                "label": _label(bet), "reasons": reasons, "sources_count": _sources(bet),
                "books_count": _books(bet), "edge_pp": round(_edge(bet), 3),
                "ev_pct": round(_f(_get(bet, "ev_pct", _get(bet, "canonical_ev_pct", 0.0))), 3),
                "confidence": round(_f(_get(bet, "confidence", 0.0)), 3),
                "quality_score": round(_quality(bet), 3),
            })
        else:
            kept.append(bet)
    return kept, rejected


def _patch_publisher() -> None:
    try:
        from app.services.telegram import TelegramPublisher
    except Exception as exc:
        _write({"publisher_patch": "import_failed", "error": f"{type(exc).__name__}: {exc}"})
        return
    if getattr(TelegramPublisher, "_harizon_main_pick_safety", False):
        return
    original_publish = TelegramPublisher.publish

    async def publish_patched(self, bets, bankroll_summary=None):
        kept, rejected = _filter_bets(bets)
        if rejected:
            _write({"main_publish_filter": True, "before": len(bets or []), "after": len(kept or []), "rejected": rejected})
        if isinstance(bets, list) and bets and not kept:
            return 0, []
        return await original_publish(self, kept, bankroll_summary=bankroll_summary)

    TelegramPublisher.publish = publish_patched
    TelegramPublisher._harizon_main_pick_safety = True
    _write({"publisher_patch": "installed"})


def _pick_text(text: str) -> bool:
    low = str(text or "").lower()
    return any(token in low for token in (
        "контролируемый прогноз", "контролируемый резерв", "controlled fallback",
        "лучшая ставка", "лучшие ставки", "🛡 профиль сигнала", "🎯 ставка:",
    ))


def _text_sources(text: str) -> list[int]:
    found = [int(m.group(1)) for m in re.finditer(r"odds\s+sources\s*:?\s*(\d+)", text, re.I)]
    found += [int(m.group(1)) for m in re.finditer(r"источники\s+коэффициентов\s*:?\s*(\d+)", text, re.I)]
    return found


def _weak_profile_text(text: str) -> bool:
    low = str(text or "").lower()
    if "single-source" in low or "non-core" in low:
        return True
    return bool(re.search(r"\bc\s+[0-9]+(?:[.,][0-9]+)?\s*/\s*100\b", str(text or ""), re.I) and "quality" in low)


def _text_reasons(text: str) -> list[str]:
    if not (_pick_text(text) or _weak_profile_text(text)):
        return []
    reasons: list[str] = []
    low = str(text or "").lower()
    if _b("TELEGRAM_BLOCK_C_SIGNAL_PROFILE", True):
        if re.search(r"\bc\s+[0-9]+(?:[.,][0-9]+)?\s*/\s*100\b", text, re.I) and "quality" in low:
            reasons.append("telegram_signal_profile_c_blocked")
    if _b("TELEGRAM_BLOCK_SINGLE_SOURCE_NON_CORE", True):
        if "single-source" in low and "non-core" in low:
            reasons.append("telegram_single_source_non_core_blocked")
    min_sources = max(1, _ei("TELEGRAM_CONTROLLED_MIN_ODDS_SOURCES", 1), _ei("TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES", 1))
    source_values = _text_sources(text)
    for value in source_values:
        if value < min_sources:
            reasons.append(f"telegram_pick_odds_sources_below_min:{value}/{min_sources}")
    match = re.search(r"запас\s*([+\-]?[0-9]+(?:[.,][0-9]+)?)\s*п\.п", text, re.I)
    if match:
        edge = _f(match.group(1))
        min_edge = _ef("TELEGRAM_MAIN_PICK_MIN_EDGE_PP", 3.0)
        if edge < min_edge:
            reasons.append(f"telegram_pick_edge_below_min:{edge:.2f}/{min_edge:.2f}")
    total_line = re.search(
        r"(?:total|тотал|ставка|рынок)[^\n\r]{0,80}?\b([0-9]+(?:[.,](?:25|75)))\b",
        text,
        re.I,
    )
    if total_line:
        reasons.append(f"telegram_quarter_total_line_not_allowed:{total_line.group(1).replace(',', '.')}")
    over15 = re.search(r"Ставка:\s*Тотал\s*[—-]\s*(?:Больше|Over|ТБ)\s*\(?1[,.]5\)?", text, re.I)
    odds = re.search(r"Коэффициент:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    if over15 and odds:
        value = _f(odds.group(1))
        max_odds = _ef("TELEGRAM_TOTAL_OVER15_MAX_ODDS", 1.65)
        actual_sources = max(source_values or [0])
        if value > max_odds and actual_sources < 3:
            reasons.append(f"controlled_total_over_1_5_suspicious_price:{value:.2f}>{max_odds:.2f};sources={actual_sources}/3")
    return sorted(set(reasons))


def _format_stake_percent(text: str) -> str:
    if "% банка" in text or "💰" not in text or "Сумма ставки" not in text:
        return text
    bank = re.search(r"💼\s*Банк:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    stake = re.search(r"(💰\s*Сумма ставки:\s*)([0-9]+(?:[.,][0-9]+)?)(?:\s*\(([^)]*)\))?", text)
    if not bank or not stake:
        return text
    bank_value = _f(bank.group(1)); stake_value = _f(stake.group(2))
    if bank_value <= 0 or stake_value <= 0:
        return text
    pct = stake_value * 100.0 / bank_value
    note = str(stake.group(3) or "").strip()
    repl = f"{stake.group(1)}{stake_value:.2f} ({pct:.2f}% банка" + (f", {note})" if note else ")")
    return text[: stake.start()] + repl + text[stake.end():]


def _validate_text(text: str) -> str:
    text = _format_stake_percent(str(text or ""))
    reasons = _text_reasons(text)
    if reasons:
        _write({"blocked_text_send": True, "reasons": reasons, "text_preview": text[:1200]})
        raise RuntimeError("blocked Telegram pick: " + "; ".join(reasons))
    return text


def _extract_text(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"]
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            return ""
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
        values = url_parse.parse_qs(raw, keep_blank_values=True).get("text") or []
        return str(values[0]) if values else ""
    except Exception:
        return ""


def _fake_blocked_response(url: Any, reasons: list[str] | None = None):
    try:
        import httpx
        payload = {
            "ok": True,
            "result": {
                "message_id": 0,
                "date": 0,
                "text": "blocked_by_harizon_pick_safety",
                "blocked_by_harizon_pick_safety": True,
                "reasons": reasons or [],
            },
        }
        return httpx.Response(200, json=payload, request=httpx.Request("POST", str(url or "https://api.telegram.org/bot/sendMessage")))
    except Exception:
        return None


class _UrlopenBlockedResponse:
    def __init__(self, reasons: list[str] | None = None):
        self.reasons = reasons or []
        self.status = 200
        self.code = 200

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return json.dumps({"ok": True, "result": {"message_id": 0, "blocked_by_harizon_pick_safety": True, "reasons": self.reasons}}, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_http() -> None:
    try:
        import httpx
        if not getattr(httpx.AsyncClient, "_harizon_controlled_pick_safety", False):
            original_post = httpx.AsyncClient.post

            async def post_patched(self, url, *args, **kwargs):
                if "api.telegram.org" in str(url or "") and "sendMessage" in str(url or ""):
                    try:
                        payload = kwargs.get("json")
                        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                            payload = dict(payload); payload["text"] = _validate_text(payload["text"]); kwargs["json"] = payload
                        else:
                            text = _extract_text(kwargs.get("data"))
                            if text:
                                _validate_text(text)
                    except RuntimeError as exc:
                        text = ""
                        payload = kwargs.get("json")
                        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                            text = payload.get("text") or ""
                        else:
                            text = _extract_text(kwargs.get("data"))
                        reasons = _text_reasons(text)
                        _write({"blocked_text_send_noop": True, "transport": "httpx", "error": str(exc), "reasons": reasons, "text_preview": str(text or "")[:1200]})
                        fake = _fake_blocked_response(url, reasons)
                        if fake is not None:
                            return fake
                        raise
                return await original_post(self, url, *args, **kwargs)

            httpx.AsyncClient.post = post_patched
            httpx.AsyncClient._harizon_controlled_pick_safety = True
    except Exception:
        pass

    if not getattr(url_request, "_harizon_controlled_pick_safety", False):
        def urlopen_patched(req, *args, **kwargs):  # type: ignore[no-untyped-def]
            url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
            if "api.telegram.org" in str(url or "") and "sendMessage" in str(url or ""):
                data = getattr(req, "data", None)
                text = _extract_text(data)
                try:
                    if text:
                        new_text = _validate_text(text)
                        if new_text != text:
                            parsed = url_parse.parse_qs(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data), keep_blank_values=True)
                            parsed["text"] = [new_text]
                            encoded = url_parse.urlencode(parsed, doseq=True).encode("utf-8")
                            try:
                                req.data = encoded
                                req.headers["Content-length"] = str(len(encoded))
                            except Exception:
                                pass
                except RuntimeError as exc:
                    reasons = _text_reasons(text)
                    _write({"blocked_text_send_noop": True, "transport": "urllib", "error": str(exc), "reasons": reasons, "text_preview": str(text or "")[:1200]})
                    return _UrlopenBlockedResponse(reasons)
            return _ORIGINAL_URLOPEN(req, *args, **kwargs)

        url_request.urlopen = urlopen_patched
        url_request._harizon_controlled_pick_safety = True


def install() -> None:
    script_name = Path(sys.argv[0] or "").name
    if not _b("HARIZON_TELEGRAM_PICK_SAFETY_ENABLED", False) and script_name not in TARGET_SCRIPT_NAMES:
        return
    _write({"active": True, "target_script": script_name, "strict_odds_source_text_guard": True})
    _patch_publisher()
    _patch_http()
    try:
        print("telegram pick safety active")
    except Exception:
        pass
