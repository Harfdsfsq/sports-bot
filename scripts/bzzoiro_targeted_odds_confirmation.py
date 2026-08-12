from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
QUEUE = EXPORT / 'latest-a-tier-targeted-enrichment-queue.json'
OUT = EXPORT / 'latest-bzzoiro-targeted-odds-confirmation.json'


def _load(path: Path, default: Any = None) -> Any:
    try: return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception: return {} if default is None else default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')


def _norm(s: Any) -> str:
    text = str(s or '').lower(); text = re.sub(r'[^a-zа-я0-9]+', ' ', text, flags=re.I).strip()
    stop = {'fc','cf','sc','afc','fk','csm','cs','club','u19','u21','ii','b','ac','as'}
    return ' '.join(t for t in text.split() if t not in stop)


def _sim(a: Any, b: Any) -> float:
    aa, bb = set(_norm(a).split()), set(_norm(b).split())
    return len(aa & bb) / max(len(aa | bb), 1) if aa and bb else 0.0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        out: list[dict[str, Any]] = []
        for key in ('events','rows','matches','items','data','odds','offers','confirmations'):
            val = payload.get(key)
            if isinstance(val, list): out.extend([r for r in val if isinstance(r, dict)])
            elif isinstance(val, dict): out.extend(_rows(val))
        return out
    return []


def _targets() -> list[dict[str, Any]]:
    q = _load(QUEUE, {})
    rows = q.get('bzzoiro_odds_targets') if isinstance(q, dict) and isinstance(q.get('bzzoiro_odds_targets'), list) else []
    return [r for r in rows if isinstance(r, dict)][:80]


def _home(row: dict[str, Any]) -> Any:
    return row.get('home_team') or row.get('home') or row.get('homeName') or row.get('team_home') or row.get('homeTeam') or row.get('event_home')


def _away(row: dict[str, Any]) -> Any:
    return row.get('away_team') or row.get('away') or row.get('awayName') or row.get('team_away') or row.get('awayTeam') or row.get('event_away')


def _offers(row: dict[str, Any]) -> Any:
    return row.get('offers') or row.get('odds') or row.get('markets') or row.get('prices') or row.get('bookmakers') or []


def _candidate_bzzoiro_artifacts() -> list[Path]:
    names = ['latest-bzzoiro-events.json','latest-bzzoiro-events-preview.json','latest-bzzoiro-odds.json','latest-bzzoiro-runtime.json','latest-bzzoiro-events-raw.json','latest-bzzoiro-odds-raw.json','latest-sstats-bzzoiro-odds-merge.json','latest-secondary-provider-matching.json','latest-signal-stack-runtime.json']
    return [EXPORT / n for n in names if (EXPORT / n).exists()]


def _extract_events() -> tuple[list[dict[str, Any]], list[str], bool]:
    out: list[dict[str, Any]] = []; scanned: list[str] = []; preview_only = False
    for path in _candidate_bzzoiro_artifacts():
        scanned.append(path.name); payload = _load(path, {})
        if isinstance(payload, dict) and payload.get('preview_only'):
            preview_only = True
        roots = [payload]
        if isinstance(payload, dict):
            for k in ('bzzoiro','data','events','matches','odds','rows'):
                if k in payload: roots.append(payload[k])
        for root in roots:
            for row in _rows(root):
                home, away = _home(row), _away(row)
                if not (home and away): continue
                offers = _offers(row)
                out.append({'artifact': path.name, 'home_team': home, 'away_team': away, 'kickoff': row.get('kickoff') or row.get('commence_time') or row.get('start_time') or row.get('event_date'), 'offers': offers, 'raw': row})
    return out, scanned, preview_only


def _offer_count(offers: Any) -> int:
    if isinstance(offers, list): return len(offers)
    if isinstance(offers, dict): return sum(_offer_count(v) for v in offers.values()) or len(offers)
    return 0


def _best_event(target: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best, score = None, 0.0
    for ev in events:
        direct = (_sim(target.get('home_team'), ev.get('home_team')) + _sim(target.get('away_team'), ev.get('away_team'))) / 2.0
        swapped = (_sim(target.get('home_team'), ev.get('away_team')) + _sim(target.get('away_team'), ev.get('home_team'))) / 2.0
        s = max(direct, swapped)
        if s > score: best, score = ev, s
    return best, score


def main() -> int:
    try:
        from scripts.persist_bzzoiro_runtime_artifacts import main as persist
        persist()
    except Exception: pass
    try:
        from scripts.trace_bzzoiro_report_source import main as trace
        trace()
    except Exception: pass
    targets = _targets(); events, scanned, preview_only = _extract_events(); confirmations: list[dict[str, Any]] = []
    matched = offers = 0
    for target in targets:
        ev, score = _best_event(target, events)
        if not ev or score < 0.58: continue
        matched += 1; cnt = _offer_count(ev.get('offers')); offers += cnt
        confirmations.append({'match_key': target.get('match_key'), 'home_team': target.get('home_team'), 'away_team': target.get('away_team'), 'target_odds_sources': target.get('odds_sources'), 'matched_bzzoiro_home': ev.get('home_team'), 'matched_bzzoiro_away': ev.get('away_team'), 'match_score': round(score,3), 'offers': cnt, 'artifact': ev.get('artifact'), 'source': 'bzzoiro', 'promotes_to_2source': bool(cnt > 0 and int(target.get('odds_sources') or 0) < 2)})
    promoted = sum(1 for c in confirmations if c.get('promotes_to_2source'))
    persist_report = _load(EXPORT/'latest-bzzoiro-runtime-artifact-persistence.json', {})
    if targets and not events:
        diagnosis = 'no_bzzoiro_events_or_odds_artifact'
    elif preview_only:
        diagnosis = 'preview_events_only_no_full_rows'
    elif matched and not offers:
        diagnosis = 'matched_without_offers'
    else:
        diagnosis = 'ok'
    payload = {'status':'ok','created_at_utc':datetime.now(UTC).isoformat(),'targets':len(targets),'bzzoiro_events_seen':len(events),'events_preview_only': preview_only,'matched_events':matched,'offers':offers,'two_source_promoted':promoted,'confirmations':confirmations[:80],'scanned_artifacts':scanned,'persistence':persist_report,'publication_contract_relaxed':False,'diagnosis':diagnosis}
    _write(OUT, payload); print(json.dumps({k:payload[k] for k in ('targets','bzzoiro_events_seen','events_preview_only','matched_events','offers','two_source_promoted','diagnosis')}, ensure_ascii=False)); return 0

if __name__ == '__main__': raise SystemExit(main())
