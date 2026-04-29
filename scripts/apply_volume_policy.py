from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
POLICY_PATH = ROOT / "config" / "volume_policy.json"
STATE_PATH = ROOT / ".data" / "volume-governor-state.json"
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-volume-governor.json"

MSK = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
UTC = timezone.utc
POLICY_VERSION = "v13-target5-quality-governor-no-hard-stop"


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def local_date(value: Any) -> str | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return dt.astimezone(MSK).date().isoformat()


def today_local() -> str:
    return datetime.now(UTC).astimezone(MSK).date().isoformat()


def row_timestamp(row: dict[str, Any]) -> Any:
    for key in ("sent_at", "published_at", "created_at", "placed_at", "timestamp", "updated_at"):
        if row.get(key):
            return row.get(key)
    return None


def normalize_piece(value: Any) -> str:
    return str(value or "").strip().lower()


def row_dedupe_key(row: dict[str, Any], fallback: str) -> str:
    parts = [
        normalize_piece(row.get("match_key")),
        normalize_piece(row.get("family")),
        normalize_piece(row.get("selection")),
        normalize_piece(row.get("selection_key")),
        normalize_piece(row.get("point")),
        normalize_piece(row.get("team_side")),
        normalize_piece(row.get("commence_time") or row.get("start_time") or row.get("kickoff")),
    ]
    key = "|".join(parts).strip("|")
    if key:
        return key

    home = normalize_piece(row.get("home_team") or row.get("home"))
    away = normalize_piece(row.get("away_team") or row.get("away"))
    selection = normalize_piece(row.get("selection"))
    odds = normalize_piece(row.get("odds"))
    kickoff = normalize_piece(row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
    key = "|".join([home, away, selection, odds, kickoff]).strip("|")
    return key or fallback


def count_collection_rows(rows: Any, today: str) -> tuple[int, set[str]]:
    if not isinstance(rows, list):
        return 0, set()
    keys: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if local_date(row_timestamp(row)) != today:
            continue
        keys.add(row_dedupe_key(row, fallback=f"row:{idx}"))
    return len(keys), keys


def configured_count_sources(policy: dict[str, Any]) -> dict[str, bool]:
    raw = policy.get("count_sources")
    if not isinstance(raw, dict):
        raw = {}

    def flag(name: str, default: bool) -> bool:
        value = raw.get(name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    return {
        "fallback_sent_index": flag("fallback_sent_index", True),
        "state_bets": flag("state_bets", True),
        "state_published_candidates": flag("state_published_candidates", True),
        "state_shadow_bets": flag("state_shadow_bets", False),
    }


def count_existing_picks_today(today: str, policy: dict[str, Any]) -> dict[str, Any]:
    sources = configured_count_sources(policy)
    counts: dict[str, Any] = {"count_sources": sources}
    effective_keys: set[str] = set()

    sent_index = load_json(ROOT / ".data" / "fallback-sent-index.json", {})
    if isinstance(sent_index, dict):
        rows = [v for v in sent_index.values() if isinstance(v, dict)]
        n, keys = count_collection_rows(rows, today)
        counts["fallback_sent_index"] = n
        if sources["fallback_sent_index"]:
            effective_keys.update(keys)
    else:
        counts["fallback_sent_index"] = 0

    state = load_json(ROOT / ".data" / "state.json", {})
    if isinstance(state, dict):
        collection_to_source = {
            "bets": "state_bets",
            "published_candidates": "state_published_candidates",
            "shadow_bets": "state_shadow_bets",
        }
        for collection, source_name in collection_to_source.items():
            n, keys = count_collection_rows(state.get(collection) or [], today)
            counts[source_name] = n
            if sources[source_name]:
                effective_keys.update(keys)
    else:
        counts["state_bets"] = 0
        counts["state_published_candidates"] = 0
        counts["state_shadow_bets"] = 0

    counts["effective_today_picks"] = len(effective_keys)
    counts["effective_count_note"] = (
        "Real pick count is deduped across fallback-sent-index/state.bets/state.published_candidates. "
        "Shadow bets are excluded because they are diagnostic/watchlist rows."
    )
    return counts


def choose_mode(policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_mode = os.getenv("VOLUME_POLICY_MODE") or str(policy.get("mode") or "target_5")
    modes = policy.get("modes") if isinstance(policy.get("modes"), dict) else {}
    mode_cfg = modes.get(raw_mode)
    if not isinstance(mode_cfg, dict):
        raw_mode = "target_5"
        mode_cfg = modes.get(raw_mode) if isinstance(modes.get(raw_mode), dict) else {}
    return raw_mode, dict(mode_cfg)


def _float(cfg: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except Exception:
        return default


def _int(cfg: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(float(cfg.get(key, default)))
    except Exception:
        return default


def volume_stage(existing_today: int, target: int) -> str:
    if existing_today < max(1, target - 1):
        return "build_to_target"
    if existing_today < target:
        return "last_target_pick"
    return "after_target_extra_strict"


def stage_thresholds(cfg: dict[str, Any], stage: str) -> dict[str, float]:
    strict = cfg.get("after_target_extra_strict") if isinstance(cfg.get("after_target_extra_strict"), dict) else {}
    if stage == "after_target_extra_strict":
        return {
            "tier_b_min_confidence": _float(strict, "min_confidence", 68.0),
            "tier_b_min_quality": _float(strict, "min_quality", 64.0),
            "tier_b_min_edge_pp": _float(strict, "min_edge_pp", 4.0),
            "tier_b_min_ev_pct": _float(strict, "min_ev_pct", 8.5),
            "tier_b_max_odds": _float(strict, "max_odds", 2.55),
            "final_min_edge_pp": _float(strict, "final_min_edge_pp", 3.8),
            "final_min_ev_pct": _float(strict, "final_min_ev_pct", 8.0),
            "extra_pick_min_confidence": _float(strict, "min_confidence", 68.0),
            "extra_pick_min_edge_pp": _float(strict, "min_edge_pp", 4.0),
            "extra_pick_min_ev_pct": _float(strict, "min_ev_pct", 8.5),
        }
    return {
        "tier_b_min_confidence": _float(cfg, "tier_b_min_confidence", 63.0),
        "tier_b_min_quality": _float(cfg, "tier_b_min_quality", 60.0),
        "tier_b_min_edge_pp": _float(cfg, "tier_b_min_edge_pp", 3.0),
        "tier_b_min_ev_pct": _float(cfg, "tier_b_min_ev_pct", 6.0),
        "tier_b_max_odds": _float(cfg, "tier_b_max_odds", 2.75),
        "final_min_edge_pp": _float(cfg, "final_min_edge_pp", 3.0),
        "final_min_ev_pct": _float(cfg, "final_min_ev_pct", 6.0),
        "extra_pick_min_confidence": _float(cfg, "extra_pick_min_confidence", 64.0),
        "extra_pick_min_edge_pp": _float(cfg, "extra_pick_min_edge_pp", 3.0),
        "extra_pick_min_ev_pct": _float(cfg, "extra_pick_min_ev_pct", 6.0),
    }


def flatten_env(mode: str, cfg: dict[str, Any], existing_today: int) -> tuple[dict[str, str], list[str], dict[str, Any]]:
    target = _int(cfg, "daily_target_picks", 5)
    max_per_run = max(1, _int(cfg, "max_picks_per_run", 2))
    extra_max_per_run = max(1, _int(cfg, "after_target_max_picks_per_run", 1))
    stage = volume_stage(existing_today, target)
    thresholds = stage_thresholds(cfg, stage)

    if stage == "build_to_target":
        allowed_this_run = min(max_per_run, max(1, target - existing_today))
        reasons = [f"target5_build_to_target:{existing_today}/{target}"]
    elif stage == "last_target_pick":
        allowed_this_run = 1
        reasons = [f"target5_last_pick_before_target:{existing_today}/{target}"]
    else:
        allowed_this_run = extra_max_per_run
        reasons = [f"target5_after_target_quality_only:{existing_today}/{target}"]

    env: dict[str, str] = {
        "VOLUME_POLICY_VERSION": POLICY_VERSION,
        "VOLUME_POLICY_MODE": mode,
        "VOLUME_POLICY_STAGE": stage,
        "VOLUME_DAILY_TARGET_PICKS": str(target),
        # Kept only as telemetry. They no longer disable analysis/publication.
        "VOLUME_DAILY_SOFT_CAP_PICKS": str(_int(cfg, "daily_soft_cap_picks", target)),
        "VOLUME_DAILY_HARD_CAP_PICKS": str(_int(cfg, "daily_hard_cap_picks", target)),
        "VOLUME_EXISTING_PICKS_TODAY": str(existing_today),
        "CONTROLLED_FALLBACK_ENABLED": "true",
        "CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN": str(allowed_this_run),
        "CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN": str(allowed_this_run),
        "MAX_PICKS_PER_RUN": str(allowed_this_run),
        "CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH": str(_int(cfg, "max_picks_per_match", 1)),
        "CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT": str(_float(cfg, "daily_stake_cap_pct", 3.0)),
        "CONTROLLED_FALLBACK_SKIP_IF_STAKE_BELOW_MIN": "true",
        "CONTROLLED_FALLBACK_EXTRA_PICK_STRICT": "true",
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT": str(thresholds["extra_pick_min_ev_pct"]),
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP": str(thresholds["extra_pick_min_edge_pp"]),
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE": str(thresholds["extra_pick_min_confidence"]),
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK": "true",
        "CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY": "true",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "true",
        "CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED": "false",
        "CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES": "",
        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": str(thresholds["final_min_edge_pp"]),
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": str(thresholds["final_min_ev_pct"]),
    }

    families = cfg.get("allowed_families") or ["totals", "dnb", "teamtotals", "teamTotals", "btts"]
    env["CONTROLLED_FALLBACK_ALLOWED_FAMILIES"] = ",".join(str(x) for x in families)

    tiers = cfg.get("tiers") if isinstance(cfg.get("tiers"), dict) else {}
    for tier_name in ("A", "B", "C"):
        tier = tiers.get(tier_name) if isinstance(tiers.get(tier_name), dict) else {}
        prefix = f"CONTROLLED_FALLBACK_TIER_{tier_name}_"
        allowed = tier.get("allowed_families") or families
        env[prefix + "ALLOWED_FAMILIES"] = "" if tier_name == "C" else ",".join(str(x) for x in allowed)
        mapping = {
            "min_books": "MIN_BOOKS",
            "min_confidence": "MIN_CONFIDENCE",
            "min_quality": "MIN_QUALITY",
            "min_edge_pp": "MIN_EDGE_PP",
            "min_ev_pct": "MIN_EV_PCT",
            "min_publication_score": "MIN_PUBLICATION_SCORE",
            "max_odds": "MAX_ODDS",
        }
        for src, dst in mapping.items():
            if src in tier:
                env[prefix + dst] = str(tier[src])

    # After the target is reached, Tier B becomes an elite-only rescue tier.
    if stage == "after_target_extra_strict":
        env.update({
            "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE": str(thresholds["tier_b_min_confidence"]),
            "CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY": str(thresholds["tier_b_min_quality"]),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": str(thresholds["tier_b_min_edge_pp"]),
            "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": str(thresholds["tier_b_min_ev_pct"]),
            "CONTROLLED_FALLBACK_TIER_B_MAX_ODDS": str(thresholds["tier_b_max_odds"]),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": str(thresholds["tier_b_min_confidence"]),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": str(thresholds["tier_b_min_edge_pp"]),
            "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": str(thresholds["tier_b_min_ev_pct"]),
        })

    final_guards = cfg.get("final_guards") if isinstance(cfg.get("final_guards"), dict) else {}
    for key, value in final_guards.items():
        # Stage-specific final EV/edge values must not be overwritten by config defaults.
        if str(key) in {"CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP", "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT"}:
            continue
        env[str(key)] = str(value).lower() if isinstance(value, bool) else str(value)

    stakes = cfg.get("tier_stake") if isinstance(cfg.get("tier_stake"), dict) else {}
    if "default_pct" in stakes:
        env["CONTROLLED_FALLBACK_STAKE_PCT"] = str(stakes["default_pct"])
    if "min_stake" in stakes:
        env["CONTROLLED_FALLBACK_MIN_STAKE"] = str(stakes["min_stake"])
    for tier_name in ("A", "B", "C"):
        key = f"max_stake_tier_{tier_name.lower()}"
        if key in stakes:
            env[f"CONTROLLED_FALLBACK_MAX_STAKE_TIER_{tier_name}"] = str(stakes[key])

    details = {"target": target, "stage": stage, "allowed_this_run": allowed_this_run, "thresholds": thresholds}
    return env, reasons, details


def append_github_env(env: dict[str, str]) -> None:
    target = os.getenv("GITHUB_ENV")
    lines = [f"{key}={value}" for key, value in sorted(env.items())]
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    else:
        print("\n".join(lines))


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    mode, cfg = choose_mode(policy)
    today = today_local()
    counts = count_existing_picks_today(today, policy)
    existing_today = int(counts.get("effective_today_picks") or 0)
    env, reasons, details = flatten_env(mode, cfg, existing_today)

    now_utc = datetime.now(UTC).isoformat()
    payload = {
        "version": POLICY_VERSION,
        "mode": mode,
        "today_local": today,
        "now_utc": now_utc,
        "existing_picks_today": existing_today,
        "counts": counts,
        "decision_reasons": reasons,
        "applied_env": env,
        "target_governor": details,
        "quality_policy": {
            "hard_daily_stop_removed": True,
            "publication_strategy": "Aim for about 5 picks/day. Before target, allow 1-2 picks/run. After target, keep analysis and fallback enabled but allow only 1 extra pick/run under stricter EV/edge/confidence/quality guards.",
            "hard_guards_preserved": [
                "canonical_negative_value",
                "xg_direction_conflict",
                "xg_probability_gap_hard_reject",
                "dnb_outlier_guard",
                "proxy_single_book_reject",
                "two-book Telegram guard",
            ],
            "tier_c_publication": "disabled",
        },
        "fix_note": (
            "v13 removes the hard daily stop that previously set MAX_PICKS_PER_RUN=0 and disabled fallback after cap. "
            "Daily average is now controlled by target-aware quality thresholds instead of blocking analysis."
        ),
    }
    write_json(STATE_PATH, payload)
    write_json(EXPORT_PATH, payload)
    append_github_env(env)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
