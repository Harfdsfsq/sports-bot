# Provider smoke

- status: **warning**
- enabled ok: 6/8
- missing keys: 0
- non-core env violations: 5

| provider | enabled | ok | status | items | http |
| --- | --- | --- | --- | ---: | ---: |
| odds_api_io | True | True | ok | 5 | 200 |
| sstats | True | True | ok | 5 | 200 |
| bzzoiro | True | True | skipped_preserve_runtime_quota |  |  |
| football_data | True | True | ok | 0 | 200 |
| thesportsdb | True | True | ok | 10 | 200 |
| weatherapi | True | True | ok | 2 | 200 |
| open_meteo | True | False | request_error |  |  |
| clubelo | True | False | ConnectTimeout:  |  |  |

## Non-core env violations
- ALLSPORTSAPI: enabled={'ALLSPORTSAPI_ENABLED': 'true'} limits={'ALLSPORTSAPI_REQUESTS_MAX_PER_RUN': '6', 'ALLSPORTSAPI_REQUEST_BUDGET_GRANTED': '6', 'ALLSPORTSAPI_PER_RUN_MAX': '6', 'ALLSPORTSAPI_MAX_HTTP_REQUESTS_PER_RUN': '6'}
- SPORTLOGIC: enabled={'SPORTLOGIC_QUERY_GUARD_ENABLED': 'true', 'SPORTLOGIC_CONTROLLED_ODDS_ENABLED': 'true', 'SPORTLOGIC_ENABLED': 'true', 'SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED': 'true'} limits={'SPORTLOGIC_REQUEST_BUDGET_GRANTED': '40', 'SPORTLOGIC_PER_RUN_MAX': '40', 'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN': '40', 'SPORTLOGIC_REQUESTS_MAX_PER_RUN': '40'}
- CURRENTS: enabled={'CURRENTS_NEWS_CONTEXT_ENABLED': 'true'} limits={}
- SHARPAPI: enabled={'SHARPAPI_TEXT_ENRICHMENT_ENABLED': 'true'} limits={}
- OPENFOOTBALL: enabled={} limits={'OPENFOOTBALL_PUBLIC_REQUEST_BUDGET_GRANTED': '2', 'OPENFOOTBALL_PUBLIC_MAX_HTTP_REQUESTS_PER_RUN': '2'}
