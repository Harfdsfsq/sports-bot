from __future__ import annotations

"""SStats deep smoke v2.

The v1 text report showed all 27 commands, but its JSON artifact passed the
whole payload through safe(), which truncates every list to 8 items. That made
provider_signal_coverage_blueprint_v2 see only 8 SStats commands.

v2 reuses the v1 probe and then rewrites the JSON/TXT artifacts with all command
rows preserved while still trimming samples/body previews inside each row.
"""

import asyncio
import json
from typing import Any

from scripts import sstats_deep_smoke_probe as base


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if isinstance(out.get("sample"), list):
        out["sample"] = base.safe(out["sample"][:5])
    else:
        out["sample"] = base.safe(out.get("sample"))
    out["body_preview"] = base.safe(str(out.get("body_preview") or "")[:1200])
    out["params"] = base.safe(out.get("params") or {})
    out["error"] = str(out.get("error") or "")[:600]
    return out


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    out["results"] = [_safe_row(row) for row in results if isinstance(row, dict)]
    out["sample_game_ids"] = list(payload.get("sample_game_ids") or [])
    out["summary"] = dict(payload.get("summary") or {})
    out["notes"] = list(payload.get("notes") or []) + [
        "v2 preserves the full results list; only per-command sample/body fields are trimmed."
    ]
    out["mode"] = "sstats_deep_smoke_probe_v2"
    return out


def _render(payload: dict[str, Any]) -> str:
    text = base.render(payload)
    return text.replace("# SStats deep smoke probe", "# SStats deep smoke probe v2")


async def run() -> dict[str, Any]:
    payload = await base.run()
    payload = _safe_payload(payload)
    base.JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.TXT_OUT.write_text(_render(payload), encoding="utf-8")
    print(_render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
