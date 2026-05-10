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
FULL_DATA_JSON = OUT_DIR / "latest-api-full-data-enrichment.json"
FULL_DATA_TXT = OUT_DIR / "latest-api-full-data-enrichment.txt"


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

# Install runtime layers before provider smoke imports/instantiates providers.
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
    first_line = text.strip().splitlines()[0] if text.strip().splitlines() else ""
    if first_line and first_line in current:
        return
    path.write_text(current.rstrip() + "\n\n---\n\n" + text.strip() + "\n", encoding="utf-8")


def _summarize_matching(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    stages = [str(row.get("failure_stage") or "unknown") for row in providers]
    out: dict[str, Any] = {"providers_total": len(providers)}
    for stage in sorted(set(stages)):
        out[stage] = stages.count(stage)
    return out


def _event_label(event: dict[str, Any] | None) -> str:
    if not isinstance(event, dict):
        return "n/a"
    teams = f"{event.get('home')} — {event.get('away')}"
    league = event.get("league") or ""
    start = event.get("start") or ""
    sid = event.get("source_id") or ""
    return f"{teams} | {league} | {start} | id={sid}".strip()


def _render_sample_block(provider: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    stage = str(provider.get("failure_stage") or "")
    name = str(provider.get("provider") or "")
    if stage in {"matching_ok", "team_form_coverage_ok"}:
        samples = provider.get("matched_samples") if isinstance(provider.get("matched_samples"), list) else []
        if samples:
            lines.append(f"  matched samples for {name}:")
            for sample in samples[:3]:
                if "provider_event" in sample:
                    lines.append(f"    + provider: {_event_label(sample.get('provider_event'))}")
                    lines.append(f"      odds:     {_event_label(sample.get('odds_event'))} | score={sample.get('score')} | quality={sample.get('quality')}")
                elif "match" in sample:
                    lines.append(f"    + team-form: {_event_label(sample.get('match'))}")
        return lines

    unmatched = provider.get("unmatched_samples") if isinstance(provider.get("unmatched_samples"), list) else []
    if unmatched:
        lines.append(f"  unmatched samples for {name}:")
        for sample in unmatched[:4]:
            provider_event = sample.get("provider_event") or sample.get("match")
            lines.append(f"    - provider: {_event_label(provider_event)}")
            if isinstance(sample.get("best_odds_event"), dict):
                lines.append(f"      best odds: {_event_label(sample.get('best_odds_event'))} | score={sample.get('best_score')} | quality={sample.get('best_quality')}")
            if isinstance(sample.get("provider_norm"), dict):
                lines.append(f"      provider_norm={sample.get('provider_norm')}")
            if isinstance(sample.get("odds_norm"), dict):
                lines.append(f"      odds_norm={sample.get('odds_norm')}")
            if sample.get("home_norm") or sample.get("away_norm"):
                lines.append(f"      missing_norm=home:{sample.get('home_norm')} away:{sample.get('away_norm')}")
    else:
        samples = provider.get("samples") if isinstance(provider.get("samples"), list) else []
        if samples:
            lines.append(f"  raw samples for {name}:")
            for sample in samples[:4]:
                lines.append(f"    - {_event_label(sample)}")
    attempts = provider.get("attempts") if isinstance(provider.get("attempts"), list) else []
    if stage in {"request_or_empty_query", "no_fixture_overlap_with_odds_inventory", "parser_extract_failed", "stale_provider_rows_date_filter_ignored"} and attempts:
        lines.append(f"  attempts for {name}:")
        for attempt in attempts[:3]:
            lines.append(f"    * http={attempt.get('http_status')} shape={attempt.get('payload_shape')} keys={attempt.get('params_keys')} url={attempt.get('url')}")
    note = provider.get("diagnostic_note")
    if note:
        lines.append(f"  diagnostic_note: {note}")
    return lines


def _render_matching_text(payload: dict[str, Any]) -> str:
    inv = payload.get("odds_inventory") if isinstance(payload.get("odds_inventory"), dict) else {}
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    lines = [
        "🧬 Provider matching diagnostics",
        f"• UTC: {payload.get('created_at_utc')}",
        f"• odds inventory: rows {inv.get('raw_rows', 0)} | parsed {inv.get('parsed_events', 0)} | pages {inv.get('pages_requested', 0)} | status {inv.get('status')}",
        f"• duplicate canonical pairs in odds inventory: {len(payload.get('inventory_duplicate_pairs') or [])}",
        "",
        "| provider | role | status | raw | parsed | eligible | matched | rate | stage |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in providers:
        lines.append(
            f"| {row.get('provider')} | {row.get('provider_role')} | {row.get('status')} | {row.get('raw_rows')} | {row.get('parsed_events')} | "
            f"{row.get('eligible_events', '')} | {row.get('matched_to_odds_inventory')} | {row.get('match_rate_pct')}% | {row.get('failure_stage')} |"
        )
    lines += ["", "🔎 Samples / reasons"]
    for row in providers:
        block = _render_sample_block(row)
        if block:
            lines.append(f"• {row.get('provider')}: {row.get('failure_stage')}")
            lines.extend(block)
    lines += ["", "📎 Send this text plus latest-provider-smoke-diagnostics.json for exact parser/matching fixes."]
    return "\n".join(lines) + "\n"


def _render_full_data_text(payload: dict[str, Any]) -> str:
    lines = [
        "🧩 API full-data enrichment diagnostics",
        f"• updated_at_utc: {payload.get('updated_at_utc')}",
        "",
        "| api | requests | errors | cache files | key rows |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for api_name in ("bzzoiro", "football_data", "odds_api_io"):
        section = payload.get(api_name)
        if not isinstance(section, dict):
            continue
        requests = section.get("requests", 0)
        errors = section.get("errors", 0)
        cache_files = len(section.get("raw_cache_files") or [])
        if api_name == "bzzoiro":
            key_rows = f"live={section.get('live_rows_count', 0)}, events={len(section.get('event_ids') or [])}, teams={len(section.get('team_ids') or [])}, leagues={len(section.get('league_ids') or [])}"
        elif api_name == "football_data":
            key_rows = f"competitions={len(section.get('competition_refs') or [])}, teams_payloads={len(section.get('teams_by_competition') or {})}, scorers_payloads={len(section.get('scorers_by_competition') or {})}"
        else:
            key_rows = f"events={len(section.get('event_ids') or [])}, updated={section.get('updated_rows_count', 0)}, movements={len(section.get('movements_by_event') or {})}"
        lines.append(f"| {api_name} | {requests} | {errors} | {cache_files} | {key_rows} |")
    lines += ["", "🔎 Raw cache files are saved under .cache/api_raw/<api>/<date>/ and summarized in latest-api-full-data-enrichment.json."]
    return "\n".join(lines) + "\n"


async def _run_matching_diagnostics() -> dict[str, Any]:
    try:
        try:
            from app.services import provider_smoke_matching_diagnostics_v2 as matching_module
        except Exception:
            from app.services import provider_smoke_matching_diagnostics as matching_module
        payload = await matching_module.run()
        MATCH_TXT.write_text(_render_matching_text(payload), encoding="utf-8")
        return payload
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


def _merge_full_data_into_existing_reports() -> dict[str, Any]:
    payload = _load_json(FULL_DATA_JSON)
    if not payload:
        return {}
    FULL_DATA_TXT.write_text(_render_full_data_text(payload), encoding="utf-8")
    diag = _load_json(DIAG_JSON)
    if diag:
        diag["api_full_data_enrichment"] = payload
        _write_json(DIAG_JSON, diag)
    if FULL_DATA_TXT.exists():
        _append_text(DIAG_TXT, FULL_DATA_TXT.read_text(encoding="utf-8"))
    return payload


def main() -> int:
    status = diagnostics_main()
    full_payload = _merge_full_data_into_existing_reports()
    if full_payload and FULL_DATA_TXT.exists():
        print("\n----- api full-data enrichment diagnostics txt -----")
        print(FULL_DATA_TXT.read_text(encoding="utf-8"))
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
