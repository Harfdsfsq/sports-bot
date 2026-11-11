"""Predeclared forward cohort; exploratory winners never authorize live bets."""

from __future__ import annotations

import ast
import hashlib
import json
import math
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from app.services.daily_match_registry import parse_time, read_json
from app.services.fixture_identity import same_team_identity
from app.services.totals_strategy_lab import MODELS, PROTOCOL, summarize
from app.utils import normalize_bookmaker_name

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ".data/exports/latest-prospective-validation.json"


def policy():
    return json.loads((ROOT / "config/prospective_validation.json").read_text())


def fingerprint(rules):
    pieces = [json.dumps(rules, sort_keys=True)]
    for name in (
        "goal_research",
        "totals_strategy_lab",
        "totals_strategy_clv",
        "daily_price_watch",
        "daily_goal_probability",
        "daily_context_reliability",
        "context_admission",
        "prospective_validation",
        "model",
        "daily_quality_runner",
    ):
        tree = ast.parse((ROOT / "app/services" / f"{name}.py").read_text())
        pieces.append(ast.dump(tree, include_attributes=False))
    # Identity and provider transformations change the forecast input too.
    for path in (
        "app/providers/sstats.py", "app/providers/espn.py",
        "app/services/fixture_identity.py", "app/services/espn_identity.py",
        "app/services/sstats_targeted_history.py", "app/services/team_result_archive.py",
        "app/services/daily_match_registry.py",
        "app/providers/odds_api_io.py", "app/services/odds_line_audit.py",
    ):
        pieces.append(ast.dump(ast.parse((ROOT / path).read_text()), include_attributes=False))
    pieces.append(json.dumps(json.loads((ROOT / "config/espn_team_aliases.json").read_text()), sort_keys=True))
    return hashlib.sha256("\n".join(pieces).encode()).hexdigest()


def evaluate(history, now, *, rules=None, code_hash=None, clv=None):
    rules = rules or policy()
    code_hash = code_hash or fingerprint(rules)
    state = history.data.get("prospective_validation")
    if not state or state.get("protocol_id") != rules["protocol_id"]:
        if state:
            history.data.setdefault("previous_validation_protocols", []).append(deepcopy(state))
        state = {
            "protocol_id": rules["protocol_id"],
            "declared_at": now.isoformat(),
            "code_hash": code_hash,
            "rules": deepcopy(rules),
        }
        history.data["prospective_validation"] = state
    report = {
        "protocol_id": state["protocol_id"],
        "declared_at": state["declared_at"],
        "generated_at": now.isoformat(),
        "code_hash": code_hash,
        "rules": rules,
        "primary_strategy": rules["primary_strategy"],
        "live_ready": False,
    }
    if state["code_hash"] != code_hash:
        return {
            **report,
            "status": "protocol_changed",
            "reasons": ["code_or_policy_changed"],
            "frozen_code_hash": state["code_hash"],
        }
    rows = []
    invalid = 0
    for row in sorted(history.data.get("priced_totals_trials", []), key=lambda r: r.get("created_at", "")):
        if row.get("protocol") != PROTOCOL:
            continue
        created, kickoff = parse_time(row.get("created_at")), parse_time(row.get("kickoff"))
        if (
            not created
            or not kickoff
            or not parse_time(state["declared_at"]) <= created <= now
            or kickoff - created < timedelta(minutes=30)
        ):
            continue
        if any(
            abs(parse_time(old["kickoff"]) - kickoff) <= timedelta(minutes=15)
            and same_team_identity(row.get("home"), [old.get("home")])
            and same_team_identity(row.get("away"), [old.get("away")])
            for old in rows
        ):
            continue
        copy = deepcopy(row)
        anchor = copy.get("anchor") or {}
        if (
            any(
                not isinstance(anchor.get(name), (int, float))
                or not math.isfinite(anchor[name])
                or not 0 < anchor[name] < 1
                for name in MODELS
            )
            or not isinstance(anchor.get("point"), (int, float))
            or not math.isfinite(anchor["point"])
        ):
            invalid += 1
            continue
        pick = (copy.get("picks") or {}).get(rules["primary_strategy"])
        if pick:
            at = parse_time(pick.get("quote_observed_at"))
            if (
                not at
                or not -timedelta(seconds=5) <= created - at <= timedelta(minutes=15)
                or not isinstance(pick.get("odds"), (int, float))
                or not math.isfinite(pick["odds"])
                or not 1.5 <= pick["odds"] <= 3.2
            ):
                invalid += 1
                continue
        if copy.get("status") == "resolved":
            if (
                not isinstance(copy.get("total"), (int, float))
                or not math.isfinite(copy["total"])
                or not 0 <= copy["total"] <= 30
            ):
                invalid += 1
                continue
            resolved = parse_time(copy.get("resolved_at"))
            ids = (copy.get("result_evidence") or {}).get("provider_ids") or {}
            identified = bool(ids) and all(
                isinstance(values, list) and len(values) == 1 and values[0] for values in ids.values()
            )
            if not resolved or not kickoff < resolved <= now or not identified:
                copy["status"] = "pending"
        rows.append(copy)
    model = rules["primary_strategy"]
    metrics = summarize(rows)["models"][model]
    chosen = [r for r in rows if (r.get("picks") or {}).get(model)]
    overdue = [
        r
        for r in chosen
        if now - parse_time(r["kickoff"]) >= timedelta(hours=rules["settlement_grace_hours"])
        and r.get("status") != "resolved"
    ]
    settled = [r for r in chosen if r.get("status") == "resolved"]
    overdue_fraction = len(overdue) / max(1, len(settled) + len(overdue))
    clv = read_json(".data/daily_quality/priced-totals-clv.json", {}) if clv is None else clv
    references = []
    for row in settled:
        pick = row["picks"][model]
        reference = clv.get(row["id"] + "|" + model, {})
        at = parse_time(reference.get("reference_at"))
        kickoff = parse_time(row["kickoff"])
        if (
            reference.get("status") != "measured"
            or not at
            or not timedelta(0) < kickoff - at <= timedelta(minutes=45)
            or normalize_bookmaker_name(reference.get("book", "")) != normalize_bookmaker_name(pick.get("book", ""))
            or reference.get("point") != pick.get("point")
            or reference.get("side") != pick.get("side")
        ):
            continue
        price = reference.get("reference_price")
        if isinstance(price, (int, float)) and math.isfinite(price) and price > 1:
            references.append(pick["odds"] / price - 1)
    mean_clv = sum(references) / len(references) if references else None
    reasons = []
    if invalid:
        reasons.append("invalid_frozen_observations")
    for field, minimum in [("bets", "min_bets"), ("paired_count", "min_paired"), ("days", "min_match_days")]:
        if metrics[field] < rules[minimum]:
            reasons.append(minimum)
    interval = metrics.get("roi_interval_95")
    if not interval or interval["low"] <= 0:
        reasons.append("profit_not_established")
    interval = metrics.get("brier_difference_interval_95")
    if not interval or interval["high"] >= 0:
        reasons.append("probability_advantage_not_established")
    if len(references) < rules["min_clv"] or mean_clv is None or mean_clv <= 0:
        reasons.append("closing_price_advantage_not_established")
    if overdue_fraction > rules["max_overdue_fraction"]:
        reasons.append("too_many_unresolved_results")
    native_ready = not reasons and model == "current_goals"
    return {
        **report,
        "status": "collecting" if reasons else "validated_for_review",
        "reasons": reasons,
        "live_ready": native_ready,
        "cohort": len(rows),
        "metrics": metrics,
        "overdue": len(overdue),
        "invalid_observations": invalid,
        "overdue_fraction": overdue_fraction,
        "clv_measured": len(references),
        "mean_clv": mean_clv,
        "production_blocker": None
        if native_ready
        else "challenger_requires_explicit_model_promotion"
        if not reasons
        else "validation_incomplete",
    }


def live_admission(report, model_version, now):
    """The current production model is not the opponent research challenger."""
    at = parse_time(report.get("generated_at"))
    if not at or not timedelta(0) <= now - at <= timedelta(hours=4):
        return False, "validation_missing_or_stale"
    if report.get("code_hash") != fingerprint(policy()):
        return False, "validation_version_changed"
    # A positive challenger report is a review result, never permission to send
    # bets produced by a different algorithm. Promotion needs a matching native
    # model implementation and its own validation contract.
    from app.services.daily_context_reliability import GOAL_MODEL_VERSION

    if (
        model_version != GOAL_MODEL_VERSION
        or report.get("primary_strategy") != "current_goals"
        or report.get("status") != "validated_for_review"
        or not report.get("live_ready")
        or report.get("reasons")
        or report.get("rules") != policy()
    ):
        return False, "model_not_validated_for_live"
    rules = policy()
    m = report.get("metrics") or {}
    roi = m.get("roi_interval_95") or {}
    brier = m.get("brier_difference_interval_95") or {}
    numeric = [m.get(field, 0) for field in ("bets", "paired_count", "days")] + [
        roi.get("low", 0),
        brier.get("high", 0),
        report.get("mean_clv", 0),
        report.get("overdue_fraction", 1),
    ]
    if (
        not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numeric)
        or report.get("invalid_observations", 0)
        or any(
            m.get(field, 0) < rules[key]
            for field, key in [("bets", "min_bets"), ("paired_count", "min_paired"), ("days", "min_match_days")]
        )
        or roi.get("low", 0) <= 0
        or brier.get("high", 0) >= 0
        or report.get("clv_measured", 0) < rules["min_clv"]
        or (report.get("mean_clv") or 0) <= 0
        or report.get("overdue_fraction", 1) > rules["max_overdue_fraction"]
    ):
        return False, "validation_evidence_incomplete"
    return True, "validated_pilot"
