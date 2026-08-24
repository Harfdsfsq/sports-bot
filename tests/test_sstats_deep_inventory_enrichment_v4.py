from __future__ import annotations

from scripts import apply_sstats_deep_inventory_enrichment_v4 as deep

GLICKO_PAYLOAD = {
    "status": "OK",
    "data": {
        "glicko": {
            "homeRating": 1712.4,
            "homeWinProbability": 0.58,
            "homeXg": 1.7015864,
            "awayXg": 0.9053843,
        },
        "fixture": {
            "id": 1550087,
            "homeTeam": {"id": 1, "name": "AS Roma"},
            "awayTeam": {"id": 2, "name": "ACF Fiorentina"},
        },
    },
}

FORM_PAYLOAD = {
    "home": {
        "avgScore": 1.6,
        "avgConceded": 1.2,
        "avgOddsXg": 9.9,
        "avgOddsXgConceded": 9.9,
        "gamesCount": 25,
    },
    "away": {
        "avgScore": 1.0,
        "avgConceded": 1.1,
        "avgOddsXg": 9.9,
        "avgOddsXgConceded": 9.9,
        "gamesCount": 25,
    },
}

ODDS_PAYLOAD = {
    "status": "OK",
    "count": 2,
    "data": [
        {
            "bookmakerId": 7,
            "bookmakerName": "William Hill",
            "odds": [
                {
                    "marketId": 5,
                    "marketName": "Goals Over/Under",
                    "odds": [
                        {"name": "Over 2.5", "value": 1.85},
                        {"name": "Under 2.5", "value": 1.95},
                    ],
                },
                {
                    "marketId": 6,
                    "marketName": "Goals Over/Under First Half",
                    "odds": [{"name": "Over 2.5", "value": 7.5}],
                },
                {
                    "marketId": 1,
                    "marketName": "Match Winner",
                    "odds": [{"name": "Home", "value": 2.1}],
                },
            ],
        },
        {
            "bookmakerId": 1,
            "bookmakerName": "10Bet",
            "odds": [
                {
                    "marketId": 5,
                    "marketName": "Goals Over/Under",
                    "odds": [{"name": "Over 2.5", "value": 1.91}],
                }
            ],
        },
    ],
}

ROW = {
    "match_key": "2026-08-24|as roma|acf fiorentina",
    "canonical_match_id": "2026-08-24|as roma|acf fiorentina",
    "home_team": "AS Roma",
    "away_team": "ACF Fiorentina",
}


def test_glicko_xg_is_read_from_the_real_field_names() -> None:
    """data.glicko.homeXg, not the homexG the old extractor looked for."""
    assert deep.glicko_xg(GLICKO_PAYLOAD) == (1.702, 0.905)


def test_glicko_null_xg_is_not_treated_as_a_value() -> None:
    payload = {"data": {"glicko": {"homeXg": None, "awayXg": None}}}

    assert deep.glicko_xg(payload) == (None, None)


def test_form_lambdas_use_scoring_rates_and_ignore_odds_derived_xg() -> None:
    """avgOddsXg is 9.9 here on purpose: using it would rebuild the market circularity."""
    lam_home, lam_away, games = deep.form_lambdas(FORM_PAYLOAD)

    assert (lam_home, lam_away, games) == (1.35, 1.1, 25)


def test_form_lambdas_reject_a_sample_that_is_too_small() -> None:
    payload = {
        "home": {"avgScore": 3.0, "avgConceded": 0.2, "gamesCount": 2},
        "away": {"avgScore": 0.4, "avgConceded": 2.8, "gamesCount": 2},
    }

    assert deep.form_lambdas(payload) == (None, None, 2)


def test_glicko_xg_wins_over_the_form_blend() -> None:
    resolved = deep.resolve_expected_goals(GLICKO_PAYLOAD, FORM_PAYLOAD)

    assert resolved["source"] == "glicko_xg"
    assert (resolved["home"], resolved["away"]) == (1.702, 0.905)
    assert (resolved["lambda_home"], resolved["lambda_away"]) == (1.35, 1.1)


def test_form_blend_is_the_fallback_when_glicko_has_no_xg() -> None:
    resolved = deep.resolve_expected_goals({"data": {"glicko": {}}}, FORM_PAYLOAD)

    assert resolved["source"] == "last_games_form_goals"
    assert (resolved["home"], resolved["away"]) == (1.35, 1.1)


def test_mark_persists_provider_xg_and_claims_the_xg_source() -> None:
    row = dict(ROW, coverage={})

    deep.mark(
        row,
        "1550087",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        glicko_payload=GLICKO_PAYLOAD,
        last_stats_payload=FORM_PAYLOAD,
    )

    assert row["expected_home"] == 1.702
    assert row["expected_away"] == 0.905
    assert row["sstats_xg_source"] == "glicko_xg"
    assert row["sstats_lambda_home"] == 1.35
    assert row["sstats_form_games"] == 25
    assert row["coverage"]["xg"] is True
    assert "sstats" in row["xg_sources"]


def test_mark_does_not_claim_xg_without_a_numeric_pair() -> None:
    row = dict(ROW, coverage={})

    deep.mark(
        row,
        "1550087",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        last_stats_payload={"home": {"avgShots": 12}, "away": {"avgShots": 8}},
    )

    assert "expected_home" not in row
    assert row["coverage"]["context"] is True
    assert row["coverage"]["xg"] is False
    assert "xg_sources" not in row


def test_mark_never_treats_the_1_0_placeholder_as_coverage() -> None:
    row = dict(ROW, coverage={}, expected_home=1.0, expected_away=1.0)

    deep.mark(
        row,
        "1550087",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        last_stats_payload={"home": {}, "away": {}},
    )

    assert "sstats_xg_source" not in row
    assert row["coverage"]["xg"] is False
    assert "xg_sources" not in row


def test_totals_offers_are_parsed_and_half_time_markets_are_excluded() -> None:
    offers = deep.parse_sstats_totals_offers(ODDS_PAYLOAD, ROW, "1550087")

    assert len(offers) == 3
    assert {offer["bookmaker"] for offer in offers} == {"william hill", "10bet"}
    assert all(offer["point"] == 2.5 for offer in offers)
    assert all(offer["source"] == "sstats" for offer in offers)
    assert 7.5 not in {offer["price"] for offer in offers}

    over = next(offer for offer in offers if offer["bookmaker"] == "william hill" and offer["selection"] == "over")

    assert over["price"] == 1.85
    assert over["family"] == "totals"
    assert over["match_key"] == ROW["match_key"]


def test_null_odds_payload_is_survived() -> None:
    assert deep.parse_sstats_totals_offers({"status": "OK", "data": None}, ROW, "1") == []


def test_mark_claims_a_second_price_source_only_with_real_offers() -> None:
    row = dict(ROW, coverage={})
    offers = deep.parse_sstats_totals_offers(ODDS_PAYLOAD, row, "1550087")

    deep.mark(
        row,
        "1550087",
        deep_ok=True,
        detail_ok=False,
        odds_ok=bool(offers),
        before_context=0,
        before_odds=1,
        glicko_payload=GLICKO_PAYLOAD,
        offers=offers,
    )

    assert row["sstats_offer_count"] == 3
    assert row["sstats_offer_books"] == ["10bet", "william hill"]
    assert "sstats" in row["odds_sources"]
    assert row["coverage"]["odds"] is True

    empty_row = dict(ROW, coverage={})
    deep.mark(
        empty_row,
        "1550087",
        deep_ok=True,
        detail_ok=False,
        odds_ok=False,
        before_context=0,
        before_odds=1,
        glicko_payload=GLICKO_PAYLOAD,
        offers=[],
    )

    assert "odds_sources" not in empty_row
    assert empty_row["coverage"].get("odds") is not True


def test_identity_verifies_matching_teams() -> None:
    status, home, away = deep.identity_status(ROW, GLICKO_PAYLOAD)

    assert status == "verified"
    assert (home, away) == ("AS Roma", "ACF Fiorentina")


def test_identity_flags_a_crosswalk_id_pointing_at_another_fixture() -> None:
    row = {"home_team": "FC Dinamo Bucuresti", "away_team": "FC Universitatea Cluj"}

    status, _, _ = deep.identity_status(row, GLICKO_PAYLOAD)

    assert status == "mismatch"


def test_identity_is_unverified_rather_than_mismatch_without_row_names() -> None:
    status, _, _ = deep.identity_status({}, GLICKO_PAYLOAD)

    assert status == "unverified"
