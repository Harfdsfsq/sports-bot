# HARIZON hybrid publication policy patch

Дата: 2026-05-22

## Что меняется

Вводится гибридная схема публикации controlled fallback:

- **Tier A**: прежний строгий режим — требуется `2+ independent odds sources`, `2+ price confirmations`, `2+ context sources`, чистые quality/xG/value guards.
- **Tier B**: разрешён `1 independent odds source`, только если одновременно есть:
  - `2+` подтверждения цены/букмекера;
  - `3+` контекстных подтверждения;
  - market family только `totals` или `spreads`;
  - canonical edge не ниже `4.0 п.п.`;
  - EV не ниже `7.0%`;
  - confidence не ниже `76.0%`;
  - quality не ниже `78.0`;
  - xG/market sanity не конфликтует со ставкой;
  - quality stops не запрещают публикацию.
- **Tier C**: остаётся watch-only, если явно не включён отдельным флагом.

Важно: SStats не становится источником текущих линий. Он остаётся источником контекста. Bzzoiro может быть источником линий только через реальные odds/comparison endpoints, а Bzzoiro prediction/xG/stats считаются контекстом.

## Изменённые файлы

- `.github/workflows/run-bot.yml`
- `scripts/publish_controlled_fallback.py`
- `scripts/apply_publication_family_policy.py`
- `scripts/apply_harizon_runtime_policy.py`
- `scripts/apply_per_run_api_quota_contract.py`
- `tests/test_controlled_fallback_single_line_context.py`

## Проверка

```bash
python -m pytest tests/test_controlled_fallback_single_line_context.py -q
# 3 passed

python -m pytest -q
# 48 passed
```

## Ключевые env-флаги

```env
CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED=true
CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_ALLOWED_FAMILIES=totals,spreads
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_ODDS_SOURCES=1
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_PRICE_CONFIRMATIONS=2
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_BOOKS=2
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONTEXT_SOURCES=3
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EDGE_PP=4.0
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EV_PCT=7.0
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONFIDENCE=76.0
CONTROLLED_FALLBACK_SINGLE_LINE_MIN_QUALITY=78.0
CONTROLLED_FALLBACK_SINGLE_LINE_REQUIRE_XG_SANITY=true
```
