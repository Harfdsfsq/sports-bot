import importlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import conservative_totals as c

NOW = datetime(2026, 11, 15, 10, tzinfo=UTC)
BASE = "unchanged-baseline"


def source(i="next", *, created=NOW):
    market = (1 / 1.7) / (1 / 1.7 + 1 / 2.2)
    a = dict.fromkeys(c.MODELS, .7)
    a.update(point=2.5, market=market)
    quotes = {side: {"price": odds, "observed_at": created.isoformat()}
              for side, odds in (("over", 1.7), ("under", 2.2))}
    return {"id": i, "home": "Home " + i, "away": "Away " + i, "protocol": "priced-totals-v1",
            "created_at": created.isoformat(), "kickoff": (created + timedelta(hours=2)).isoformat(),
            "model_hash": BASE, "status": "pending", "anchor": a, "rates": {"shrunk": 3.5},
            "evidence": {"best_team_sample": 10, "supported_weight_share": .8},
            "lines": [{"point": 2.5, "books": [{"book": b, "quotes": deepcopy(quotes)} for b in ("Unibet", "Pari")]}]}


def profile():
    return {"status": "validated", "alpha": .5, "intercept": .1}


def training():
    rows = []
    for i in range(200):
        r = source(str(i), created=NOW - timedelta(days=201-i))
        probability = .85 if i % 2 else .15
        r["anchor"] = {**dict.fromkeys(c.MODELS, probability), "market": .5, "point": 2.5}
        r["rates"]["shrunk"] = 4. if i % 2 else 2.
        r.update(status="resolved", total=4 if i % 2 else 1,
                 resolved_at=(datetime.fromisoformat(r["kickoff"]) + timedelta(hours=3)).isoformat(),
                 result_evidence={"provider_ids": {"sstats": [str(i + 1)]}})
        rows.append(r)
    return rows


def test_temporal_calibration_does_not_train_on_holdout_or_future_results():
    rules, rows = c.policy(), training()
    p = c.fit(rows, NOW, rules, BASE)
    assert p["status"] == "validated" and p["train_count"] == 150 and p["validation_count"] == 50
    changed = deepcopy(rows)
    for r in changed[-50:]:
        r["total"] = 1 if r["total"] == 4 else 4
    rejected = c.fit(changed, NOW, rules, BASE)
    assert rejected["status"] == "market_not_beaten"
    assert rejected["alpha"] == p["alpha"] and rejected["intercept"] == p["intercept"]
    assert rejected["goal_rate_factor"] == p["goal_rate_factor"]
    future = deepcopy(rows)
    for r in future:
        r["resolved_at"] = (NOW + timedelta(hours=1)).isoformat()
    assert c.fit(rows + future, NOW, rules, BASE) == p
    assert c.fit(future, NOW, rules, BASE)["count"] == 0
    assert c.fit(rows, NOW, rules, "another-model")["count"] == 0


@pytest.mark.parametrize("problem", ["wrong_line", "unknown_sample", "thin", "disagreement", "stale", "one_book",
                                      "duplicate_book", "unvalidated", "quote_mismatch", "no_value"])
def test_selection_fails_closed(problem):
    r, p, rules = source(), profile(), c.policy()
    pick, reason = c.choose(r, p, rules, NOW)
    assert reason == "selected" and pick["side"] == "over" and pick["stressed_ev"] >= .02
    if problem == "wrong_line":
        r["anchor"]["point"] = 3.
    elif problem == "unknown_sample":
        r["evidence"].pop("best_team_sample")
    elif problem == "thin":
        r["evidence"]["supported_weight_share"] = .2
    elif problem == "disagreement":
        r["anchor"]["opponent"] = .3
    elif problem == "stale":
        r["lines"][0]["books"][0]["quotes"]["under"]["observed_at"] = (NOW-timedelta(minutes=16)).isoformat()
    elif problem == "one_book":
        r["lines"][0]["books"].pop()
    elif problem == "duplicate_book":
        r["lines"][0]["books"][1]["book"] = "unibet"
    elif problem == "unvalidated":
        p["status"] = "market_not_beaten"
    elif problem == "quote_mismatch":
        r["anchor"]["market"] = .61
    elif problem == "no_value":
        rules["probability_stress"] = .2
    assert c.choose(r, p, rules, NOW)[0] is None


def history(rows):
    return SimpleNamespace(data={"priced_totals_trials": rows, "prospective_validation": {"code_hash": BASE}},
                           save=lambda: None)


def test_forward_only_frozen_decisions_and_quarantined_results(monkeypatch):
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: profile())
    h = history([source("old", created=NOW-timedelta(minutes=1)), source()])
    previous = deepcopy(h.data)
    first = c.run(h, NOW)
    assert first["observed"] == first["selected"] == 1 and first["settled"] == 0
    assert h.data["prospective_validation"] == previous["prospective_validation"]
    assert h.data["priced_totals_trials"] == previous["priced_totals_trials"]
    frozen = deepcopy(h.data["conservative_totals"]["observations"][0])
    # A rejected profile later cannot rewrite a frozen winning or losing pick.
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: {"status": "market_not_beaten"})
    h.data["priced_totals_trials"][1].update(status="resolved", total=1,
        resolved_at=(NOW+timedelta(hours=4)).isoformat(), result_evidence={"provider_ids": {"sstats": ["7"]}})
    report = c.run(h, NOW+timedelta(hours=4))
    assert report["losses"] == 1 and report["paper_pnl"] == -1 and not report["live_ready"]
    assert report["paired_count"] == 1 and report["brier"] > report["market_brier"]
    assert h.data["conservative_totals"]["observations"][0]["pick"] == frozen["pick"]
    assert h.data["conservative_totals"]["observations"][0]["calibration"] == frozen["calibration"]
    h.data["priced_totals_trials"][1]["status"] = "pending"
    report = c.run(h, NOW+timedelta(hours=5))
    assert report["settled"] == 0 and report["paper_pnl"] == 0 and report["selected"] == 1


def test_abstention_is_frozen_and_never_upgraded_after_repeated_runs(monkeypatch):
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: {"status": "market_not_beaten"})
    h = history([source()])
    assert c.run(h, NOW)["selected"] == 0
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: profile())
    assert c.run(h, NOW+timedelta(minutes=1))["selected"] == 0
    assert len(h.data["conservative_totals"]["observations"]) == 1


def test_model_changes_archive_old_losses_without_retroactive_credit(monkeypatch):
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: profile())
    h = history([source()])
    c.run(h, NOW)
    old = deepcopy(h.data["conservative_totals"])
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fingerprint", lambda *a: "new-epoch")
    report = c.run(h, NOW+timedelta(minutes=1))
    assert report["observed"] == 0
    assert h.data["previous_conservative_totals"] == [old]


def test_duplicate_or_invalid_results_cannot_expand_calibration():
    rows = training()
    original = c.fit(rows, NOW, c.policy(), BASE)
    duplicates = deepcopy(rows)
    for r in duplicates:
        r["id"] += "-duplicate"
        r["created_at"] = (datetime.fromisoformat(r["created_at"]) + timedelta(minutes=1)).isoformat()
        r["total"] = 1 if r["total"] == 4 else 4
    assert c.fit(rows + duplicates, NOW, c.policy(), BASE) == original
    for r in rows:
        r["result_evidence"]["provider_ids"]["sstats"].append("conflicting-id")
    assert c.fit(rows, NOW, c.policy(), BASE)["count"] == 0


def test_under_selection_and_settlement_use_the_selected_side(monkeypatch):
    r = source()
    r["anchor"] = {k: 1-v if k != "point" else v for k, v in r["anchor"].items()}
    for book in r["lines"][0]["books"]:
        book["quotes"]["over"], book["quotes"]["under"] = book["quotes"]["under"], book["quotes"]["over"]
    down = {"status": "validated", "alpha": .5, "intercept": -.1}
    pick, reason = c.choose(r, down, c.policy(), NOW)
    assert reason == "selected" and pick["side"] == "under"
    monkeypatch.setattr(importlib.import_module("app.services.conservative_totals"), "fit", lambda *a: down)
    h = history([r])
    c.run(h, NOW)
    r.update(status="resolved", total=1, resolved_at=(NOW+timedelta(hours=4)).isoformat(),
             result_evidence={"provider_ids": {"sstats": ["7"]}})
    report = c.run(h, NOW+timedelta(hours=4))
    assert report["wins"] == 1 and report["paper_pnl"] == pytest.approx(.7)


def test_goal_bias_correction_uses_training_goals_only_and_checks_rate():
    rows = training()
    p = c.fit(rows, NOW, c.policy(), BASE)
    assert .8 <= p["goal_rate_factor"] < 1
    changed = deepcopy(rows)
    for row in changed[-50:]:
        row["total"] = 20 if row["total"] > 2.5 else 0
    q = c.fit(changed, NOW, c.policy(), BASE)
    assert q["goal_rate_factor"] == p["goal_rate_factor"]
    assert q["training_hash"] == p["training_hash"]
    assert q["validation_hash"] != p["validation_hash"]
    missing = source()
    missing.pop("rates")
    assert c.choose(missing, p, c.policy(), NOW)[0] is None
    up = source()
    up["rates"]["shrunk"] = float("nan")
    assert c.corrected_anchor(up, p, 2.5) is None
