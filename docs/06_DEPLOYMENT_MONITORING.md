# Deploy и monitoring

## Deploy порядок
1. Обновить fixed-run workflow
2. Включить canonical fields
3. Включить hard reject
4. Включить main publish policy
5. Оставить shadow collection включённой
6. 7 дней гонять в диагностическом режиме
7. Только потом открывать main publish

## Monitoring
Собирать:
- total matches seen
- candidates_before_quality
- candidates_after_quality
- suspicious_candidates
- suspicious_published_candidates
- avg_odds_main
- avg_odds_all
- ROI rolling 7d
- ROI rolling 30d
- fallback_count
- empty_run_count
- publish_window_skip_count

## Alert conditions
- suspicious ratio > 10%
- published suspicious > 0
- ROI rolling 7d < -10%
- avg_odds_main > 2.5
- empty_run_count 3 раза подряд
- odds source missing

## Artifacts
Всегда хранить:
- latest-run.json
- debug-last-run.json
- latest-picks.json
- latest-quality-report.json
- latest-candidate-integrity.json
- fixed-run-bundle.zip
