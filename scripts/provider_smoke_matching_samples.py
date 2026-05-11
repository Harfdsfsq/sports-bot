from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _short(value: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text[:limit]


def _event_label(event: Any) -> str:
    if not isinstance(event, dict):
        return "n/a"
    home = event.get("home") or "?"
    away = event.get("away") or "?"
    league = event.get("league") or ""
    start = event.get("start") or ""
    source_id = event.get("source_id") or ""
    return f"{home} — {away} | {league} | {start} | id={source_id}"


def _diagnostic_fields(provider: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    keys = [
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
        "unified_inventory_error",
    ]
    for key in keys:
        value = provider.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"  {key}: {_short(value)}")
    return lines


def _block(provider: dict[str, Any]) -> list[str]:
    name = provider.get("provider") or "unknown"
    stage = provider.get("failure_stage") or "unknown"
    lines = [f"• {name}: {stage}"]
    lines.extend(_diagnostic_fields(provider))

    matched = provider.get("matched_samples") if isinstance(provider.get("matched_samples"), list) else []
    if matched:
        lines.append("  matched samples:")
        for item in matched[:4]:
            if isinstance(item, dict) and "provider_event" in item:
                lines.append(f"    + provider: {_event_label(item.get('provider_event'))}")
                lines.append(f"      odds:     {_event_label(item.get('odds_event'))} | score={item.get('score')} | quality={item.get('quality')}")
            elif isinstance(item, dict) and "match" in item:
                lines.append(f"    + team-form: {_event_label(item.get('match'))}")

    unmatched = provider.get("unmatched_samples") if isinstance(provider.get("unmatched_samples"), list) else []
    if unmatched:
        lines.append("  unmatched samples:")
        for item in unmatched[:6]:
            if not isinstance(item, dict):
                continue
            event = item.get("provider_event") or item.get("match")
            lines.append(f"    - provider: {_event_label(event)}")
            if isinstance(item.get("best_odds_event"), dict):
                lines.append(f"      best odds: {_event_label(item.get('best_odds_event'))} | score={item.get('best_score')} | quality={item.get('best_quality')}")
            if isinstance(item.get("provider_norm"), dict):
                lines.append(f"      provider_norm={item.get('provider_norm')}")
            if isinstance(item.get("odds_norm"), dict):
                lines.append(f"      odds_norm={item.get('odds_norm')}")
            if item.get("home_norm") or item.get("away_norm"):
                lines.append(f"      missing_norm=home:{item.get('home_norm')} away:{item.get('away_norm')}")

    attempts = provider.get("attempts") if isinstance(provider.get("attempts"), list) else []
    if stage in {"request_or_empty_query", "parser_extract_failed", "stale_provider_rows_date_filter_ignored"} and attempts:
        lines.append("  attempts:")
        for attempt in attempts[:8]:
            if isinstance(attempt, dict):
                lines.append(f"    * http={attempt.get('http_status')} shape={attempt.get('payload_shape')} keys={attempt.get('params_keys')} url={attempt.get('url')}")
    note = provider.get("diagnostic_note")
    if note:
        lines.append(f"  diagnostic_note: {note}")
    return lines


def render(payload: dict[str, Any]) -> str:
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    lines = ["", "🔎 Matching samples / exact repair material"]
    for provider in providers:
        if isinstance(provider, dict):
            lines.extend(_block(provider))
    return "\n".join(lines).rstrip() + "\n"


def append_samples(json_path: str | Path = ".data/exports/latest-provider-smoke-matching-diagnostics.json", txt_path: str | Path = ".data/exports/latest-provider-smoke-matching-diagnostics.txt") -> bool:
    json_file = Path(json_path)
    txt_file = Path(txt_path)
    payload = _load(json_file)
    if not payload:
        return False
    text = txt_file.read_text(encoding="utf-8") if txt_file.exists() else ""
    if "🔎 Matching samples / exact repair material" in text:
        return True
    txt_file.write_text(text.rstrip() + "\n" + render(payload), encoding="utf-8")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if append_samples() else 1)
