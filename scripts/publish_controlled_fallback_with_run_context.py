from __future__ import annotations

"""Run controlled fallback publisher with GitHub Actions metadata in Telegram.

The original publisher remains untouched. This wrapper patches only small safety
points around the original controlled fallback module:
- append GitHub Actions run metadata to Telegram messages;
- enforce honest A/B tier rules based on independent odds providers;
- allow B-tier no-next-cron lifecycle relief without promoting it to A-tier.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

TARGET = Path(__file__).with_name("publish_controlled_fallback.py")
STATUS_PATH = Path(".data/exports/latest-controlled-fallback-run-context-wrapper.json")


LIVE_ODDS_SOURCES = {
    "odds_api_io",
    "bzzoiro",
    "sportlogic",
    "allsportsapi",
    "api_football",
    "rapidapi_odds",
    "oddspapi",
    "odds_papi",
    "highlightly",
}

SOURCE_ALIASES = {
    "oddsapiio": "odds_api_io",
    "odds_api": "odds_api_io",
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "odds_api_io_key_1": "odds_api_io",
    "odds_api_io_key_2": "odds_api_io",
    "bzzoiro_predictions": "bzzoiro",
    "bzzoiro_current_odds": "bzzoiro",
    "bzzoiro_v2": "bzzoiro",
    "sport_logic": "sportlogic",
    "sportlogic_io": "sportlogic",
    "all_sports_api": "allsportsapi",
    "api_football_rapidapi": "api_football",
    "rapidapi": "rapidapi_odds",
    "oddsapi": "odds_api_io",
}


def _load_target() -> Any:
    spec = importlib.util.spec_from_file_location("publish_controlled_fallback_original", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TARGET}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm_source(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return SOURCE_ALIASES.get(text, text)


def _iter_source_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,|;/]+", value) if part.strip()]
    return []


def _add_live_source(out: set[str], value: Any) -> None:
    src = _norm_source(value)
    if src in LIVE_ODDS_SOURCES:
        out.add(src)


def _nested_dict(container: dict[str, Any], *path: str) -> dict[str, Any]:
    cur: Any = container
    for key in path:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(key)
    return cur if isinstance(cur, dict) else {}


def _independent_odds_sources(candidate: dict[str, Any]) -> tuple[list[str], int, dict[str, Any]]:
    """Return independent live odds providers, not bookmakers or API accounts.

    odds-api.io account1/account2, Bet365 and Betfair Exchange depth are useful
    price confirmations, but they are not separate independent odds providers.
    This prevents controlled fallback from promoting B-tier candidates to A-tier.
    """
    sources: set[str] = set()

    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    diagnostics = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    publish_contract = _nested_dict(diagnostics, "publish_coverage_contract")
    consensus = _nested_dict(diagnostics, "api_coverage_consensus")

    containers = [candidate, source_summary, publish_contract, consensus]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "odds_sources",
            "independent_odds_sources",
            "live_odds_sources",
            "exact_odds_sources",
            "exact_price_sources",
            "sources",
        ):
            for item in _iter_source_values(container.get(key)):
                _add_live_source(sources, item)

    for offer in candidate.get("raw_bucket_offers") or []:
        if isinstance(offer, dict):
            _add_live_source(sources, offer.get("source"))

    # If names are absent but an explicitly independent count exists, keep it as a
    # fallback.  Prefer named/normalized sources whenever available because counts
    # can be polluted by bookmaker depth or odds-api.io account split.
    explicit_counts: list[int] = []
    for container in (source_summary, publish_contract):
        for key in ("independent_odds_sources_count", "odds_sources_count", "named_odds_sources_count"):
            try:
                val = container.get(key) if isinstance(container, dict) else None
                if val not in (None, ""):
                    explicit_counts.append(int(float(str(val))))
            except Exception:
                continue

    normalized = sorted(sources)
    if normalized:
        count = len(normalized)
    elif explicit_counts:
        count = max(0, min(explicit_counts))
    else:
        count = 0
    return normalized, count, {
        "normalized_sources": normalized,
        "explicit_counts": explicit_counts,
        "source_summary_odds_sources": source_summary.get("odds_sources"),
        "publish_contract_odds_sources": publish_contract.get("odds_sources"),
    }


publisher = _load_target()
_original_send_telegram = publisher.send_telegram
_original_candidate_metrics = publisher.candidate_metrics


# HARIZON patch: enforce honest A/B tier rules without changing the original
# publisher file.  A-tier must have 2+ independent odds sources and 2+
# confirmations.  B-tier is allowed with 1 odds source + 1 confirmation when the
# line movement guard has already approved the candidate or there is no next cron
# before kickoff.
def _tier_code(value: object) -> str:
    raw = str(value or "").strip().lower().replace("уровень", "").strip()
    if raw in {"a", "а", "tier_a", "a-tier"}:
        return "A"
    if raw in {"b", "б", "tier_b", "b-tier"}:
        return "B"
    if raw in {"c", "с", "tier_c", "c-tier"}:
        return "C"
    if "a" == raw[-1:] or "а" == raw[-1:]:
        return "A"
    if "b" == raw[-1:] or "б" == raw[-1:]:
        return "B"
    if "c" == raw[-1:] or "с" == raw[-1:]:
        return "C"
    return ""


def _env_int(name: str, default: int) -> int:
    try:
        import os
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    try:
        import os
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}
    except Exception:
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def candidate_metrics_with_independent_odds(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(_original_candidate_metrics(candidate) or {})
    sources, count, detail = _independent_odds_sources(candidate)
    if count > 0 or sources:
        metrics["odds_sources_count"] = count
        metrics["independent_odds_sources_count"] = count
        metrics["odds_sources"] = sources
        metrics["independent_odds_source_detail"] = detail
    return metrics


_original_tier_reasons = publisher.tier_reasons
_original_final_publish_guard_reasons = publisher.final_publish_guard_reasons


def tier_reasons_with_honest_ab_rules(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons = list(_original_tier_reasons(tier, candidate, metrics) or [])
    code = _tier_code(tier)
    odds_sources = _as_int(metrics.get("independent_odds_sources_count"), _as_int(metrics.get("odds_sources_count"), _as_int(metrics.get("sources_count"))))
    confirmations = _as_int(metrics.get("confirmation_sources_count"), _as_int(metrics.get("sources_count")))
    if code == "A":
        min_odds = _env_int("CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES", 2)
        min_conf = _env_int("CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES", 2)
        if odds_sources < min_odds:
            reasons.append(f"tier_a_odds_sources_below_min:{odds_sources}/{min_odds}")
        if confirmations < min_conf:
            reasons.append(f"tier_a_confirmation_sources_below_min:{confirmations}/{min_conf}")
    elif code == "B":
        # B-tier by project rules is allowed with one independent odds provider.
        # Some global workflow envs still set CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES=2
        # because A-tier/main publication uses 2+ odds-source.  Do not let that
        # global value leak into B-tier fallback evaluation.
        min_odds = 1
        min_conf = _env_int("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES", 1)
        if odds_sources >= min_odds:
            reasons = [r for r in reasons if not str(r).startswith("tier_b_odds_sources_below_min:")]
        else:
            reasons.append(f"tier_b_odds_sources_below_min:{odds_sources}/{min_odds}")
        if confirmations < min_conf:
            reasons.append(f"tier_b_confirmation_sources_below_min:{confirmations}/{min_conf}")
    return reasons


def final_publish_guard_reasons_with_b_tier_lifecycle(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> list[str]:
    reasons = list(_original_final_publish_guard_reasons(candidate, metrics, tier) or [])
    code = _tier_code(tier)
    if code != "B":
        return reasons

    confirmations = _as_int(metrics.get("confirmation_sources_count"), _as_int(metrics.get("sources_count")))
    min_conf = _env_int("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES", 1)
    if confirmations >= min_conf:
        prefix = "controlled_fallback_confirmation_sources_below_min:"
        reasons = [r for r in reasons if not str(r).startswith(prefix)]

    movement = metrics.get("line_movement") if isinstance(metrics.get("line_movement"), dict) else {}
    status = str(movement.get("status") or "")
    if (
        _env_bool("CONTROLLED_FALLBACK_TIER_B_ALLOW_NO_NEXT_CRON", True)
        and bool(movement.get("passed"))
        and status == "publish_now_no_next_cron"
    ):
        reasons = [r for r in reasons if str(r) != "line_movement_not_confirmed:publish_now_no_next_cron"]
    return reasons


publisher.candidate_metrics = candidate_metrics_with_independent_odds
publisher.tier_reasons = tier_reasons_with_honest_ab_rules
publisher.final_publish_guard_reasons = final_publish_guard_reasons_with_b_tier_lifecycle


def send_telegram_with_run_context(message: Any):
    write_github_run_context()
    return _original_send_telegram(append_github_run_reference(message))


publisher.send_telegram = send_telegram_with_run_context
_write_status({
    "status": "installed",
    "wrapper": "controlled-fallback-github-run-reference",
    "github_actions": github_run_context(),
    "independent_odds_source_correction": True,
})


if __name__ == "__main__":
    raise SystemExit(publisher.main())
