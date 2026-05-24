from __future__ import annotations

"""Suppress legacy no-pick Telegram summaries.

The factual run report is now sent by `send_harizon_telegram_run_report_v5.py`.
`publish_controlled_fallback.py` may still send an older generic no-pick message
before v5. This patch intercepts only those no-pick sendMessage calls and leaves
actual pick messages untouched.

When installed explicitly before report/fallback sending, it also installs the
core source coverage runtime patch before the runner imports providers.
"""

from urllib import parse
from urllib import request as urllib_request

_INSTALLED = False
_CORE_PATCH_INSTALLED = False
_ORIGINAL_URLOPEN = urllib_request.urlopen


class _SuppressedResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b'{"ok":true,"suppressed_by":"telegram_no_pick_summary_suppressor"}'


NO_PICK_MARKERS = (
    "Отчёт по запуску бота",
    "Прогнозов не было",
    "Основной слой качества не нашёл чистую ставку",
    "Проверено резервных кандидатов",
)


def _body_text(req) -> str:
    try:
        data = getattr(req, "data", None)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        if data is not None:
            return str(data)
    except Exception:
        pass
    return ""


def _decoded_body(req) -> str:
    body = _body_text(req)
    try:
        return parse.unquote_plus(body)
    except Exception:
        return body


def _should_suppress(req) -> bool:
    try:
        url = str(getattr(req, "full_url", "") or getattr(req, "get_full_url", lambda: "")())
    except Exception:
        url = ""
    if "sendMessage" not in url:
        return False
    decoded = _decoded_body(req)
    return any(marker in decoded for marker in NO_PICK_MARKERS)


def _urlopen(req, *args, **kwargs):
    if _should_suppress(req):
        return _SuppressedResponse()
    return _ORIGINAL_URLOPEN(req, *args, **kwargs)


def _install_core_source_coverage() -> dict[str, object]:
    global _CORE_PATCH_INSTALLED
    if _CORE_PATCH_INSTALLED:
        return {"status": "already_installed"}
    result: dict[str, object] = {}
    try:
        from app.services.core_source_coverage_runtime_patch import install as install_core_patch
        install_core_patch()
        result["core_source_coverage"] = "installed"
    except Exception as exc:
        result["core_source_coverage"] = f"failed:{type(exc).__name__}: {exc}"
    try:
        from app.services.core_source_coverage_fixups import install as install_fixups
        result["core_source_fixups"] = install_fixups()
    except Exception as exc:
        result["core_source_fixups"] = f"failed:{type(exc).__name__}: {exc}"
    _CORE_PATCH_INSTALLED = True
    return result


def install() -> dict[str, object]:
    global _INSTALLED
    core_status = _install_core_source_coverage()
    if _INSTALLED:
        return {"status": "already_installed", "core_source_coverage": core_status}
    urllib_request.urlopen = _urlopen
    _INSTALLED = True
    return {
        "status": "installed",
        "suppresses": "legacy_no_pick_summary_only",
        "core_source_coverage": core_status,
    }
