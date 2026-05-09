# Provider smoke

- status: **warning**
- enabled ok: 7/8
- missing keys: 0
- non-core env violations: 4

| provider | enabled | ok | status | items | http |
| --- | --- | --- | --- | ---: | ---: |
| odds_api_io | True | True | ok | 5 | 200 |
| sstats | True | True | ok | 5 | 200 |
| bzzoiro | True | True | ok | 50 | 200 |
| football_data | True | True | ok | 0 | 200 |
| thesportsdb | True | True | ok | 1 | 200 |
| weatherapi | True | True | ok | 2 | 200 |
| open_meteo | True | True | ok | 9 | 200 |
| clubelo | True | False | ConnectTimeout:  |  |  |

## Non-core env violations
- SPORTLOGIC: enabled={'SPORTLOGIC_QUERY_GUARD_ENABLED': 'true', 'SPORTLOGIC_BROAD_FALLBACK_ENABLED': 'true', 'SPORTLOGIC_CONTROLLED_ODDS_ENABLED': 'true'} limits={'SPORTLOGIC_PER_RUN_MAX': '30', 'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN': '30'}
- CURRENTS: enabled={'CURRENTS_NEWS_CONTEXT_ENABLED': 'true'} limits={}
- SHARPAPI: enabled={'SHARPAPI_TEXT_ENRICHMENT_ENABLED': 'true'} limits={}
- OPENFOOTBALL: enabled={} limits={'OPENFOOTBALL_PUBLIC_REQUEST_BUDGET_GRANTED': '2', 'OPENFOOTBALL_PUBLIC_MAX_HTTP_REQUESTS_PER_RUN': '2'}
