from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import CandidateBet, Match
from app.utils import candidate_selection_key


class JsonStateStore:
    def __init__(self, state_path: str, debug_path: str) -> None:
        self.state_path = Path(state_path)
        self.debug_path = Path(debug_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _default_state(self) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        return {
            'version': 2,
            'updated_at': now,
            'last_run': {},
            'bankroll': {
                'enabled': True,
                'currency': 'units',
                'starting_balance': 1000.0,
                'current_balance': 1000.0,
                'peak_balance': 1000.0,
                'open_exposure': 0.0,
                'closed_pnl': 0.0,
                'total_staked': 0.0,
                'bets_published': 0,
                'bets_settled': 0,
                'wins': 0,
                'losses': 0,
                'pushes': 0,
                'voids': 0,
            },
            'bets': [],
            'published_candidates': [],
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding='utf-8'))
        except Exception:
            return self._default_state()
        state = self._default_state()
        if isinstance(payload, dict):
            state.update({k: v for k, v in payload.items() if k in {'version', 'updated_at', 'last_run', 'bets', 'published_candidates', 'bankroll'}})
            if isinstance(payload.get('bankroll'), dict):
                state['bankroll'].update(payload['bankroll'])
        return state

    def _save(self) -> None:
        self._state['updated_at'] = datetime.now(UTC).isoformat()
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding='utf-8')

    def save_run(self, status: str, summary: dict[str, Any] | None = None, error_text: str | None = None) -> None:
        self._state['last_run'] = {
            'status': status,
            'at': datetime.now(UTC).isoformat(),
            'summary': summary or {},
            'error': error_text,
        }
        self._save()

    def write_debug(self, payload: dict[str, Any]) -> None:
        self.debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _sync_bankroll_defaults(self, settings: Any) -> None:
        bank = self._state.setdefault('bankroll', {})
        if not bank:
            bank.update(self._default_state()['bankroll'])
        starting = float(bank.get('starting_balance') or 0.0)
        if starting <= 0:
            starting = float(getattr(settings, 'bankroll_starting_balance', 1000.0) or 1000.0)
        current = float(bank.get('current_balance') or 0.0)
        if current <= 0:
            current = starting
        bank['enabled'] = bool(getattr(settings, 'bankroll_enabled', True))
        currency = str(getattr(settings, 'bankroll_currency', 'units') or 'units').strip()
        if currency.lower() == 'u':
            currency = 'units'
        bank['currency'] = currency
        bank['starting_balance'] = starting
        bank['current_balance'] = current
        bank['peak_balance'] = max(float(bank.get('peak_balance') or 0.0), current, starting)
        for key in ('open_exposure', 'closed_pnl', 'total_staked'):
            bank[key] = float(bank.get(key) or 0.0)
        for key in ('bets_published', 'bets_settled', 'wins', 'losses', 'pushes', 'voids'):
            bank[key] = int(bank.get(key) or 0)

    def bankroll_summary(self, settings: Any | None = None) -> dict[str, Any]:
        if settings is not None:
            self._sync_bankroll_defaults(settings)
        bank = dict(self._state.get('bankroll') or {})
        starting = float(bank.get('starting_balance') or 0.0)
        current = float(bank.get('current_balance') or 0.0)
        open_exposure = float(bank.get('open_exposure') or 0.0)
        total_staked = float(bank.get('total_staked') or 0.0)
        closed_pnl = float(bank.get('closed_pnl') or 0.0)
        roi_pct = (closed_pnl / starting * 100.0) if starting > 0 else 0.0
        yield_pct = (closed_pnl / total_staked * 100.0) if total_staked > 0 else 0.0
        bank['available_balance'] = round(max(0.0, current - open_exposure), 2)
        bank['roi_pct'] = round(roi_pct, 2)
        bank['yield_pct'] = round(yield_pct, 2)
        return bank

    def pending_bets(self) -> list[dict[str, Any]]:
        return [dict(item) for item in (self._state.get('bets') or []) if str(item.get('status') or '') == 'pending']

    def annotate_candidates_with_stakes(self, candidates: list[CandidateBet], settings: Any) -> list[CandidateBet]:
        self._sync_bankroll_defaults(settings)
        bank = self._state['bankroll']
        if not bank.get('enabled', True):
            return candidates
        current = float(bank.get('current_balance') or 0.0)
        open_exposure = float(bank.get('open_exposure') or 0.0)
        open_limit_pct = float(getattr(settings, 'bankroll_max_open_exposure_pct', 100.0) or 100.0)
        if open_limit_pct >= 100.0 or open_limit_pct <= 0.0:
            available_open = max(0.0, current - open_exposure)
        else:
            available_open = max(0.0, current * open_limit_pct / 100.0 - open_exposure)
        for candidate in candidates:
            pct = self._stake_pct(candidate, settings)
            stake = current * pct / 100.0
            min_amt = float(getattr(settings, 'bankroll_min_stake_amount', 10.0) or 10.0)
            stake = max(min_amt, stake) if stake > 0 else 0.0
            if available_open > 0:
                stake = min(stake, available_open)
            stake = self._round_stake(stake, float(getattr(settings, 'bankroll_round_to', 1.0) or 1.0))
            if stake < min_amt:
                stake = 0.0
                pct = 0.0
            else:
                pct = stake / current * 100.0 if current > 0 else 0.0
                available_open = max(0.0, available_open - stake)
            candidate.stake_amount = round(stake, 2)
            candidate.stake_pct = round(pct, 2)
            candidate.bankroll_snapshot = round(current, 2)
            candidate.bankroll_currency = str(bank.get('currency') or 'u')
            candidate.risk_label = 'high' if pct >= 5.0 else 'medium' if pct >= 3.0 else 'low'
        return candidates

    @staticmethod
    def _stake_pct(candidate: CandidateBet, settings: Any) -> float:
        min_pct = float(getattr(settings, 'bankroll_min_stake_pct', 1.0) or 1.0)
        max_pct = float(getattr(settings, 'bankroll_max_stake_pct', 6.0) or 6.0)
        flat_pct = float(getattr(settings, 'bankroll_flat_stake_pct', 3.0) or 3.0)
        if not getattr(settings, 'bankroll_kelly_enabled', True):
            return max(min_pct, min(max_pct, flat_pct))
        p = max(0.0, min(0.995, float(candidate.adjusted_probability or 0.0)))
        b = max(0.0, float(candidate.odds or 0.0) - 1.0)
        if p <= 0 or b <= 0:
            return max(min_pct, min(max_pct, flat_pct))
        q = 1.0 - p
        kelly = ((b * p) - q) / b
        if kelly <= 0:
            return max(min_pct, min(max_pct, flat_pct * 0.75))
        pct = kelly * 100.0 * float(getattr(settings, 'bankroll_kelly_fraction', 0.35) or 0.35)
        return max(min_pct, min(max_pct, pct))

    @staticmethod
    def _round_stake(value: float, step: float) -> float:
        if value <= 0:
            return 0.0
        if step <= 0:
            return round(value, 2)
        return round(round(value / step) * step, 2)

    def store_candidates(self, candidates: list[CandidateBet], telegram_sent: bool = False) -> int:
        bets = self._state.setdefault('bets', [])
        published = self._state.setdefault('published_candidates', [])
        existing = {item.get('fingerprint') for item in bets if isinstance(item, dict)}
        bank = self._state.setdefault('bankroll', self._default_state()['bankroll'])
        added = 0
        for candidate in candidates:
            row = self._serialize_candidate(candidate)
            fp = row['fingerprint']
            if fp in existing:
                continue
            row['published_at'] = datetime.now(UTC).isoformat()
            row['status'] = 'pending' if telegram_sent else 'generated'
            row['settlement'] = None
            row['telegram_sent'] = bool(telegram_sent)
            bets.append(row)
            published.append(row)
            existing.add(fp)
            added += 1
            if telegram_sent and float(row.get('stake_amount') or 0.0) > 0:
                bank['open_exposure'] = round(float(bank.get('open_exposure') or 0.0) + float(row['stake_amount']), 2)
                bank['total_staked'] = round(float(bank.get('total_staked') or 0.0) + float(row['stake_amount']), 2)
                bank['bets_published'] = int(bank.get('bets_published') or 0) + 1
        self._save()
        return added

    def apply_settlements(self, settlements: list[dict[str, Any]], settings: Any | None = None) -> dict[str, Any]:
        if settings is not None:
            self._sync_bankroll_defaults(settings)
        if not settlements:
            return {'settled_count': 0, 'items': [], 'bankroll': self.bankroll_summary(settings)}
        by_fp = {str(item.get('fingerprint')): item for item in settlements if item.get('fingerprint')}
        bank = self._state.setdefault('bankroll', self._default_state()['bankroll'])
        settled_items: list[dict[str, Any]] = []
        for bet in self._state.get('bets') or []:
            fp = str(bet.get('fingerprint') or '')
            settlement = by_fp.get(fp)
            if not settlement or str(bet.get('status') or '') != 'pending':
                continue
            bet['status'] = settlement['outcome']
            bet['settlement'] = settlement
            stake = float(bet.get('stake_amount') or 0.0)
            pnl = float(settlement.get('pnl') or 0.0)
            bank['current_balance'] = round(float(bank.get('current_balance') or 0.0) + pnl, 2)
            bank['peak_balance'] = max(float(bank.get('peak_balance') or 0.0), float(bank['current_balance']))
            bank['open_exposure'] = round(max(0.0, float(bank.get('open_exposure') or 0.0) - stake), 2)
            bank['closed_pnl'] = round(float(bank.get('closed_pnl') or 0.0) + pnl, 2)
            bank['bets_settled'] = int(bank.get('bets_settled') or 0) + 1
            outcome = str(settlement.get('outcome') or '')
            if outcome in {'won', 'half_won'}:
                bank['wins'] = int(bank.get('wins') or 0) + 1
            elif outcome in {'lost', 'half_lost'}:
                bank['losses'] = int(bank.get('losses') or 0) + 1
            elif outcome == 'push':
                bank['pushes'] = int(bank.get('pushes') or 0) + 1
            else:
                bank['voids'] = int(bank.get('voids') or 0) + 1
            settled_items.append(dict(bet))
        self._save()
        return {'settled_count': len(settled_items), 'items': settled_items, 'bankroll': self.bankroll_summary(settings)}

    def export_payloads(self, export_dir: str, matches: list[Match], candidates: list[CandidateBet]) -> dict[str, str]:
        root = Path(export_dir)
        dated = root / datetime.now(UTC).strftime('%Y-%m-%d')
        dated.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime('%H%M%S')
        match_rows = [self._serialize_match(item) for item in matches]
        pick_rows = [self._serialize_candidate(item) for item in candidates]
        pending = [item for item in (self._state.get('bets') or []) if str(item.get('status') or '') == 'pending']
        settled = [item for item in (self._state.get('bets') or []) if str(item.get('status') or '') not in {'pending', 'generated'}]
        bank = self.bankroll_summary()
        return {
            'matches_json': str(self._write_json(dated / f'{stamp}-matches.json', match_rows)),
            'picks_json': str(self._write_json(dated / f'{stamp}-picks.json', pick_rows)),
            'matches_csv': str(self._write_csv(dated / f'{stamp}-matches.csv', match_rows)),
            'picks_csv': str(self._write_csv(dated / f'{stamp}-picks.csv', pick_rows)),
            'bankroll_json': str(self._write_json(dated / f'{stamp}-bankroll.json', bank)),
            'pending_bets_json': str(self._write_json(dated / f'{stamp}-pending-bets.json', pending)),
            'settled_bets_json': str(self._write_json(dated / f'{stamp}-settled-bets.json', settled)),
            'latest_matches_json': str(self._write_json(root / 'latest-matches.json', match_rows)),
            'latest_picks_json': str(self._write_json(root / 'latest-picks.json', pick_rows)),
            'latest_matches_csv': str(self._write_csv(root / 'latest-matches.csv', match_rows)),
            'latest_picks_csv': str(self._write_csv(root / 'latest-picks.csv', pick_rows)),
            'latest_bankroll_json': str(self._write_json(root / 'latest-bankroll.json', bank)),
            'latest_pending_bets_json': str(self._write_json(root / 'latest-pending-bets.json', pending)),
            'latest_settled_bets_json': str(self._write_json(root / 'latest-settled-bets.json', settled)),
        }

    @staticmethod
    def _write_json(path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        headers: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: JsonStateStore._csv_value(row.get(key)) for key in headers})
        return path

    @staticmethod
    def _csv_value(value: Any) -> Any:
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

    @staticmethod
    def _serialize_match(match: Match) -> dict[str, Any]:
        return {
            'match_key': match.match_key,
            'source': match.source,
            'source_event_id': match.source_event_id,
            'sport_key': match.sport_key,
            'league_name': match.league_name,
            'home_team': match.home_team,
            'away_team': match.away_team,
            'commence_time': match.commence_time.isoformat(),
            'tier': match.tier,
            'metadata': match.metadata,
        }

    @staticmethod
    def _serialize_candidate(candidate: CandidateBet) -> dict[str, Any]:
        row = asdict(candidate)
        row['commence_time'] = candidate.commence_time.isoformat()
        row['fingerprint'] = JsonStateStore._fingerprint_from_candidate(candidate)
        return row

    @staticmethod
    def _fingerprint_from_candidate(candidate: CandidateBet) -> str:
        point = '' if candidate.point is None else f'{float(candidate.point):g}'
        selection_key = getattr(candidate, 'selection_key', '') or candidate_selection_key(
            str(candidate.family or ''),
            str(candidate.selection or ''),
            point=candidate.point,
            team_side=getattr(candidate, 'team_side', None),
            home_team=str(candidate.home_team or ''),
            away_team=str(candidate.away_team or ''),
        )
        team_side = str(getattr(candidate, 'team_side', '') or '').strip().lower()
        return '|'.join([
            str(candidate.match_key or ''),
            str(candidate.family or ''),
            str(selection_key or candidate.selection or ''),
            team_side,
            point,
            candidate.commence_time.astimezone(UTC).isoformat(),
        ])
