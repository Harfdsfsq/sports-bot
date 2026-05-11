from __future__ import annotations

import json
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v3 as v3

ADAPTER_VERSION = "v4_sportlogic_documented_adapter_marker"


def install() -> None:
    v3.install()


def _mark_text() -> None:
    marker = f"• matching_adapter_version: {ADAPTER_VERSION}"
    try:
        text = base.MATCH_TXT.read_text(encoding="utf-8") if base.MATCH_TXT.exists() else ""
        if marker in text:
            return
        lines = text.splitlines()
        if lines and lines[0].startswith("🧬 Provider matching diagnostics"):
            lines.insert(1, marker)
            text = "\n".join(lines) + "\n"
        else:
            text = marker + "\n" + text
        base.MATCH_TXT.write_text(text, encoding="utf-8")
    except Exception:
        pass


def _mark_json(payload: dict[str, Any]) -> None:
    payload["matching_adapter_version"] = ADAPTER_VERSION
    try:
        base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    payload = await v3.run(timeout_seconds=timeout_seconds)
    _mark_json(payload)
    _mark_text()
    return payload
