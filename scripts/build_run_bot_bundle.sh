#!/usr/bin/env bash
set -euo pipefail
mkdir -p artifacts/run-bot
cp -f .logs/debug-last-run.json artifacts/run-bot/debug-last-run.json 2>/dev/null || true
cp -f .data/state.json artifacts/run-bot/state.json 2>/dev/null || true
cp -f .data/exports/latest-picks.json artifacts/run-bot/latest-picks.json 2>/dev/null || true
cp -f .data/exports/latest-bets.json artifacts/run-bot/latest-bets.json 2>/dev/null || true
cp -f .data/exports/latest-matches.json artifacts/run-bot/latest-matches.json 2>/dev/null || true
cp -f .data/exports/latest-quality-report.json artifacts/run-bot/latest-quality-report.json 2>/dev/null || true
cp -f .data/exports/latest-controlled-fallback-report.json artifacts/run-bot/latest-controlled-fallback-report.json 2>/dev/null || true
cp -f artifacts/controlled-fallback-report.json artifacts/run-bot/controlled-fallback-report.json 2>/dev/null || true
python - <<'PY' || true
import json
from pathlib import Path
p=Path('.logs/debug-last-run.json')
out=Path('artifacts/run-bot/latest-run-summary.json')
out.parent.mkdir(parents=True, exist_ok=True)
if not p.exists():
    out.write_text('{}', encoding='utf-8')
else:
    d=json.loads(p.read_text(encoding='utf-8'))
    s=d.get('summary') or {}
    q=d.get('quality_report') or {}
    qs=q.get('summary') or {}
    payload={
      'created_at': d.get('created_at'),
      'matches_seen': s.get('matches_seen'),
      'matches_with_offers': s.get('matches_with_offers'),
      'contexts_built': s.get('contexts_built'),
      'candidates_before_quality': s.get('candidates_before_quality'),
      'candidates_after_quality': s.get('candidates'),
      'candidates_publishable': s.get('candidates_publishable'),
      'published': s.get('published'),
      'top_rejections': dict(list((s.get('rejections') or {}).items())[:20]) if isinstance(s.get('rejections'),dict) else {},
      'quality_summary': {k: qs.get(k) for k in ['settled_binary_bets','wins','losses','roi_pct','hit_rate_pct','avg_odds']},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
PY
(cd artifacts && zip -qr run-bot-bundle.zip run-bot controlled-fallback-report.json 2>/dev/null || zip -qr run-bot-bundle.zip run-bot)
