from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.schemas import MatchContext
from app.services.daily_context_reliability import blend_contexts, evidence_quality, sample_support
from app.services.daily_quality_policy import quality_decision
from app.services.quality import PredictionQualityService


@pytest.fixture(autouse=True)
def daily(monkeypatch):
    monkeypatch.setenv('PUBLICATION_PROFILE', 'daily_quality')
    monkeypatch.setenv('DAILY_A_MIN_TEAM_SAMPLE', '6')
    monkeypatch.setenv('DAILY_A_MAX_GOAL_DISAGREEMENT', '.65')


def context(sample=None, source='sstats', home=1.4, away=1.2):
    return MatchContext(source, {}, expected_home=home, expected_away=away,
                        confidence=75, details={} if sample is None else
                        {'home_recent_count': sample, 'away_recent_count': sample})


def candidate(evidence):
    now = datetime.now(UTC)
    return SimpleNamespace(source_summary={'daily_context_reliability': evidence,
                           'context_sources_count': 1, 'context_source': 'sstats',
                           'daily_context_observed_at': now.isoformat(),
                           'daily_price_observed_at': now.isoformat()},
                           books_count=1, sources_count=1, confidence=75.,
                           ev_pct=10., edge_pct=5., adjusted_probability=.65, odds=1.9,
                           commence_time=now + timedelta(hours=1), family='totals',
                           expected_home=1.4, expected_away=1.2)


def blended(*contexts):
    return blend_contexts(list(contexts), lambda c: 1.)


def test_quality_does_not_increase_when_model_claims_more_value():
    c = candidate(blended(context(12)).details['daily_context_reliability'])
    service = PredictionQualityService(SimpleNamespace())
    first = service._candidate_quality_score(c, [], {}, False)
    c.ev_pct, c.edge_pct, c.confidence = 100, 50, 100
    assert service._candidate_quality_score(c, [], {}, False) == first
    assert first >= 78  # One well-supported context and one line can qualify.


@pytest.mark.parametrize('sample', [0, 1, 2])
def test_thin_sample_alone_cannot_pass_b(sample):
    merged = blended(context(sample))
    assert merged.confidence < 60
    c = candidate(merged.details['daily_context_reliability'])
    c.source_summary['quality_score'] = evidence_quality(c)
    assert c.source_summary['quality_score'] < 65
    assert 'below_b_quality' in quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=datetime.now(UTC))[1]


def test_unknown_sample_can_be_b_but_never_a_even_with_large_ev():
    merged = blended(context())
    c = candidate(merged.details['daily_context_reliability'])
    c.source_summary['quality_score'] = 100  # Defend against a stale legacy quality score.
    tier, reasons, _ = quality_decision(c, {'odds_sources_count': 1, 'context_sources_count': 1, 'books_count': 1}, now=datetime.now(UTC))
    assert tier == 'B' and not reasons
    assert evidence_quality(c) >= 65
    assert merged.confidence <= 69


def test_multiple_unknown_apis_do_not_create_a_supported_sample():
    merged = blended(context(), context(source='espn'))
    assert not merged.details['daily_context_reliability']['a_supported']
    assert merged.confidence <= 69


def test_flat_blend_is_order_independent_and_downweights_one_match_table():
    weak = MatchContext('openligadb', {}, expected_home=.3, expected_away=4., confidence=75,
                        details={'openligadb_table_home_matches': 1, 'openligadb_table_away_matches': 1})
    strong = context(12)
    extra = context(source='espn', home=1.5, away=1.1)
    a, b = blended(weak, strong, extra), blended(extra, strong, weak)
    assert a.expected_home == pytest.approx(b.expected_home)
    assert a.expected_away == pytest.approx(b.expected_away)
    assert a.expected_away < 1.7
    assert weak.expected_away == 4.  # Do not alter stored provider responses.


def test_disagreement_limits_a_without_forcing_two_apis():
    merged = blended(context(12, home=.3, away=.3), replace(context(12, home=4, away=4), source='sstats_form'))
    assert not merged.details['daily_context_reliability']['a_supported']
    c = candidate(merged.details['daily_context_reliability'])
    assert evidence_quality(c) < 78


@pytest.mark.parametrize('source,details,count', [
    ('football_data', {'football_data_home_sample': 8, 'football_data_away_sample': 6}, 6),
    ('football_data', {'football_data_home_played': 1, 'football_data_away_played': 2}, 1),
    ('thesportsdb', {'thesportsdb_home_played': 10, 'thesportsdb_away_played': 8}, 8),
    ('openligadb', {'openligadb_history_home_sample': 6, 'openligadb_history_away_sample': 5}, 5),
    ('espn', {'home_recent_count': 20, 'away_recent_count': 20}, None),
    ('sstats', {'home_recent_count': float('nan'), 'away_recent_count': 12}, None),
])
def test_sample_counts_are_provider_specific_and_finite(source, details, count):
    assert sample_support(MatchContext(source, {}, details=details))['minimum'] == count


def test_learning_excludes_shadow_without_modifying_accounting():
    service = PredictionQualityService(SimpleNamespace())
    real = {'prediction_id': 'real', 'status': 'lost', 'adjusted_probability_pct': 60,
            'market_probability_pct': 50, 'stake_amount': 1, 'pnl': -1}
    shadow = {**real, 'prediction_id': 'shadow', 'tracking_mode': 'shadow', 'status': 'won', 'pnl': 100}
    report = service.build_quality_report([real, shadow])
    assert report['summary']['excluded_shadow_rows'] == 1
    assert report['summary']['settled_binary_bets'] == 1
    assert report['summary']['learning_population'] == 'published_only'
    assert shadow['pnl'] == 100
    paired = report['calibration']['paired_probability_scores']
    assert paired['count'] == 1
    assert paired['model']['brier'] == .36
    assert paired['market']['brier'] == .25


def test_push_is_not_binary_loss_and_three_outcome_score_uses_unconditional_probabilities():
    service = PredictionQualityService(SimpleNamespace())
    row = {'status': 'push', 'is_hit': False, 'adjusted_probability_pct': 60, 'model_push_probability': .2}
    assert service._binary_result(row) is None
    score = service._win_push_loss_scores([row, {**row, 'status': 'void'}])
    assert score['count'] == 1
    assert score['brier'] == pytest.approx(.48**2 + .8**2 + .32**2, abs=1e-5)
    assert score['log_loss'] == pytest.approx(1.60944)


def test_old_standings_without_sample_count_cannot_be_a_or_b_alone():
    old = context(source='football_data')
    old.details['football_data_mode'] = 'standings'
    merged = blended(old)
    assert merged.confidence < 60
    assert merged.details['daily_context_reliability']['all_thin']


def test_invalid_goal_values_do_not_enter_blend_or_create_supported_goal_evidence():
    bad = context(20, home=float('nan'), away='broken')
    merged = blended(bad, context(8))
    assert merged.expected_home == 1.4 and merged.expected_away == 1.2
    single = blended(bad)
    assert single.expected_home is None and single.expected_away is None
    assert not single.details['daily_context_reliability']['a_supported']


def test_sub_one_percent_is_not_interpreted_as_fraction_in_daily_ledger():
    service = PredictionQualityService(SimpleNamespace())
    assert service._probability({'adjusted_probability_pct': .8}) == .008


def test_factory_confidence_cannot_rise_from_claimed_ev_or_price_premium(monkeypatch):
    from app.config import Settings
    from app.schemas import Match, Offer
    from app.services.model import CandidateFactory
    for line in __import__('pathlib').Path('config/daily_quality.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            monkeypatch.setenv(key, value)
    factory = CandidateFactory(Settings(_env_file=None))
    now = datetime.now(UTC)
    m = Match('day_inventory', 'test', 'soccer', 'Premier League', 'Home', 'Away',
              now + timedelta(hours=1), 'home', 'away', 'premier')
    ctx = blended(context(12))
    kwargs = dict(match=m, family='totals', selection='Under', point=2.5,
                  offers=[Offer('odds_api_io', 'Bet365', 'totals', 'Under', 2., 2.5)],
                  market_prob=.5, reasons=[], expected_home=1.4, expected_away=1.2,
                  model_mode='xg_total', context=ctx)
    low = factory._candidate_from_bucket(**kwargs, model_prob=.55)
    high = factory._candidate_from_bucket(**kwargs, model_prob=.95)
    assert high.ev_pct > low.ev_pct
    assert high.confidence == low.confidence


def test_legacy_quality_formula_remains_outside_daily_profile(monkeypatch):
    c = candidate(blended(context(12)).details['daily_context_reliability'])
    c.league_name = 'Premier League'
    c.selection_key, c.point, c.selection = 'under', 2.5, 'Under'
    monkeypatch.setenv('PUBLICATION_PROFILE', 'legacy')
    service = PredictionQualityService(SimpleNamespace())
    first = service._candidate_quality_score(c, [], {}, False)
    c.ev_pct = 1
    assert service._candidate_quality_score(c, [], {}, False) < first
