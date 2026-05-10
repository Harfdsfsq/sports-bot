from __future__ import annotations

"""Provider-smoke orchestrator.

Runs three diagnostics in one workflow log/artifact:
1. low-level provider smoke;
2. direct full-data endpoint probe from API documentation;
3. cross-provider matching diagnostics against odds inventory.
"""

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
FULL_DATA_JSON = OUT_DIR / "latest-api-full-data-enrichment.json"
FULL_DATA_TXT = OUT_DIR / "latest-api-full-data-enrichment.txt"
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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_text_once(path: Path, text: str) -> None:
    if not text.strip():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    first_line = text.strip().splitlines()[0] if text.strip().splitlines() else ""
    if first_line and first_line in current:
        return
    path.write_text(current.rstrip() + "\n\n---\n\n" + text.strip() + "\n", encoding="utf-8")


def _stage_counts(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    stages = [str(row.get("failure_stage") or "unknown") for row in providers]
    out: dict[str, Any] = {"providers_total": len(providers)}
    for stage in sorted(set(stages)):
        out[stage] = stages.count(stage)
    return out


def _install_runtime_patches() -> None:
    try:
        from app.services import provider_matching_alias_runtime_patch
        provider_matching_alias_runtime_patch.install()
    except Exception:
        pass
    try:
        from app.services import api_full_data_runtime_patch
        api_full_data_runtime_patch.install()
    except Exception:
        pass


def _prepare_args() -> None:
    try:
        current_timeout = float(_arg_value("--timeout") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or "0")
    except Exception:
        current_timeout = 0.0
    if current_timeout < 18.0:
        _set_or_replace_arg("--timeout", "18")
    if _arg_value("--repeats") is None and not os.getenv("PROVIDER_SMOKE_REPEATS"):
        _set_or_replace_arg("--repeats", "2")


async def _run_full_data_probe() -> dict[str, Any]:
    try:
        try:
            from scripts import api_full_data_smoke_probe_v4 as probe_module
        except Exception:
            try:
                from scripts import api_full_data_smoke_probe_v3 as probe_module
            except Exception:
                try:
                    from scripts import api_full_data_smoke_probe_v2 as probe_module
                except Exception:
                    from scripts import api_full_data_smoke_probe as probe_module
        return await probe_module.run()
    except Exception as exc:
        payload = {
            "mode": "direct_full_data_smoke_probe",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(FULL_DATA_JSON, payload)
        FULL_DATA_TXT.write_text(
            "🧩 API full-data enrichment diagnostics\n"
            f"• status: failed\n• error: {payload['error']}\n",
            encoding="utf-8",
        )
        return payload


async def _run_matching_diagnostics() -> dict[str, Any]:
    try:
        try:
            from app.services import provider_smoke_matching_diagnostics_v2 as matching_module
        except Exception:
            from app.services import provider_smoke_matching_diagnostics as matching_module
        return await matching_module.run()
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


def _merge_diagnostics(full_payload: dict[str, Any], matching_payload: dict[str, Any]) -> None:
    diag = _load_json(DIAG_JSON)
    if diag:
        if full_payload:
            diag["api_full_data_enrichment"] = full_payload
        if matching_payload:
            diag["matching_diagnostics"] = matching_payload
            diag["matching_summary"] = _stage_counts(matching_payload)
        _write_json(DIAG_JSON, diag)
    if FULL_DATA_TXT.exists():
        _append_text_once(DIAG_TXT, FULL_DATA_TXT.read_text(encoding="utf-8"))
    if MATCH_TXT.exists():
        _append_text_once(DIAG_TXT, MATCH_TXT.read_text(encoding="utf-8"))


def _print_optional(title: str, path: Path) -> None:
    if path.exists():
        print(f"\n----- {title} -----")
        print(path.read_text(encoding="utf-8"))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _prepare_args()
    _install_runtime_patches()

    from scripts.provider_smoke_diagnostics_v4 import main as diagnostics_main

    status = diagnostics_main()

    full_payload: dict[str, Any] = {}
    if _truthy("API_FULL_SMOKE_ENABLED", True):
        full_payload = asyncio.run(_run_full_data_probe())
        _print_optional("api full-data enrichment diagnostics txt", FULL_DATA_TXT)

    matching_payload: dict[str, Any] = {}
    if _truthy("PROVIDER_SMOKE_MATCHING_DIAGNOSTICS_ENABLED", True):
        matching_payload = asyncio.run(_run_matching_diagnostics())
        _print_optional("provider smoke matching diagnostics txt", MATCH_TXT)

    _merge_diagnostics(full_payload, matching_payload)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
