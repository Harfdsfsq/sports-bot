from __future__ import annotations

"""Controlled fallback publisher v2.

The original controlled fallback script has two responsibilities:
1. publish an actual controlled fallback pick;
2. send a no-pick diagnostic Telegram summary.

Since `send_harizon_telegram_run_report_v5.py` is now the single factual run
report, responsibility #2 creates duplicate Telegram messages with overlapping
and sometimes differently-normalized numbers.

This wrapper keeps actual pick publishing intact but suppresses only the generic
no-pick summary text. It does not change candidate evaluation or guards.
"""

import runpy
from urllib import request as urllib_request

_ORIGINAL_URLOPEN = urllib_request.urlopen


class _SuppressedTelegramResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b'{"ok":true,"suppressed_by":"publish_controlled_fallback_v2","reason":"duplicate_no_pick_summary"}'


def _request_body_text(req) -> str:
    try:
        data = getattr(req, "data", None)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        if data is not None:
            return str(data)
    except Exception:
        pass
    return ""


def _should_suppress(req) -> bool:
    body = _request_body_text(req)
    # URL-encoded Russian text may still contain these fragments after Telegram
    # payload construction because urllib encodes spaces but leaves many UTF-8
    # bytes escaped. Check both raw and common escaped forms.
    raw_markers = (
        "Прогнозов не было",
        "Отчёт по запуску бота",
        "Проверено резервных кандидатов",
    )
    if any(marker in body for marker in raw_markers):
        return True
    escaped_markers = (
        "%D0%9F%D1%80%D0%BE%D0%B3%D0%BD%D0%BE%D0%B7%D0%BE%D0%B2+%D0%BD%D0%B5+%D0%B1%D1%8B%D0%BB%D0%BE",
        "%D0%9E%D1%82%D1%87%D1%91%D1%82+%D0%BF%D0%BE+%D0%B7%D0%B0%D0%BF%D1%83%D1%81%D0%BA%D1%83+%D0%B1%D0%BE%D1%82%D0%B0",
        "%D0%9F%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D0%B5%D0%BD%D0%BE+%D1%80%D0%B5%D0%B7%D0%B5%D1%80%D0%B2%D0%BD%D1%8B%D1%85+%D0%BA%D0%B0%D0%BD%D0%B4%D0%B8%D0%B4%D0%B0%D1%82%D0%BE%D0%B2",
    )
    return any(marker in body for marker in escaped_markers)


def _urlopen_guard(req, *args, **kwargs):
    if _should_suppress(req):
        return _SuppressedTelegramResponse()
    return _ORIGINAL_URLOPEN(req, *args, **kwargs)


def main() -> int:
    urllib_request.urlopen = _urlopen_guard
    try:
        runpy.run_path("scripts/publish_controlled_fallback.py", run_name="__main__")
    finally:
        urllib_request.urlopen = _ORIGINAL_URLOPEN
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
