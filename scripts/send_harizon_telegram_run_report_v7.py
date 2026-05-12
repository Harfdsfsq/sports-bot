from __future__ import annotations

"""HARIZON Telegram run report v7.

Extends the standalone v5/v6 report without changing prediction logic:
- counts Bzzoiro v2 enrichment requests/resources when direct Bzzoiro provider is
  intentionally disabled by runtime guards;
- displays SportLogic fixture/odds diagnostic so `req 2, matched 0` is not
  misleading when the provider returns stale or unmatched fixtures.
"""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

V5_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v5.py")
EXPORT_DIR = Path(".data/exports")
UTC = timezone.utc


def _load_v5() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v5", V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v5 = _load_v5()
_base_build_payload = v5.build_payload
_base_render = v5.render


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _as_int(value: Any) -> int:
    return v5.as_int(value)


def _walk_dicts(value: Any, key: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        child = value.get(key)
        if isinstance(child, dict):
            found.append(child)
        for item in value.values():
            found.extend(_walk_dicts(item, key))
    elif isinstance(value, list):
        for item in value[:200]:
            found.extend(_walk_dicts(item, key))
    return found


def _max_bzzoiro_v2(data: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    debug = _first_dict(data.get("debug"))
    candidates.extend(_walk_dicts(debug, "bzzoiro_v2"))
    # Also inspect optional JSON artifacts if they exist.
    for path in (
        EXPORT_DIR / "latest-windowed-core-coverage.json",
        EXPORT_DIR / "latest-windowed-core-candidate-audit.json",
        EXPORT_DIR / "latest-bzzoiro-runner-bridge.json",
        EXPORT_DIR / "latest-signal-stack-runtime.json",
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            payload = {}
        candidates.extend(_walk_dicts(payload, "bzzoiro_v2"))
    if not candidates:
        return {}
    def score(row: dict[str, Any]) -> int:
        return _as_int(row.get("requests")) + _as_int(row.get("contexts_built")) * 4 + _as_int(row.get("odds_resources")) * 5
    return dict(max(candidates, key=score))


def _sportlogic_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    dbg = _first_dict(data.get("sportlogic_debug"))
    stats = _first_dict(dbg.get("stats"))
    preview = _first_dict(dbg.get("preview"))
    fixtures = preview.get("sample_fixtures") if isinstance(preview.get("sample_fixtures"), list) else []
    sample_dates: list[str] = []
    for row in fixtures[:5]:
        if isinstance(row, dict):
            start = str(row.get("start_time") or "")
            if len(start) >= 10:
                sample_dates.append(start[:10])
    stale_sample = bool(sample_dates and len(set(sample_dates)) == 1 and sample_dates[0] not in {datetime.now(UTC).date().isoformat()})
    return {
        "fixtures_fetched": _as_int(stats.get("fixtures_fetched"),),
        "games_fetched": _as_int(stats.get("games_fetched")),
        "sample_dates": sorted(set(sample_dates)),
        "stale_sample": stale_sample,
        "query_variants_used": stats.get("query_variants_used") if isinstance(stats.get("query_variants_used"), list) else [],
        "diagnosis": "games_endpoint_returned_unmatched_or_stale_rows" if _as_int(stats.get("fixtures_fetched")) and not _as_int(stats.get("events_matched")) else "ok_or_no_data",
    }


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    data = v5.artifacts()
    bzz_v2 = _max_bzzoiro_v2(data)
    sport_diag = _sportlogic_diagnostics(data)
    payload["version"] = "harizon-telegram-report-v7-bzzoiro-v2-sportlogic-diagnostics"
    payload.setdefault("diagnostics", {})
    payload["diagnostics"]["bzzoiro_v2"] = bzz_v2
    payload["diagnostics"]["sportlogic"] = sport_diag

    api = payload.setdefault("api", {})
    bzz = dict(api.get("bzzoiro") or {})
    if bzz_v2:
        bzz["v2_requests"] = _as_int(bzz_v2.get("requests"))
        bzz["v2_contexts"] = _as_int(bzz_v2.get("contexts_built"))
        bzz["v2_events"] = _as_int(bzz_v2.get("events_fetched"))
        bzz["v2_odds_resources"] = _as_int(bzz_v2.get("odds_resources"))
        bzz["v2_stats_resources"] = _as_int(bzz_v2.get("stats_resources"))
        bzz["v2_lineups_resources"] = _as_int(bzz_v2.get("lineups_resources"))
        bzz["v2_errors"] = _as_int(bzz_v2.get("errors"))
        bzz["requests_total_effective"] = max(_as_int(bzz.get("requests")), _as_int(bzz_v2.get("requests")))
        bzz["contexts_total_effective"] = max(_as_int(bzz.get("contexts")), _as_int(bzz_v2.get("contexts_built")))
        bzz["events_total_effective"] = max(_as_int(bzz.get("events")), _as_int(bzz_v2.get("events_fetched")))
    api["bzzoiro"] = bzz
    sport = dict(api.get("sportlogic") or {})
    sport.update({
        "fixtures_fetched": sport_diag.get("fixtures_fetched", 0),
        "games_fetched": sport_diag.get("games_fetched", 0),
        "stale_sample": bool(sport_diag.get("stale_sample")),
        "sample_dates": sport_diag.get("sample_dates") or [],
        "diagnosis": sport_diag.get("diagnosis"),
    })
    api["sportlogic"] = sport
    payload["api"] = api
    return payload


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload).replace("HARIZON run report v6", "HARIZON run report v7")
    api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
    bzz = api.get("bzzoiro") if isinstance(api.get("bzzoiro"), dict) else {}
    sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}
    old_bzz = (
        f"• bzzoiro: req {_as_int(bzz.get('requests'))}, ctx {_as_int(bzz.get('contexts'))}, events {_as_int(bzz.get('events'))}, "
        f"secondary offers {_as_int(bzz.get('secondary_offers_added'))}, overlap odds-api.io {_as_int(bzz.get('overlap'))}, err {_as_int(bzz.get('errors'))}"
    )
    new_bzz = (
        f"• bzzoiro: direct req {_as_int(bzz.get('requests'))}, v2 req {_as_int(bzz.get('v2_requests'))}, "
        f"v2 ctx {_as_int(bzz.get('v2_contexts'))}, v2 odds {_as_int(bzz.get('v2_odds_resources'))}, "
        f"secondary offers {_as_int(bzz.get('secondary_offers_added'))}, overlap odds-api.io {_as_int(bzz.get('overlap'))}, "
        f"err {max(_as_int(bzz.get('errors')), _as_int(bzz.get('v2_errors')))}"
    )
    if old_bzz in text:
        text = text.replace(old_bzz, new_bzz)
    old_sport = (
        f"• sportlogic: enabled {bool(sport.get('enabled'))}, req {_as_int(sport.get('requests'))}, odds req {_as_int(sport.get('odds_requests'))}, "
        f"matched {_as_int(sport.get('matched'))}, offers {_as_int(sport.get('offers'))}, err {_as_int(sport.get('errors'))}"
    )
    dates = ",".join(str(x) for x in (sport.get("sample_dates") or [])) or "n/a"
    new_sport = (
        f"• sportlogic: enabled {bool(sport.get('enabled'))}, req {_as_int(sport.get('requests'))}, fixtures {_as_int(sport.get('fixtures_fetched'))}, "
        f"matched {_as_int(sport.get('matched'))}, odds req {_as_int(sport.get('odds_requests'))}, offers {_as_int(sport.get('offers'))}, "
        f"sample_dates {dates}, diag {sport.get('diagnosis')}, err {_as_int(sport.get('errors'))}"
    )
    if old_sport in text:
        text = text.replace(old_sport, new_sport)
    return text


v5.build_payload = build_payload
v5.render = render


if __name__ == "__main__":
    raise SystemExit(v5.main())
