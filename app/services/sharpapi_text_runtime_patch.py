from __future__ import annotations

"""Runtime hook for optional SharpAPI Telegram text enrichment.

Two Telegram paths exist in this project:
- app.services.telegram.TelegramPublisher uses httpx;
- several scripts send directly through urllib to Telegram's sendMessage API.

This patch covers both paths without changing selection/quality logic. It only
runs when SHARPAPI_TEXT_ENRICHMENT_ENABLED=true and only for prediction-like
messages, not diagnostics/no-pick reports.
"""

import builtins
import os
from typing import Any
from urllib import parse
from urllib import request as urllib_request

PATCH_MARKER = "_harizon_sharpapi_text_runtime_patch_v1"
IMPORT_HOOK_MARKER = "_harizon_sharpapi_text_import_hook_v1"
URLOPEN_MARKER = "_harizon_sharpapi_text_urlopen_patch_v1"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _enabled() -> bool:
    return _truthy(os.getenv("SHARPAPI_TEXT_ENRICHMENT_ENABLED"))


def _looks_like_prediction_text(text: str) -> bool:
    value = str(text or "")
    if not value.strip():
        return False
    positive_markers = ("🎯 Ставка", "💸 Коэффициент", "📝 Разбор", "Сумма ставки", "Контрольная ценность")
    if not any(marker in value for marker in positive_markers):
        return False
    negative_markers = ("Подробный отчёт run", "Provider smoke", "прогнозов нет", "Проверка завершённых ставок", "Daily", "no-pick")
    return not any(marker in value for marker in negative_markers)


def _enrich_if_allowed(text: str) -> str:
    original = str(text or "").strip()
    if not _enabled() or not _looks_like_prediction_text(original):
        return original
    try:
        from app.services.sharpapi_text_enrichment import enrich_telegram_text
        return enrich_telegram_text(original)
    except Exception:
        return original


def _patch_telegram_publisher() -> bool:
    try:
        from app.services.telegram import TelegramPublisher
    except Exception:
        return False
    if getattr(TelegramPublisher, PATCH_MARKER, False):
        return False
    original = getattr(TelegramPublisher, "_send_message", None)
    if not callable(original):
        return False

    async def send_message_patched(self: Any, message: str) -> tuple[int, list[str]]:
        text = str(message or "").strip()
        if text:
            text = _enrich_if_allowed(text)
        return await original(self, text)

    TelegramPublisher._send_message = send_message_patched
    setattr(TelegramPublisher, PATCH_MARKER, True)
    return True


def _request_url(req: Any) -> str:
    try:
        return str(req.full_url)
    except Exception:
        try:
            return str(req.get_full_url())
        except Exception:
            return str(req or "")


def _patch_urlopen() -> bool:
    if getattr(urllib_request, URLOPEN_MARKER, False):
        return False
    original_urlopen = urllib_request.urlopen

    def urlopen_patched(url: Any, data: Any = None, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        try:
            target_url = _request_url(url)
            req_data = data
            if req_data is None and hasattr(url, "data"):
                req_data = getattr(url, "data", None)
            if _enabled() and "api.telegram.org" in target_url and "/sendMessage" in target_url and req_data:
                raw = req_data.decode("utf-8", errors="replace") if isinstance(req_data, (bytes, bytearray)) else str(req_data)
                values = parse.parse_qs(raw, keep_blank_values=True)
                text_values = values.get("text") or []
                if text_values:
                    original_text = text_values[0]
                    enriched = _enrich_if_allowed(original_text)
                    if enriched != original_text:
                        values["text"] = [enriched]
                        encoded = parse.urlencode(values, doseq=True).encode("utf-8")
                        if hasattr(url, "data"):
                            try:
                                url.data = encoded
                                if hasattr(url, "headers"):
                                    url.headers["Content-type"] = "application/x-www-form-urlencoded"
                                return original_urlopen(url, *args, **kwargs)
                            except Exception:
                                pass
                        data = encoded
        except Exception:
            pass
        return original_urlopen(url, data, *args, **kwargs)

    urllib_request.urlopen = urlopen_patched
    setattr(urllib_request, URLOPEN_MARKER, True)
    return True


def _install_import_hook() -> bool:
    if getattr(builtins, IMPORT_HOOK_MARKER, False):
        _patch_telegram_publisher()
        return False
    original_import = builtins.__import__

    def import_patched(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        module = original_import(name, globals, locals, fromlist, level)
        try:
            if name == "app.services.telegram" or str(name).startswith("app.services.telegram"):
                _patch_telegram_publisher()
        except Exception:
            pass
        return module

    builtins.__import__ = import_patched
    setattr(builtins, IMPORT_HOOK_MARKER, True)
    _patch_telegram_publisher()
    return True


def install() -> bool:
    changed = False
    changed = _install_import_hook() or changed
    changed = _patch_telegram_publisher() or changed
    changed = _patch_urlopen() or changed
    return changed
