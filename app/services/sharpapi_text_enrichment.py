from __future__ import annotations

"""Optional SharpAPI text enrichment for Telegram publications.

This module is deliberately post-selection only: it never changes markets,
odds, probabilities, stake sizing, or publication decisions. It may only polish
an already-built Telegram message. If SharpAPI is slow, unavailable, or returns
unsafe text, the original message is returned unchanged.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request

UTC = timezone.utc
EXPORT_PATH = Path(".data/exports/latest-sharpapi-text-enrichment.json")
SECRET_KEYS = ("SHARPAPI_API_KEY", "SHARPAPI_KEY", "SHARP_API_KEY")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _api_key() -> str:
    for name in SECRET_KEYS:
        value = str(os.getenv(name) or "").strip()
        if value:
            if value.lower().startswith("bearer "):
                value = value.split(" ", 1)[1].strip()
            return value
    return ""


def _write_diag(payload: dict[str, Any]) -> None:
    try:
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _base_diag(original_text: str) -> dict[str, Any]:
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "enabled": _env_bool("SHARPAPI_TEXT_ENRICHMENT_ENABLED", False),
        "api_key_present": bool(_api_key()),
        "original_chars": len(str(original_text or "")),
        "status": "skipped",
        "reason": "not_started",
        "endpoint": None,
        "http_statuses": [],
        "elapsed_ms": 0,
    }


def _headers(key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "harizon-sharpapi-text-enrichment/1.0",
    }


def _post_json(url: str, payload: dict[str, Any], key: str, timeout_seconds: int) -> tuple[int | None, dict[str, Any], dict[str, str], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=_headers(key), method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec - CI runtime integration
            body = resp.read(700_000).decode("utf-8", errors="replace")
            headers = {str(k): str(v) for k, v in resp.headers.items()}
            return int(getattr(resp, "status", 0) or 0), _safe_json(body), headers, body[:2000]
    except urlerror.HTTPError as exc:
        try:
            body = exc.read(700_000).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return int(getattr(exc, "code", 0) or 0), _safe_json(body), {}, body[:2000]
    except Exception as exc:
        return None, {}, {}, f"{type(exc).__name__}: {exc}"


def _get_json(url: str, key: str, timeout_seconds: int) -> tuple[int | None, dict[str, Any], dict[str, str], str]:
    req = request.Request(url, headers=_headers(key), method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec - CI runtime integration
            body = resp.read(700_000).decode("utf-8", errors="replace")
            headers = {str(k): str(v) for k, v in resp.headers.items()}
            return int(getattr(resp, "status", 0) or 0), _safe_json(body), headers, body[:2000]
    except urlerror.HTTPError as exc:
        try:
            body = exc.read(700_000).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return int(getattr(exc, "code", 0) or 0), _safe_json(body), {}, body[:2000]
    except Exception as exc:
        return None, {}, {}, f"{type(exc).__name__}: {exc}"


def _safe_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
        return payload if isinstance(payload, dict) else {"data": payload}
    except Exception:
        return {}


def _extract_status_url(payload: dict[str, Any]) -> str:
    for key in ("status_url", "statusUrl", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
        for key in ("status_url", "statusUrl", "url"):
            value = attrs.get(key) if isinstance(attrs, dict) else None
            if isinstance(value, str) and value.startswith("http"):
                return value
    return ""


def _extract_result_text(payload: dict[str, Any]) -> str:
    candidates: list[Any] = []
    candidates.append(payload.get("paraphrase"))
    candidates.append(payload.get("content"))
    candidates.append(payload.get("result"))
    data = payload.get("data")
    if isinstance(data, dict):
        attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
        if isinstance(attrs, dict):
            candidates.append(attrs.get("result"))
            candidates.append(attrs.get("paraphrase"))
            candidates.append(attrs.get("content"))
            result = attrs.get("result")
            if isinstance(result, dict):
                candidates.extend([result.get("paraphrase"), result.get("content"), result.get("text")])
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            for key in ("paraphrase", "content", "text", "message"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _extract_status(payload: dict[str, Any]) -> str:
    values: list[Any] = [payload.get("status")]
    data = payload.get("data")
    if isinstance(data, dict):
        attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
        if isinstance(attrs, dict):
            values.extend([attrs.get("status"), attrs.get("state")])
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _important_tokens(text: str) -> set[str]:
    # Preserve all numbers that matter in betting posts: odds, dates, percentages,
    # xG values, stake sizes, EV and edge metrics.
    return {item.replace(",", ".") for item in re.findall(r"(?<!\w)\d+(?:[\.,]\d+)?%?(?!\w)", text or "")}


def _unsafe_claims(text: str) -> list[str]:
    lowered = str(text or "").lower()
    bad_phrases = [
        "гарантирован", "100%", "сто процентов", "без риска", "верняк", "точно зайдет",
        "железобетон", "не проиграет", "обязана зайти", "обязаны зайти",
    ]
    return [phrase for phrase in bad_phrases if phrase in lowered]


def _validate_enriched(original: str, enriched: str) -> tuple[bool, str]:
    original = str(original or "").strip()
    enriched = str(enriched or "").strip()
    if not enriched:
        return False, "empty_result"
    if len(enriched) > 3900:
        return False, "telegram_text_too_long"
    if len(enriched) < max(80, int(len(original) * 0.35)):
        return False, "result_too_short"
    if len(enriched) > max(500, int(len(original) * 1.8)):
        return False, "result_too_long"
    original_tokens = _important_tokens(original)
    if original_tokens:
        enriched_tokens = _important_tokens(enriched)
        missing = sorted(original_tokens - enriched_tokens)
        allowed_missing = max(1, int(len(original_tokens) * 0.15))
        if len(missing) > allowed_missing:
            return False, "important_numbers_missing:" + ",".join(missing[:8])
    unsafe = _unsafe_claims(enriched)
    if unsafe:
        return False, "unsafe_claims:" + ",".join(unsafe[:5])
    return True, "ok"


def enrich_telegram_text(text: str) -> str:
    """Return an AI-polished Telegram text or the original text on any problem."""
    original = str(text or "").strip()
    diag = _base_diag(original)
    started = time.monotonic()

    if not _env_bool("SHARPAPI_TEXT_ENRICHMENT_ENABLED", False):
        diag.update({"status": "skipped", "reason": "disabled"})
        _write_diag(diag)
        return original
    if not original:
        diag.update({"status": "skipped", "reason": "empty_text"})
        _write_diag(diag)
        return original
    if len(original) < _env_int("SHARPAPI_TEXT_MIN_CHARS", 300):
        diag.update({"status": "skipped", "reason": "text_too_short"})
        _write_diag(diag)
        return original

    key = _api_key()
    if not key:
        diag.update({"status": "skipped", "reason": "missing_api_key"})
        _write_diag(diag)
        return original

    base_url = str(os.getenv("SHARPAPI_BASE_URL") or "https://sharpapi.com").rstrip("/")
    endpoint = str(os.getenv("SHARPAPI_TEXT_ENDPOINT") or "/api/v1/content/paraphrase").strip()
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    url = f"{base_url}{endpoint}"
    diag["endpoint"] = url

    max_chars = max(500, _env_int("SHARPAPI_TEXT_MAX_CHARS", 3200))
    timeout_seconds = max(3, min(20, _env_int("SHARPAPI_TEXT_TIMEOUT_SECONDS", 10)))
    poll_attempts = max(0, min(5, _env_int("SHARPAPI_TEXT_POLL_ATTEMPTS", 2)))
    max_retry_after = max(1, min(12, _env_int("SHARPAPI_TEXT_MAX_RETRY_AFTER_SECONDS", 8)))
    content = original[:max_chars]
    body = {
        "content": content,
        "language": os.getenv("SHARPAPI_TEXT_LANGUAGE", "Russian"),
        "max_length": max(500, min(3500, _env_int("SHARPAPI_TEXT_MAX_LENGTH", len(content) + 250))),
        "voice_tone": os.getenv("SHARPAPI_TEXT_VOICE_TONE", "confident, analytical, concise sports betting Telegram style"),
        "context": os.getenv(
            "SHARPAPI_TEXT_CONTEXT",
            "Отредактируй Telegram-прогноз на русском для канала HARIZON. Сохрани все команды, лигу, рынок, коэффициент, сумму, проценты, EV, edge, xG, даты и время без изменений. Не добавляй гарантий, обещаний прибыли или новых фактов. Улучши только стиль, читаемость и связность объяснения.",
        ),
    }

    http_statuses: list[int] = []
    status_code, payload, headers, preview = _post_json(url, body, key, timeout_seconds)
    if status_code is not None:
        http_statuses.append(status_code)
    diag["http_statuses"] = http_statuses
    diag["post_status_code"] = status_code
    diag["post_preview"] = preview[:1000]

    result_text = _extract_result_text(payload)
    if not result_text:
        status_url = _extract_status_url(payload)
        diag["status_url_present"] = bool(status_url)
        if status_url:
            for attempt in range(poll_attempts):
                retry_after_raw = headers.get("Retry-After") or os.getenv("SHARPAPI_TEXT_POLL_SLEEP_SECONDS") or "2"
                try:
                    sleep_seconds = min(max_retry_after, max(0, int(float(retry_after_raw))))
                except Exception:
                    sleep_seconds = 2
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                poll_status, poll_payload, poll_headers, poll_preview = _get_json(status_url, key, timeout_seconds)
                if poll_status is not None:
                    http_statuses.append(poll_status)
                diag["http_statuses"] = http_statuses
                diag["last_poll_status_code"] = poll_status
                diag["last_poll_preview"] = poll_preview[:1000]
                status_text = _extract_status(poll_payload)
                diag["last_job_status"] = status_text
                result_text = _extract_result_text(poll_payload)
                headers = poll_headers or headers
                if result_text or status_text in {"failed", "error"}:
                    break

    if not result_text:
        diag.update({"status": "fallback_original", "reason": "no_completed_result"})
        diag["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        _write_diag(diag)
        return original

    ok, reason = _validate_enriched(original, result_text)
    if not ok:
        diag.update({"status": "fallback_original", "reason": reason, "result_chars": len(result_text)})
        diag["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        _write_diag(diag)
        return original

    diag.update({"status": "enriched", "reason": "ok", "result_chars": len(result_text)})
    diag["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    _write_diag(diag)
    return result_text
