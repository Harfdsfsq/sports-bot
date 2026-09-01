from __future__ import annotations

"""Final Telegram prepublish guard for controlled fallback.

This guard is intentionally hard and text-level. The controlled fallback script can
load a lifecycle-approved candidate, re-rank it, then send a Telegram message even
when the final report says publishable=0. Therefore the last safe place is the
Telegram sendMessage request itself.
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
AUDIT_PATH = ROOT / ".data" / "exports" / "latest-controlled-fallback-prepublish-guard.json"
TARGET_SCRIPT = "publish_controlled_fallback.py"
_ORIGINAL_URL_OPEN = url_request.urlopen


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_float(name: str, default: float) -> float:
    return _as_float(os.getenv(name), default)


def _env_int(name: str, default: int) -> int:
    return _as_int(os.getenv(name), default)


def _write_audit(payload: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_selected() -> dict[str, Any]:
    path = Path(os.getenv("CANDIDATE_LIFECYCLE_REPORT_PATH") or ".data/exports/latest-candidate-lifecycle-report.json")
    if not path.is_absolute():
        path = ROOT / path
    report = _load_json(path)
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else None
    if selected is None and isinstance(report.get("selected"), dict):
        selected = report.get("selected")
    selected = dict(selected or {})
    metrics = selected.get("last_metrics") if isinstance(selected.get("last_metrics"), dict) else {}
    selected["_metrics"] = metrics
    return selected


def _metric(selected: dict[str, Any], *names: str) -> Any:
    metrics = selected.get("_metrics") if isinstance(selected.get("_metrics"), dict) else {}
    for name in names:
        if selected.get(name) not in (None, ""):
            return selected.get(name)
        if metrics.get(name) not in (None, ""):
            return metrics.get(name)
    return None


def _telegram_text(req: Any) -> str:
    data = getattr(req, "data", None)
    if not data:
        return ""
    try:
        raw = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        parsed = url_parse.parse_qs(raw, keep_blank_values=True)
        values = parsed.get("text") or []
        return str(values[0] if values else "")
    except Exception:
        return ""


def _tier_code(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.replace("уровень", "").strip()
    if raw in {"a", "а", "tier_a", "a-tier"}:
        return "A"
    if raw in {"b", "б", "tier_b", "b-tier"}:
        return "B"
    if raw in {"c", "с", "tier_c", "c-tier"}:
        return "C"
    if raw.endswith((" a", " а")):
        return "A"
    if raw.endswith((" b", " б")):
        return "B"
    if raw.endswith((" c", " с")):
        return "C"
    return ""


def _parse_text_metrics(text: str) -> dict[str, Any]:
    low = text.lower()
    out: dict[str, Any] = {}
    m = re.search(r"odds\s*sources\s*[:=]\s*(\d+)", low)
    if m:
        out["odds_sources"] = _as_int(m.group(1))
    m = re.search(r"confirmation\s*sources\s*[:=]\s*(\d+)", low)
    if m:
        out["confirmation_sources"] = _as_int(m.group(1))
    m = re.search(r"контекст\s*[:=]\s*(\d+)", low)
    if m:
        out["context_sources"] = _as_int(m.group(1))
    m = re.search(r"подтверждени[яй]\s*[:=]\s*(\d+)", low)
    if m:
        out["confirmation_sources"] = max(_as_int(out.get("confirmation_sources")), _as_int(m.group(1)))
    m = re.search(r"качество\s+([0-9]+(?:[\.,][0-9]+)?)", low)
    if not m:
        m = re.search(r"quality\s+([0-9]+(?:[\.,][0-9]+)?)", low)
    if m:
        out["quality"] = _as_float(m.group(1))
    if "уровень b" in low or "level b" in low:
        out["tier"] = "B"
    elif "уровень c" in low or "level c" in low:
        out["tier"] = "C"
    elif "уровень a" in low or "level a" in low:
        out["tier"] = "A"
    return out


def _enhance_stake_percent(text: str) -> str:
    if "% банка" in text:
        return text
    bank_match = re.search(r"💼\s*Банк:\s*([0-9]+(?:[.,][0-9]+)?)", text)
    stake_match = re.search(r"(💰\s*Сумма ставки:\s*)([0-9]+(?:[.,][0-9]+)?)(\s*\()", text)
    if not bank_match or not stake_match:
        return text
    bank = _as_float(bank_match.group(1), 0.0)
    stake = _as_float(stake_match.group(2), 0.0)
    if bank <= 0 or stake <= 0:
        return text
    pct = stake / bank * 100.0
    replacement = f"{stake_match.group(1)}{stake_match.group(2)} ({pct:.2f}% банка, "
    return text[: stake_match.start()] + replacement + text[stake_match.end() :]


def _set_request_text(req: Any, text: str) -> Any:
    data = getattr(req, "data", None)
    if not data:
        return req
    try:
        raw = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        parsed = url_parse.parse_qs(raw, keep_blank_values=True)
        if "text" not in parsed:
            return req
        parsed["text"] = [text]
        encoded = url_parse.urlencode(parsed, doseq=True).encode("utf-8")
        req.data = encoded
        req.headers["Content-length"] = str(len(encoded))
    except Exception:
        pass
    return req


def _strict_selected_independent_odds_sources(selected: dict[str, Any]) -> int:
    # Prefer corrected independent provider counts.  Do not fall back to books_count
    # or price_sources_count: those are bookmaker/depth signals, not independent APIs.
    value = _metric(selected, "independent_odds_sources_count", "odds_sources_count")
    return _as_int(value)


def _should_block_send(text: str, selected: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    text_metrics = _parse_text_metrics(text)
    parsed_tier = _tier_code(text_metrics.get("tier") or _metric(selected, "tier", "level"))
    if parsed_tier == "B":
        # B-tier requires one independent odds provider; 2+ is A-tier.
        # Ignore any inherited global/env min=2 here so B-tier no-next-cron
        # candidates are not blocked by an A-tier publication threshold.  Context
        # confirmation still stays strict at 2 sources for Telegram publication.
        min_odds_sources = 1
        min_context_sources = max(2, _env_int("CONTROLLED_FALLBACK_TIER_B_TELEGRAM_MIN_CONTEXT_SOURCES", 2))
    elif parsed_tier == "C":
        min_odds_sources = max(1, _env_int("CONTROLLED_FALLBACK_TIER_C_TELEGRAM_MIN_ODDS_SOURCES", 1))
        min_context_sources = max(1, _env_int("CONTROLLED_FALLBACK_TIER_C_TELEGRAM_MIN_CONTEXT_SOURCES", 1))
    else:
        min_odds_sources = max(1, _env_int("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES", 2))
        min_context_sources = max(
            1,
            _env_int(
                "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES",
                _env_int("PUBLISH_MIN_CONTEXT_SOURCES", _env_int("MIN_CONTEXT_SOURCES_PUBLISH", 2)),
            ),
        )
    min_quality = _env_float("CONTROLLED_FALLBACK_TELEGRAM_MIN_QUALITY", 70.0)
    selected_independent = _strict_selected_independent_odds_sources(selected)
    if selected_independent > 0:
        odds_sources = selected_independent
    else:
        odds_sources = max(
            _as_int(text_metrics.get("odds_sources")),
            _as_int(_metric(selected, "odds_sources_count", "independent_odds_sources_count")),
        )
    context_sources = max(
        _as_int(text_metrics.get("confirmation_sources")),
        _as_int(text_metrics.get("context_sources")),
        _as_int(_metric(selected, "confirmation_sources_count", "context_sources_count", "independent_context_sources_count", "latest_confirmation_sources_max", "latest_context_sources_max")),
    )
    quality = max(
        _as_float(text_metrics.get("quality")),
        _as_float(_metric(selected, "quality", "quality_score", "publication_quality")),
    )
    tier = parsed_tier or str(text_metrics.get("tier") or _metric(selected, "tier", "level") or "").upper()
    details = {
        "text_metrics": text_metrics,
        "selected_match_key": selected.get("match_key"),
        "odds_sources": odds_sources,
        "min_odds_sources": min_odds_sources,
        "context_sources": context_sources,
        "min_context_sources": min_context_sources,
        "quality": quality,
        "min_quality": min_quality,
        "tier": tier,
        "selected_independent_odds_sources": selected_independent,
    }
    if _env_bool("CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM", True) and odds_sources < min_odds_sources:
        return True, f"telegram_price_odds_sources_below_min:{odds_sources}/{min_odds_sources}", details
    if _env_bool("CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM", True) and context_sources < min_context_sources:
        return True, f"telegram_context_sources_below_min:{context_sources}/{min_context_sources}", details
    if quality > 0 and quality < min_quality:
        return True, f"telegram_quality_below_min:{quality:.1f}/{min_quality:.1f}", details
    if tier == "B" and not _env_bool("CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B", True):
        return True, "telegram_tier_b_blocked", details
    if tier == "C" and not _env_bool("CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_C", False):
        return True, "telegram_tier_c_blocked", details
    return False, "ok", details


def install() -> None:
    if Path(sys.argv[0] or "").name != TARGET_SCRIPT:
        return
    selected = _load_selected()
    audit: dict[str, Any] = {
        "active": True,
        "selected_match_key": selected.get("match_key"),
        "telegram_stake_percent_patch": True,
        "blocked_telegram_sends": 0,
        "telegram_send_attempts": 0,
        "telegram_sends_succeeded": 0,
        "guard_version": "telegram-hard-independent-odds-context-sources-quality-v4",
    }
    _write_audit(audit)

    def guarded_urlopen(req: Any, *args: Any, **kwargs: Any) -> Any:
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
        if "api.telegram.org" not in str(url) or "sendMessage" not in str(url):
            return _ORIGINAL_URL_OPEN(req, *args, **kwargs)
        audit["telegram_send_attempts"] = int(audit.get("telegram_send_attempts") or 0) + 1
        text = _telegram_text(req)
        block, reason, details = _should_block_send(text, selected)
        audit["last_text_metrics"] = details
        audit["final_reason"] = reason
        audit["final_allowed"] = not block
        if block:
            audit["blocked_telegram_sends"] = int(audit.get("blocked_telegram_sends") or 0) + 1
            _write_audit(audit)
            raise RuntimeError(f"controlled fallback telegram send blocked by prepublish guard: {reason}")
        req = _set_request_text(req, _enhance_stake_percent(text))
        try:
            response = _ORIGINAL_URL_OPEN(req, *args, **kwargs)
            audit["telegram_sends_succeeded"] = int(audit.get("telegram_sends_succeeded") or 0) + 1
            audit["last_send_status"] = "ok"
            _write_audit(audit)
            return response
        except Exception as exc:
            audit["last_send_status"] = "error"
            audit["last_send_error"] = f"{type(exc).__name__}: {exc}"
            _write_audit(audit)
            raise

    url_request.urlopen = guarded_urlopen
    os.environ["CONTROLLED_FALLBACK_PREPUBLISH_GUARD_ACTIVE"] = "true"
    try:
        print("controlled fallback prepublish guard active: telegram-hard-independent-odds-context-sources-quality-v4")
    except Exception:
        pass
