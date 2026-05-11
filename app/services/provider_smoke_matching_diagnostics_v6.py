from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v3 as upstream

ADAPTER_VERSION = "v6_preserve_sportlogic_active_odds_diagnostics_no_recursion"
_MARK = "_harizon_provider_smoke_matching_diagnostics_v6_installed"

DIAG_KEYS = (
    "adapter_version",
    "documented_adapter_status",
    "documented_adapter_error",
    "documented_active_odds_rows",
    "documented_active_odds_pages_scanned",
    "documented_active_game_ids_checked",
    "documented_active_odds_sample_keys",
    "documented_active_id_candidates_sample",
    "documented_active_game_samples_all",
    "documented_adapter_stats",
    "documented_adapter_preview",
)


def install() -> None:
    upstream.install()
    if getattr(base, _MARK, False):
        return
    original_match = base._match_provider_to_inventory

    def patched_match(provider_payload: dict[str, Any], inventory: list[Any]) -> dict[str, Any]:
        result = original_match(provider_payload, inventory)
        for key in DIAG_KEYS:
            value = provider_payload.get(key)
            if value not in (None, "", [], {}):
                result[key] = value
        if str(provider_payload.get("provider") or "") == "sportlogic":
            result["adapter_version"] = ADAPTER_VERSION
        return result

    base._match_provider_to_inventory = patched_match
    setattr(base, _MARK, True)


def _mark(payload: dict[str, Any]) -> None:
    payload["matching_adapter_version"] = ADAPTER_VERSION
    try:
        base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    try:
        text = base.MATCH_TXT.read_text(encoding="utf-8") if base.MATCH_TXT.exists() else ""
        marker = f"• matching_adapter_version: {ADAPTER_VERSION}"
        if marker not in text:
            lines = text.splitlines()
            if lines and lines[0].startswith("🧬 Provider matching diagnostics"):
                lines.insert(1, marker)
                text = "\n".join(lines) + "\n"
            else:
                text = marker + "\n" + text
            base.MATCH_TXT.write_text(text, encoding="utf-8")
    except Exception:
        pass


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    try:
        payload = await asyncio.wait_for(base.run(timeout_seconds=timeout_seconds), timeout=95.0)
    except Exception as exc:
        payload = {"mode": "provider_smoke_matching_diagnostics", "status": "failed_or_timeout", "error": f"{type(exc).__name__}: {exc}"}
        try:
            base.MATCH_TXT.write_text("🧬 Provider matching diagnostics\n" f"• matching_adapter_version: {ADAPTER_VERSION}\n" f"• status: failed_or_timeout\n• error: {payload['error']}\n", encoding="utf-8")
        except Exception:
            pass
    _mark(payload)
    return payload
