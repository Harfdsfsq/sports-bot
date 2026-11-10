from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from scripts.build_day_inventory_core_v3 import install_strict_merge, install_strict_enrich

@dataclass
class Fixture:
    match_key: str
    home_team: str
    away_team: str
    commence_time: datetime
    sport_key: str = 'soccer'
    source: str = 'sstats'
    source_event_id: str = '1'
    metadata: dict = field(default_factory=dict)


def test_inventory_never_merges_youth_swapped_or_later_fixture():
    core = SimpleNamespace(Match=Fixture)
    install_strict_merge(core)
    now = datetime.now(UTC)
    first = Fixture('a', 'Arsenal', 'Chelsea', now)
    for incoming in [Fixture('a', 'Arsenal U21', 'Chelsea U21', now),
                     Fixture('b', 'Chelsea', 'Arsenal', now),
                     Fixture('b', 'Arsenal', 'Chelsea', now+timedelta(hours=2))]:
        rows, _ = core.merge_matches({'odds_api_io': [first], 'sstats': [incoming]}, None)
        assert len(rows) == 2
    rows, _ = core.merge_matches({'odds_api_io': [first], 'sstats': [Fixture('b', 'Arsenal', 'Chelsea', now)]}, None)
    assert len(rows) == 1


def test_discovery_ids_do_not_claim_price_or_context():
    core = SimpleNamespace(CORE_PROVIDERS=['sstats', 'odds_api_io'])
    install_strict_enrich(core)
    row = {'source_ids': {'sstats': '1', 'odds_api_io': '2'}}
    payload = core.enrich_payload_coverage({'matches': [row]})
    assert row['coverage']['context_discovery_hint']
    assert row['coverage']['odds_discovery_hint']
    assert not row['coverage']['ready_for_model']
    assert payload['counts']['matches_with_odds'] == 0


def test_sstats_server_page_cap_does_not_truncate_inventory(monkeypatch):
    import asyncio
    from scripts import build_day_inventory_core as core
    monkeypatch.setenv('SSTATS_API_KEY', 'test-only')
    monkeypatch.setenv('DAY_INVENTORY_SSTATS_MAX_REQUESTS', '4')
    offsets = []
    class Response:
        status_code = 200
        def __init__(self, rows): self.rows = rows
        def json(self): return self.rows
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, params):
            offsets.append(params['offset'])
            # Server returns just one row despite limit=1000.
            n = params['offset']
            return Response([{'Id': n+1, 'Home': f'Home {n}', 'Away': 'Away', 'LeagueName': 'League', 'DateTime': '2026-11-10T12:00:00Z'}] if n < 2 else [])
    monkeypatch.setattr(core, 'BudgetedAsyncClient', Client)
    _, stats = asyncio.run(core.fetch_sstats(SimpleNamespace(app_timezone='Europe/Moscow'), '2026-11-10'))
    assert offsets == [0, 1, 2]
    assert stats['rows_fetched'] == 2
    assert stats['pagination_complete']
