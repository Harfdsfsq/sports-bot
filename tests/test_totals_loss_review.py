from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.totals_loss_review import assess, run

NOW = datetime(2026, 11, 18, 9, tzinfo=UTC)


def fixture(key, *, support):
    def book(name, over, under):
        return {
            "book": name,
            "quotes": {
                side: {"price": price, "observed_at": NOW.isoformat()}
                for side, price in (("over", over), ("under", under))
            },
        }

    return {
        "id": key, "home": f"Home {key}", "away": f"Away {key}",
        "protocol": "priced-totals-v1", "validation_cohort_id": "cohort", "model_hash": "model",
        "created_at": NOW.isoformat(), "kickoff": (NOW + timedelta(hours=2)).isoformat(), "status": "pending",
        "picks": {"opponent": {"point": 2.0, "side": "under", "book": "Pari", "odds": 2.2,
                              "quote_observed_at": NOW.isoformat()}},
        "lines": [{"point": 2.0, "books": [book("Pari", 1.7, 2.2),
                                         book("Betano", 2.1, 1.75) if support else book("Betano", 1.7, 2.2)]}],
    }


def test_market_support_uses_fresh_pairs_at_the_exact_line_and_excludes_selected_book():
    row = fixture("A", support=True)
    result = assess(row)
    assert result["reason"] == "selected"
    assert result["reference_books"] == ["betano"]
    assert result["reference_probability"] > .5
    row["lines"][0]["books"][1]["quotes"]["over"]["observed_at"] = (NOW - timedelta(minutes=16)).isoformat()
    assert assess(row)["reason"] == "missing_reference_book"
    row["lines"][0]["point"] = 2.5
    assert assess(row)["reason"] == "missing_exact_line"


def test_book_alias_and_quote_mismatch_do_not_create_reference_support():
    row = fixture("A", support=True)
    row["lines"][0]["books"][1]["book"] = "PARI"
    assert assess(row)["reason"] == "duplicate_book_identity"
    row = fixture("A", support=True)
    row["picks"]["opponent"]["odds"] = 2.3
    assert assess(row)["reason"] == "selected_quote_mismatch"


def test_forward_comparison_preserves_refusals_and_counts_returns_separately():
    rows = [fixture("A", support=True), fixture("B", support=False), fixture("C", support=True)]
    baseline = {"cohort_id": "cohort", "code_hash": "model"}
    historical = fixture("D", support=True)
    historical["created_at"] = (NOW - timedelta(minutes=1)).isoformat()
    rows.append(historical)
    history = SimpleNamespace(data={"prospective_validation": baseline, "priced_totals_trials": rows}, save=lambda: None)
    original = deepcopy(history.data)
    first = run(history, NOW)
    assert first["new"] == 3 and first["filtered"]["pending"] == 2
    assert history.data["priced_totals_trials"] == original["priced_totals_trials"]
    assert history.data["prospective_validation"] == original["prospective_validation"]

    for row, total in zip(rows[:3], (1, 3, 2), strict=True):
        row.update(status="resolved", total=total, resolved_at=(NOW + timedelta(hours=4)).isoformat(),
                   result_evidence={"provider_ids": {"sstats": [row["id"]]}})
    # A later edit to the source prices must not change the stored decision.
    rows[1]["lines"] = deepcopy(rows[0]["lines"])
    second = run(history, NOW + timedelta(hours=4))
    assert second["new"] == 0 and second["losses_avoided"] == 1 and second["wins_skipped"] == 0
    assert second["filtered"]["wins"] == 1 and second["filtered"]["pushes"] == 1
    assert second["baseline"]["settled"] == 3 and second["filtered"]["settled"] == 2

    rows[0]["status"] = "pending"
    third = run(history, NOW + timedelta(hours=5))
    assert third["filtered"]["wins"] == 0 and third["filtered"]["pending"] == 1
