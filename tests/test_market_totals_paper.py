from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.services.goal_research import GoalHistory
from app.services.market_totals_paper import fit, opportunity, run
from app.services.totals_strategy_clv import PATH, read_json

NOW = datetime(2026, 11, 26, 10, tzinfo=UTC)


def fixture(key, at=NOW):
    books = []
    for name, over, under in [("pari", 2.05, 1.80), ("1xbet", 1.65, 2.2), ("betano", 1.68, 2.15)]:
        books.append({"book": name, "quotes": {
            side: {"price": price, "observed_at": at.isoformat()}
            for side, price in (("over", over), ("under", under))
        }})
    return {
        "id": key, "home": "Home", "away": "Away", "match_key": key, "status": "pending",
        "model_hash": "baseline", "protocol": "priced-totals-v1", "created_at": at.isoformat(),
        "kickoff": (at + timedelta(hours=1)).isoformat(),
        "lines": [{"point": 2.5, "books": books, "predictions": {"shrunk": {"over": .60}}}],
        "evidence": {"best_team_sample": 8, "supported_weight_share": .90},
    }


def test_market_reference_excludes_execution_book_and_requires_paired_fresh_prices():
    row = fixture("match")
    quote = opportunity(row)
    assert quote["book"] == "pari" and quote["side"] == "over"
    assert quote["reference_books"] == ["1xbet", "betano"]
    assert quote["market_over"] == pytest.approx((2.2 / (2.2 + 1.65) + 2.15 / (2.15 + 1.68)) / 2)
    row["lines"][0]["books"][1]["quotes"]["under"]["observed_at"] = (NOW - timedelta(seconds=61)).isoformat()
    assert opportunity(row) is None


def test_fit_excludes_results_known_only_after_the_validation_boundary():
    examples = []
    for i in range(200):
        created = NOW + timedelta(days=i)
        observed = created + timedelta(days=1) if i < 100 else NOW + timedelta(days=250)
        examples.append((created, observed, str(i), .8 if i % 2 else .2, .5, float(i % 2)))
    report = fit(examples)
    assert report["train_count"] == 100 and report["validation_count"] == 50
    assert report["status"] == "validated_correction" and report["alpha"] > 0


def test_forward_decision_freezes_quote_skips_old_fixture_and_rechecks_settlement(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history = GoalHistory(tmp_path / "history.json")
    current = fixture("fresh")
    history.data.update(prospective_validation={"code_hash": "baseline"},
                        priced_totals_trials=[fixture("old", NOW - timedelta(minutes=1)), current])
    first = run(history, NOW + timedelta(seconds=2), capture_started_at=NOW)
    assert first["mode"] == "paper_only" and not first["live_ready"]
    assert first["new"] == first["selected"] == first["pending"] == 1
    state = history.data["market_totals_paper"]["decisions"]
    frozen = deepcopy(state["fresh"])
    assert len(read_json(PATH, {})) == 1
    current["lines"][0]["books"][0]["quotes"]["over"]["price"] = 1.3
    current.update(status="resolved", total=3, resolved_at=(NOW + timedelta(hours=3)).isoformat(),
                   result_evidence={"provider_ids": {"sstats": ["12"]}})
    report = run(history, NOW + timedelta(hours=4))
    assert report["new"] == 0 and report["challenger"]["paper_pnl"] == 1.05
    assert report["market_control"]["paper_pnl"] == 1.05 and state["fresh"] == frozen
    current.update(status="pending")
    assert run(history, NOW + timedelta(hours=4))["challenger"]["settled"] == 0
