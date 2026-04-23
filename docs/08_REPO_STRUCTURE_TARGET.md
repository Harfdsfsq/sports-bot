# Целевая структура репозитория после рефакторинга

```text
app/
  services/
    model.py
    quality.py
    candidate_integrity.py
    publish_gate.py
    calibration.py
  schemas.py
  state.py

scripts/
  load_env_safe.sh
  audit_odds_integrity.py
  build_fixed_run_bundle.py
  summarize_latest_run.py

config/
  main_clean_publish.env
  fixed_run_diagnostic.env

docs/
  00_EXEC_SUMMARY.md
  01_FULL_AUDIT.md
  02_REPAIR_ROADMAP.md
  03_CANONICAL_CANDIDATE_SPEC.md
  04_PUBLISH_GATE_POLICY.md
  05_TEST_AND_QA_PLAN.md
  06_DEPLOYMENT_MONITORING.md
  07_READY_FOR_PRODUCTION_CHECKLIST.md

.github/
  workflows/
    run-bot-fixed-run.yml
```
