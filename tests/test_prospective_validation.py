from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.daily_context_reliability import GOAL_MODEL_VERSION
from app.services.prospective_validation import evaluate, live_admission, policy
from app.services.totals_strategy_lab import MODELS, PROTOCOL

NOW = datetime(2026, 11, 11, 18, tzinfo=UTC)


def cohort(model="opponent"):
    rules = {
        **policy(),
        "primary_strategy": model,
        "min_bets": 20,
        "min_paired": 20,
        "min_match_days": 10,
        "min_clv": 20,
    }
    rows = []
    clv = {}
    for i in range(24):
        kickoff = NOW - timedelta(days=20 - i // 2, hours=i % 2)
        pick = {
            "point": 2.5,
            "side": "over",
            "odds": 1.9,
            "book": "pari",
            "p": 0.9,
            "push": 0,
            "ev": 0.71,
            "quote_observed_at": (kickoff - timedelta(hours=2)).isoformat(),
        }
        row = {
            "id": str(i),
            "protocol": PROTOCOL,
            "validation_cohort_id": "test-cohort",
            "model_hash": "test",
            "pipeline_hash": "pipeline-1",
            "home": f"Home {i}",
            "away": f"Away {i}",
            "kickoff": kickoff.isoformat(),
            "created_at": (kickoff - timedelta(hours=2)).isoformat(),
            "result_evidence": {"records": 1, "provider_ids": {"sstats": [str(i)]}},
            "status": "resolved",
            "resolved_at": (kickoff + timedelta(hours=3)).isoformat(),
            "total": 3,
            "picks": {name: deepcopy(pick) if name == model else None for name in MODELS},
            "anchor": {"point": 2.5, **{name: 0.3 if name == "market" else 0.9 for name in MODELS}},
        }
        rows.append(row)
        clv[str(i) + "|" + model] = {
            **pick,
            "status": "measured",
            "reference_price": 1.8,
            "reference_at": (kickoff - timedelta(minutes=5)).isoformat(),
        }
    state = {
        "protocol_id": rules["protocol_id"],
        "declared_at": (NOW - timedelta(days=40)).isoformat(),
        "code_hash": "test",
        "cohort_id": "test-cohort",
        "rules": deepcopy(rules),
    }
    return SimpleNamespace(data={"priced_totals_trials": rows, "prospective_validation": state}), rules, clv


def test_historical_winner_does_not_count_after_new_protocol_declaration():
    h, rules, clv = cohort()
    h.data.pop("prospective_validation")
    frozen = deepcopy(h.data["priced_totals_trials"])
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["cohort"] == 0 and not report["live_ready"]
    assert h.data["priced_totals_trials"] == frozen
    assert h.data["prospective_validation"]["declared_at"] == NOW.isoformat()


def test_forward_cohort_can_validate_challenger_but_never_other_live_model():
    h, rules, clv = cohort()
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["status"] == "validated_for_review"
    assert report["metrics"]["bets"] == 24 and report["clv_measured"] == 24
    assert not report["live_ready"]
    assert not live_admission(report, GOAL_MODEL_VERSION, NOW)[0]


def test_changed_model_code_blocks_protocol_without_reusing_old_results():
    h, rules, clv = cohort()
    frozen = deepcopy(h.data)
    report = evaluate(h, NOW, rules=rules, code_hash="different", clv=clv)
    assert report["status"] == "protocol_changed" and not report["live_ready"]
    assert h.data == frozen


@pytest.mark.parametrize(
    "problem", ["losses", "future_result", "wrong_book_clv", "old_clv", "too_few_days", "missing_results"]
)
def test_validation_requires_real_profit_probability_and_price_evidence(problem):
    h, rules, clv = cohort()
    if problem == "losses":
        for row in h.data["priced_totals_trials"]:
            row["total"] = 0
    if problem == "future_result":
        for row in h.data["priced_totals_trials"]:
            row["resolved_at"] = (NOW + timedelta(days=1)).isoformat()
    if problem == "wrong_book_clv":
        for row in clv.values():
            row["book"] = "Other"
    if problem == "old_clv":
        for row in clv.values():
            row["reference_at"] = (NOW - timedelta(days=40)).isoformat()
    if problem == "too_few_days":
        rules["min_match_days"] = 30
    if problem == "missing_results":
        for row in h.data["priced_totals_trials"][:3]:
            row["status"] = "pending"
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["status"] == "collecting" and report["reasons"] and not report["live_ready"]


def test_live_release_requires_matching_native_strategy_and_complete_report(monkeypatch):
    h, rules, clv = cohort("current_goals")
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    monkeypatch.setattr("app.services.prospective_validation.policy", lambda: rules)
    monkeypatch.setattr("app.services.prospective_validation.fingerprint", lambda _: "test")
    assert live_admission(report, GOAL_MODEL_VERSION, NOW)[0]
    assert not live_admission(report, "unrelated-model", NOW)[0]
    assert not live_admission(report, GOAL_MODEL_VERSION, NOW + timedelta(hours=5))[0]
    broken = deepcopy(report)
    broken.pop("metrics")
    assert not live_admission(broken, GOAL_MODEL_VERSION, NOW)[0]


def test_same_fixture_alias_cannot_replace_earlier_losing_forecast():
    h, rules, clv = cohort()
    first = h.data["priced_totals_trials"][0]
    first["home"] = "AS Arsenal"
    first["total"] = 0
    later = deepcopy(first)
    later.update(
        id="alias",
        home="Arsenal",
        total=3,
        created_at=(datetime.fromisoformat(first["created_at"]) + timedelta(minutes=1)).isoformat(),
    )
    h.data["priced_totals_trials"].append(later)
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["cohort"] == 24 and report["metrics"]["paper_pnl"] < 24 * 0.9
