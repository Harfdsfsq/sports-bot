from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / ".data" / "exports"
DIAG_JSON = OUT_DIR / "latest-provider-smoke-diagnostics.json"
DIAG_TXT = OUT_DIR / "latest-provider-smoke-diagnostics.txt"
MATCH_JSON = OUT_DIR / "latest-provider-smoke-matching-diagnostics.json"
MATCH_TXT = OUT_DIR / "latest-provider-smoke-matching-diagnostics.txt"


def _arg_value(name: str) -> str | None:
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return None
    return sys.argv[index + 1]


def _set_or_replace_arg(name: str, value: str) -> None:
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            sys.argv[index + 1] = value
        else:
            sys.argv.append(value)
    else:
        sys.argv.extend([name, value])


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


try:
    current_timeout = float(_arg_value("--timeout") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or "0")
except Exception:
    current_timeout = 0.0
if current_timeout < 18.0:
    _set_or_replace_arg("--timeout", "18")

if _arg_value("--repeats") is None and not os.getenv("PROVIDER_SMOKE_REPEATS"):
    _set_or_replace_arg("--repeats", "2")

from scripts.provider_smoke_diagnostics_v4 import main as diagnostics_main  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = "\n\n---\n\n🧬 Provider matching diagnostics"
    if "🧬 Provider matching diagnostics" in current:
        return
    path.write_text(current.rstrip() + marker + "\n" + text.strip() + "\n", encoding="utf-8")


def _summarize_matching(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    return {
        "providers_total": len(providers),
        "matching_ok": sum(1 for row in providers if row.get("failure_stage") == "matching_ok"),
        "partial_matching_low_yield": sum(1 for row in providers if row.get("failure_stage") == "partial_matching_low_yield"),
        "normalization_or_time_matching_failed": sum(1 for row in providers if row.get("failure_stage") == "normalization_or_time_matching_failed"),
        "parser_extract_failed": sum(1 for row in providers if row.get("failure_stage") == "parser_extract_failed"),
        "request_or_empty_query": sum(1 for row in providers if row.get("failure_stage") == "request_or_empty_query"),
    }


async def _run_matching_diagnostics() -> dict[str, Any]:
    try:
        from app.services import provider_smoke_matching_diagnostics
        return await provider_smoke_matching_diagnostics.run()
    except Exception as exc:
        payload = {
            "mode": "provider_smoke_matching_diagnostics",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(MATCH_JSON, payload)
        MATCH_TXT.write_text(
            "🧬 Provider matching diagnostics\n"
            f"• status: failed\n• error: {payload['error']}\n",
            encoding="utf-8",
        )
        return payload


def _merge_matching_into_existing_reports(matching_payload: dict[str, Any]) -> None:
    diag = _load_json(DIAG_JSON)
    if diag:
        diag["matching_diagnostics"] = matching_payload
        diag["matching_summary"] = _summarize_matching(matching_payload)
        _write_json(DIAG_JSON, diag)
    if MATCH_TXT.exists():
        _append_text(DIAG_TXT, MATCH_TXT.read_text(encoding="utf-8"))


def main() -> int:
    status = diagnostics_main()
    if not _truthy("PROVIDER_SMOKE_MATCHING_DIAGNOSTICS_ENABLED", True):
        return status
    matching_payload = asyncio.run(_run_matching_diagnostics())
    _merge_matching_into_existing_reports(matching_payload)
    if MATCH_TXT.exists():
        print("\n----- provider smoke matching diagnostics txt -----")
        print(MATCH_TXT.read_text(encoding="utf-8"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
