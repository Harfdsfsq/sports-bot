from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any

from app.schemas import CandidateBet, Match
from app.services.publication_lifecycle import is_sent_pick_row
from app.utils import candidate_selection_key, parse_datetime


def _json_default(value: Any) -> Any:
    """Safe JSON fallback for runtime/debug payloads.

    Provider/debug structures may contain datetime/date/timedelta/Path/set
    objects.  They should never crash the production run while writing debug
    artifacts.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value, key=lambda item: str(item))
    if hasattr(value, 'model_dump'):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, 'dict'):
        try:
            return value.dict()
        except Exception:
            pass
    if hasattr(value, '__dict__'):
        try:
            return dict(value.__dict__)
        except Exception:
            pass
    return str(value)


def legacy_run_logs_dir(state_path: str | Path) -> Path:
    return Path(state_path).parent / 'history' / 'runs'


def resolve_run_logs_dir(settings: Any | None = None, state_path: str | Path | None = None) -> Path:
    configured = str(getattr(settings, 'run_logs_dir', '') or '').strip() if settings is not None else ''
    if configured:
        return Path(configured)
    fallback_state_path = state_path if state_path is not None else getattr(settings, 'state_path', '.data/state.json')
    return legacy_run_logs_dir(fallback_state_path)


def resolve_run_history_roots(settings: Any | None = None, state_path: str | Path | None = None) -> list[Path]:
    fallback_state_path = state_path if state_path is not None else getattr(settings, 'state_path', '.data/state.json')
    primary_root = resolve_run_logs_dir(settings=settings, state_path=fallback_state_path)
    roots = [primary_root]
    legacy_root = legacy_run_logs_dir(fallback_state_path)
    if legacy_root != primary_root:
        roots.append(legacy_root)
    return roots


def collect_run_archive_paths(
    history_root: str | Path | Sequence[str | Path],
    *,
    newest_first: bool = False,
) -> list[Path]:
    if isinstance(history_root, (str, Path)):
        raw_roots: Sequence[str | Path] = [history_root]
    else:
        raw_roots = history_root

    roots: list[Path] = []
    seen_roots: set[str] = set()
    for raw_root in raw_roots:
        root = Path(raw_root)
        key = str(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        roots.append(root)

    archive_paths: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob('*/*-run.json'):
            if path.is_file():
                archive_paths.setdefault(str(path), path)

    return sorted(
        archive_paths.values(),
        key=lambda item: (item.parent.name, item.name, str(item)),
        reverse=newest_first,
    )


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
            'version': 4,
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
            'shadow_bets': [],
            'published_candidates': [],
            'daily_reports': {},
            'run_history': [],
            'message_history': [],
            'learning_state': {},
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
            state.update({
                k: v
                for k, v in payload.items()
                if k in {
                    'version',
                    'updated_at',
                    'last_run',
                    'bets',
                    'shadow_bets',
                    'published_candidates',
                    'bankroll',
                    'daily_reports',
                    'run_history',
                    'message_history',
                    'learning_state',
                }
            })
            if isinstance(payload.get('bankroll'), dict):
                state['bankroll'].update(payload['bankroll'])
            if not isinstance(state.get('daily_reports'), dict):
                state['daily_reports'] = {}
            if not isinstance(state.get('shadow_bets'), list):
                state['shadow_bets'] = []
            if not isinstance(state.get('run_history'), list):
                state['run_history'] = []
            if not isinstance(state.get('message_history'), list):
                state['message_history'] = []
            if not isinstance(state.get('learning_state'), dict):
                state['learning_state'] = {}
        return state

    def _save(self) -> None:
        self._state['updated_at'] = datetime.now(UTC).isoformat()
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')

    def save_run(self, status: str, summary: dict[str, Any] | None = None, error_text: str | None = None) -> None:
        self._state['last_run'] = {
            'status': status,
            'at': datetime.now(UTC).isoformat(),
            'summary': summary or {},
            'error': error_text,
        }
        self._save()

    def learning_state_snapshot(self) -> dict[str, Any]:
        snapshot = self._state.get('learning_state') or {}
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def write_debug(self, payload: dict[str, Any]) -> None:
        self.debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')

    def archive_run_payload(self, payload: dict[str, Any], settings: Any | None = None) -> dict[str, Any]:
        created_at = str(payload.get('created_at') or datetime.now(UTC).isoformat())
        try:
            created_dt = parse_datetime(created_at)
        except Exception:
            created_dt = datetime.now(UTC)
            created_at = created_dt.isoformat()
        history_root = resolve_run_logs_dir(settings=settings, state_path=self.state_path)
        dated_dir = history_root / created_dt.strftime('%Y-%m-%d')
        stamp = created_dt.strftime('%H%M%S')
        archive_path = dated_dir / f'{stamp}-run.json'
        archived_payload = dict(payload)
        archived_payload['created_at'] = created_at
        self._write_json(archive_path, archived_payload)

        summary = dict(archived_payload.get('summary') or {})
        forecast_rows = [dict(item) for item in (archived_payload.get('forecast_rows') or []) if isinstance(item, dict)]
        telegram_messages = [
            str(item).strip()
            for item in (archived_payload.get('telegram_messages') or [])
            if str(item).strip()
        ]
        compact_record = {
            'created_at': created_at,
            'path': str(archive_path),
            'status': str(
                summary.get('run_status')
                or summary.get('status')
                or ('error' if archived_payload.get('error') else 'ok')
            ),
            'matches_seen': int(summary.get('matches_seen') or 0),
            'contexts_built': int(summary.get('contexts_built') or 0),
            'candidates': int(summary.get('candidates') or 0),
            'published': int(summary.get('published') or 0),
            'telegram_messages_sent': int(summary.get('telegram_messages_sent') or 0),
            'forecast_rows': len(forecast_rows),
        }

        run_history = [dict(item) for item in (self._state.get('run_history') or []) if isinstance(item, dict)]
        run_history.append(compact_record)
        run_history = run_history[-60:]
        self._state['run_history'] = run_history

        message_history = [dict(item) for item in (self._state.get('message_history') or []) if isinstance(item, dict)]
        for index, message in enumerate(telegram_messages, start=1):
            message_history.append({
                'created_at': created_at,
                'run_path': str(archive_path),
                'message_index': index,
                'message': message,
            })
        self._state['message_history'] = message_history[-250:]

        quality_report = dict(archived_payload.get('quality_report') or {})
        daily_report = dict(archived_payload.get('daily_report') or {})
        existing_learning_state = dict(self._state.get('learning_state') or {})
        self._state['learning_state'] = {
            'updated_at': created_at,
            'summary': dict(quality_report.get('summary') or {}),
            'learning': dict(quality_report.get('learning') or {}),
            'error_analysis': dict(quality_report.get('error_analysis') or {}),
            'daily_quality_analysis': dict(
                daily_report.get('quality_analysis')
                or existing_learning_state.get('daily_quality_analysis')
                or {}
            ),
            'next_day_adjustments': dict(
                daily_report.get('next_day_adjustments')
                or existing_learning_state.get('next_day_adjustments')
                or {}
            ),
            'report_date': str(daily_report.get('report_date') or existing_learning_state.get('report_date') or ''),
            'archive_path': str(archive_path),
        }
        self._save()
        return {
            'run_archive_path': str(archive_path),
            'run_history_count': len(run_history),
            'message_history_count': len(self._state.get('message_history') or []),
        }

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

    def pending_bets(self, include_shadow: bool = False) -> list[dict[str, Any]]:
        rows = [dict(item) for item in (self._state.get('bets') or []) if str(item.get('status') or '') == 'pending']
        if include_shadow:
            rows.extend(
                dict(item)
                for item in (self._state.get('shadow_bets') or [])
                if str(item.get('status') or '') == 'shadow_pending'
            )
        return rows

    def record_settlement_attempts(self, probe: dict[str, Any]) -> int:
        attempts = [dict(item) for item in (probe.get('bets') or []) if isinstance(item, dict)]
        if not attempts:
            return 0
        attempted_at = str(probe.get('checked_at') or datetime.now(UTC).isoformat())
        by_id: dict[str, dict[str, Any]] = {}
        for item in attempts:
            compact = self._compact_settlement_attempt(item, attempted_at)
            for key in ('prediction_id', 'fingerprint'):
                value = str(item.get(key) or '').strip()
                if value:
                    by_id[value] = compact
        changed = 0
        for collection_name in ('bets', 'shadow_bets'):
            for bet in self._state.get(collection_name) or []:
                if not isinstance(bet, dict):
                    continue
                attempt = by_id.get(str(bet.get('prediction_id') or '').strip()) or by_id.get(str(bet.get('fingerprint') or '').strip())
                if not attempt:
                    continue
                bet['last_settlement_attempt'] = attempt
                changed += 1
        if changed:
            self._save()
        return changed

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

        if (
            candidates
            and bool(getattr(settings, 'bankroll_force_min_stake_when_empty_enabled', True))
            and not any(float(getattr(item, 'stake_amount', 0.0) or 0.0) > 0.0 for item in candidates)
        ):
            min_amt = float(getattr(settings, 'bankroll_min_stake_amount', 10.0) or 10.0)
            max_pct = max(0.0, float(getattr(settings, 'bankroll_force_min_stake_max_pct', 1.2) or 1.2))
            forced_cap = current * max_pct / 100.0 if max_pct > 0 else min_amt
            forced_stake = max(min_amt, forced_cap)
            forced_stake = self._round_stake(forced_stake, float(getattr(settings, 'bankroll_round_to', 1.0) or 1.0))
            if forced_stake >= min_amt and current > 0:
                best = max(
                    candidates,
                    key=lambda item: (
                        float(getattr(item, 'publication_score', 0.0) or 0.0),
                        float(getattr(item, 'confidence', 0.0) or 0.0),
                        float(getattr(item, 'ev_pct', 0.0) or 0.0),
                    ),
                )
                best.stake_amount = round(forced_stake, 2)
                best.stake_pct = round(forced_stake / current * 100.0, 2)
                best.risk_label = 'forced'
                best.source_summary = dict(getattr(best, 'source_summary', {}) or {})
                best.source_summary['forced_stake_when_empty'] = True
                best.reasons.append('bankroll=forced_min_stake_when_empty')
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
        shadow_bets = self._state.setdefault('shadow_bets', [])
        published = self._state.setdefault('published_candidates', [])
        existing = {item.get('fingerprint') for item in bets if isinstance(item, dict)}
        bank = self._state.setdefault('bankroll', self._default_state()['bankroll'])
        added = 0
        published_added = 0
        for candidate in candidates:
            row = self._serialize_candidate(candidate)
            fp = row['fingerprint']
            if fp in existing:
                continue
            if shadow_bets:
                shadow_bets[:] = [
                    item for item in shadow_bets
                    if not (isinstance(item, dict) and str(item.get('fingerprint') or '') == str(fp))
                ]
            row['published_at'] = datetime.now(UTC).isoformat()
            row['status'] = 'pending' if telegram_sent else 'generated'
            row['settlement'] = None
            row['telegram_sent'] = bool(telegram_sent)
            row['publication_lifecycle_status'] = 'telegram_sent' if telegram_sent else 'generated_not_sent'
            row['publication_lifecycle_stage'] = row['publication_lifecycle_status']
            source_summary = dict(row.get('source_summary') or {})
            source_summary['telegram_sent'] = bool(telegram_sent)
            source_summary['publication_lifecycle_status'] = row['publication_lifecycle_status']
            source_summary['publication_lifecycle_stage'] = row['publication_lifecycle_stage']
            row['source_summary'] = source_summary
            bets.append(row)
            if telegram_sent:
                published.append(row)
                published_added += 1
            existing.add(fp)
            added += 1
            if telegram_sent and float(row.get('stake_amount') or 0.0) > 0:
                bank['open_exposure'] = round(float(bank.get('open_exposure') or 0.0) + float(row['stake_amount']), 2)
                bank['total_staked'] = round(float(bank.get('total_staked') or 0.0) + float(row['stake_amount']), 2)
                bank['bets_published'] = int(bank.get('bets_published') or 0) + 1
        self._save()
        return published_added

    def store_shadow_candidates(self, candidates: list[CandidateBet], tracking_reason: str = 'shadow_tracking') -> int:
        shadow_bets = self._state.setdefault('shadow_bets', [])
        existing = {
            item.get('fingerprint')
            for item in ([*(self._state.get('bets') or []), *shadow_bets])
            if isinstance(item, dict)
        }
        added = 0
        published_added = 0
        for candidate in candidates:
            row = self._serialize_candidate(candidate)
            fp = row['fingerprint']
            if fp in existing:
                continue
            source_summary = dict(row.get('source_summary') or {})
            diagnostics = dict(row.get('diagnostics') or {})
            row['published_at'] = datetime.now(UTC).isoformat()
            row['status'] = 'shadow_pending'
            row['settlement'] = None
            row['telegram_sent'] = False
            row['stake_amount'] = 1.0
            row['stake_pct'] = 0.0
            row['bankroll_snapshot'] = 0.0
            row['risk_label'] = 'shadow'
            row['shadow_tracked'] = True
            row['tracking_mode'] = 'shadow'
            row['tracking_reason'] = str(tracking_reason or 'shadow_tracking')
            source_summary['tracking_mode'] = 'shadow'
            source_summary['tracking_reason'] = row['tracking_reason']
            row['source_summary'] = source_summary
            diagnostics['shadow_tracking'] = {
                'enabled': True,
                'tracking_reason': row['tracking_reason'],
                'tracked_at': row['published_at'],
            }
            row['diagnostics'] = diagnostics
            shadow_bets.append(row)
            existing.add(fp)
            added += 1
        if added:
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
        shadow_settled_count = 0
        for collection_name, pending_status in (('bets', 'pending'), ('shadow_bets', 'shadow_pending')):
            for bet in self._state.get(collection_name) or []:
                fp = str(bet.get('fingerprint') or '')
                settlement = by_fp.get(fp)
                if not settlement or str(bet.get('status') or '') != pending_status:
                    continue
                bet['status'] = settlement['outcome']
                bet['settlement'] = settlement
                if collection_name == 'bets':
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
                else:
                    shadow_settled_count += 1
        self._save()
        return {
            'settled_count': len(settled_items),
            'shadow_settled_count': shadow_settled_count,
            'items': settled_items,
            'bankroll': self.bankroll_summary(settings),
        }

    def daily_report_due(self, settings: Any, now_utc: datetime) -> tuple[bool, str, str | None]:
        offset_days = max(0, int(getattr(settings, 'daily_report_target_offset_days', 1) or 1))
        local_now = now_utc.astimezone(getattr(settings, 'tzinfo', UTC))
        report_date = (local_now.date() - timedelta(days=offset_days)).isoformat()
        if not bool(getattr(settings, 'daily_report_enabled', True)):
            return False, report_date, 'disabled'
        report_hour = min(23, max(0, int(getattr(settings, 'daily_report_hour_local', 8) or 8)))
        if local_now.hour < report_hour:
            return False, report_date, 'before_report_hour'
        reports = self._state.setdefault('daily_reports', {})
        report_state = reports.get(report_date) if isinstance(reports, dict) else None
        if isinstance(report_state, dict) and report_state.get('sent_at'):
            return False, report_date, 'already_sent'
        return True, report_date, None

    def mark_daily_report_sent(
        self,
        report_date: str,
        report: dict[str, Any] | None,
        *,
        telegram_sent: bool = False,
        skipped_reason: str | None = None,
    ) -> None:
        reports = self._state.setdefault('daily_reports', {})
        existing = reports.get(str(report_date)) if isinstance(reports, dict) else None
        previous_revision = 0
        if isinstance(existing, dict):
            try:
                previous_revision = int(existing.get('revision') or 0)
            except Exception:
                previous_revision = 0
            if previous_revision <= 0 and existing.get('sent_at'):
                previous_revision = 1
        reports[str(report_date)] = {
            'sent_at': datetime.now(UTC).isoformat(),
            'telegram_sent': bool(telegram_sent),
            'skipped_reason': skipped_reason,
            'report_hash': self._daily_report_hash(report),
            'revision': max(1, previous_revision + 1),
            'is_revision': previous_revision > 0,
            'summary': dict((report or {}).get('summary') or {}),
        }
        self._save()

    def build_daily_report(self, settings: Any, report_date: str) -> dict[str, Any]:
        raw_rows = [
            self._accounting_row_for_bet(item, settings)
            for item in self._tracked_bets()
            if self._local_date_for_bet(item, settings) == str(report_date)
        ]
        rows: list[dict[str, Any]] = []
        corrupted_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            reasons = self._accounting_row_corruption_reasons(row)
            if reasons:
                row = dict(row)
                row['corrupted_entry'] = True
                row['corruption_reasons'] = reasons
                corrupted_rows.append(row)
                continue
            rows.append(row)
        rows.sort(key=lambda item: (str(item.get('commence_time_local') or ''), str(item.get('home_team') or '')))
        corrupted_rows.sort(key=lambda item: (str(item.get('commence_time_local') or ''), str(item.get('home_team') or '')))
        summary = self._daily_summary(rows, settings, report_date)
        summary['raw_total_bets'] = len(raw_rows)
        summary['corrupted_bets'] = len(corrupted_rows)
        return {
            'created_at': datetime.now(UTC).isoformat(),
            'report_date': str(report_date),
            'timezone': str(getattr(settings, 'app_timezone', 'UTC') or 'UTC'),
            'summary': summary,
            'rows': rows,
            'corrupted_rows': corrupted_rows,
        }

    def daily_report_refresh_due(self, report_date: str, report: dict[str, Any] | None) -> tuple[bool, str | None]:
        reports = self._state.setdefault('daily_reports', {})
        report_state = reports.get(str(report_date)) if isinstance(reports, dict) else None
        if not isinstance(report_state, dict) or not report_state.get('sent_at'):
            return False, 'not_sent'
        current_hash = self._daily_report_hash(report)
        previous_hash = str(report_state.get('report_hash') or '')
        if previous_hash:
            if current_hash == previous_hash:
                return False, 'unchanged'
            return True, 'report_changed'
        previous_summary = dict(report_state.get('summary') or {})
        current_summary = dict((report or {}).get('summary') or {})
        keys = {
            'total_bets',
            'settled_bets',
            'pending_bets',
            'won',
            'lost',
            'push',
            'void',
            'stake_total',
            'settled_stake',
            'pending_stake',
            'revenue',
            'pnl',
            'roi_pct',
            'hit_rate_pct',
        }
        if any(previous_summary.get(key) != current_summary.get(key) for key in keys):
            return True, 'legacy_summary_changed'
        return False, 'legacy_hash_missing'

    def prediction_ledger(self, settings: Any | None = None, include_shadow: bool = False) -> list[dict[str, Any]]:
        rows = [self._accounting_row_for_bet(item, settings) for item in self._tracked_bets(include_shadow=include_shadow)]
        rows.sort(key=lambda item: (str(item.get('commence_time_utc') or ''), str(item.get('home_team') or '')))
        return rows

    def export_daily_report(self, export_dir: str, report: dict[str, Any]) -> dict[str, str]:
        root = Path(export_dir)
        report_date = str(report.get('report_date') or datetime.now(UTC).date().isoformat())
        dated = root / report_date
        rows = [dict(item) for item in (report.get('rows') or []) if isinstance(item, dict)]
        summary = dict(report.get('summary') or {})
        return {
            'daily_report_json': str(self._write_json(dated / 'daily-report.json', report)),
            'daily_report_csv': str(self._write_csv(dated / 'daily-report.csv', rows)),
            'daily_summary_json': str(self._write_json(dated / 'daily-summary.json', summary)),
            'daily_summary_csv': str(self._write_csv(dated / 'daily-summary.csv', [summary])),
            'latest_daily_report_json': str(self._write_json(root / 'latest-daily-report.json', report)),
            'latest_daily_report_csv': str(self._write_csv(root / 'latest-daily-report.csv', rows)),
            'latest_daily_summary_json': str(self._write_json(root / 'latest-daily-summary.json', summary)),
            'latest_daily_summary_csv': str(self._write_csv(root / 'latest-daily-summary.csv', [summary])),
        }

    @staticmethod
    def _daily_report_hash(report: dict[str, Any] | None) -> str:
        payload = report or {}
        rows = [dict(item) for item in (payload.get('rows') or []) if isinstance(item, dict)]
        compact_rows = []
        for row in rows:
            compact_rows.append({
                'fingerprint': row.get('fingerprint') or '',
                'status': row.get('status') or '',
                'result': row.get('result') or '',
                'is_hit': row.get('is_hit') if row.get('is_hit') in {True, False} else '',
                'pnl': row.get('pnl') if row.get('pnl') not in (None, '') else '',
                'roi_pct': row.get('roi_pct') if row.get('roi_pct') not in (None, '') else '',
                'final_score': row.get('final_score') or '',
                'settlement_source': row.get('settlement_source') or '',
                'settlement_note': row.get('settlement_note') or '',
            })
        signature = {
            'report_date': payload.get('report_date') or dict(payload.get('summary') or {}).get('report_date') or '',
            'summary': dict(payload.get('summary') or {}),
            'rows': sorted(compact_rows, key=lambda item: str(item.get('fingerprint') or '')),
        }
        raw = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=_json_default)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @staticmethod
    def _compact_settlement_attempt(item: dict[str, Any], attempted_at: str) -> dict[str, Any]:
        top_candidates = item.get('top_candidates')
        if isinstance(top_candidates, list):
            top_candidates = top_candidates[:3]
        else:
            top_candidates = []
        return {
            'attempted_at': attempted_at,
            'reason': item.get('reason') or '',
            'grade_issue': item.get('grade_issue') or '',
            'match_failure': item.get('match_failure') or '',
            'match_threshold': item.get('match_threshold') or '',
            'best_score': item.get('best_score') or '',
            'best_source': item.get('best_source') or '',
            'best_status': item.get('best_status') or '',
            'best_event': item.get('best_event') or '',
            'best_scoreline': item.get('best_scoreline') or '',
            'result_orientation': item.get('result_orientation') or '',
            'matched_via': item.get('matched_via') or '',
            'manual_override_match_mode': item.get('manual_override_match_mode') or '',
            'manual_override_note': item.get('manual_override_note') or '',
            'top_candidates': top_candidates,
        }

    def _tracked_bets(self, include_shadow: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._state.get('bets') or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get('status') or '')
            if status == 'generated' and not bool(item.get('telegram_sent')):
                continue
            rows.append(dict(item))
        if include_shadow:
            for item in self._state.get('shadow_bets') or []:
                if not isinstance(item, dict):
                    continue
                status = str(item.get('status') or '')
                if status == 'generated' and not bool(item.get('telegram_sent')):
                    continue
                rows.append(dict(item))
        return rows

    def _local_date_for_bet(self, bet: dict[str, Any], settings: Any) -> str | None:
        try:
            tzinfo = getattr(settings, 'tzinfo', UTC)
            return parse_datetime(str(bet.get('commence_time') or '')).astimezone(tzinfo).date().isoformat()
        except Exception:
            return None

    @staticmethod
    def _payload_chain(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [payload]
        for key in ('bet', 'candidate', 'match', 'payload'):
            value = payload.get(key)
            if isinstance(value, dict):
                rows.append(value)
        return rows

    @staticmethod
    def _first_text(payloads: list[dict[str, Any]], *keys: str) -> str:
        for payload in payloads:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if value not in (None, '') and not isinstance(value, (dict, list)):
                    text = str(value).strip()
                    if text:
                        return text
        return ''

    @staticmethod
    def _split_match_name(value: str) -> tuple[str, str]:
        text = ' '.join(str(value or '').strip().split())
        if not text:
            return '', ''
        separators = [f' {chr(8212)} ', f' {chr(8211)} ', ' vs ', ' v ', ' - ']
        low = text.lower()
        for sep in separators:
            marker = sep.lower()
            if marker not in low:
                continue
            index = low.find(marker)
            home = text[:index].strip()
            away = text[index + len(sep):].strip()
            if home and away:
                return home, away
        return '', ''

    def _bet_team_pair(self, bet: dict[str, Any]) -> tuple[str, str]:
        payloads = self._payload_chain(bet)
        home = self._first_text(payloads, 'home_team', 'home', 'team_home', 'home_name')
        away = self._first_text(payloads, 'away_team', 'away', 'team_away', 'away_name')
        if home and away:
            return home, away
        match_name = self._first_text(payloads, 'match_name', 'event_name', 'fixture_name', 'name', 'match')
        parsed_home, parsed_away = self._split_match_name(match_name)
        return home or parsed_home, away or parsed_away

    def _bet_odds_value(self, bet: dict[str, Any]) -> Any:
        payloads = self._payload_chain(bet)
        for payload in payloads:
            for key in ('odds', 'selected_odds', 'price', 'decimal_odds', 'odd', 'value'):
                value = payload.get(key)
                if value not in (None, ''):
                    return value
        return ''

    def _accounting_row_corruption_reasons(self, row: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if not str(row.get('home_team') or '').strip() or not str(row.get('away_team') or '').strip():
            reasons.append('missing_teams')
        if self._safe_float(row.get('odds')) <= 1.0:
            reasons.append('invalid_odds')
        return reasons

    def _accounting_row_for_bet(self, bet: dict[str, Any], settings: Any | None = None) -> dict[str, Any]:
        settlement = dict(bet.get('settlement') or {})
        settlement_attempt = dict(bet.get('last_settlement_attempt') or {})
        source_summary = dict(bet.get('source_summary') or {})
        status = str(bet.get('status') or 'pending')
        outcome = str(settlement.get('outcome') or status)
        stake = self._safe_float(bet.get('stake_amount'))
        pnl = self._settled_pnl(status, settlement)
        roi_pct = round((pnl / stake * 100.0), 2) if pnl is not None and stake > 0 else ''
        commence_utc = ''
        commence_local = ''
        report_date = ''
        published_at_local = ''
        published_date_local = ''
        try:
            parsed = parse_datetime(str(bet.get('commence_time') or ''))
            commence_utc = parsed.astimezone(UTC).isoformat()
            if settings is not None:
                local = parsed.astimezone(getattr(settings, 'tzinfo', UTC))
                commence_local = local.isoformat()
                report_date = local.date().isoformat()
        except Exception:
            commence_utc = str(bet.get('commence_time') or '')
        try:
            published = parse_datetime(str(bet.get('published_at') or ''))
            if settings is not None:
                published_local = published.astimezone(getattr(settings, 'tzinfo', UTC))
                published_at_local = published_local.isoformat()
                published_date_local = published_local.date().isoformat()
        except Exception:
            pass

        final_home = settlement.get('final_home_goals')
        final_away = settlement.get('final_away_goals')
        final_score = ''
        if final_home not in (None, '') and final_away not in (None, ''):
            try:
                final_score = f"{int(float(final_home))}:{int(float(final_away))}"
            except Exception:
                final_score = f"{final_home}:{final_away}"

        family = str(bet.get('family') or '')
        selection = str(bet.get('selection') or '')
        point = bet.get('point')
        home_team, away_team = self._bet_team_pair(bet)
        odds_value = self._bet_odds_value(bet)
        selection_key = str(
            bet.get('selection_key')
            or candidate_selection_key(
                family,
                selection,
                point=point,
                team_side=bet.get('team_side'),
                home_team=home_team,
                away_team=away_team,
            )
        )

        return {
            'report_date': report_date,
            'prediction_id': bet.get('prediction_id') or bet.get('fingerprint') or '',
            'fingerprint': bet.get('fingerprint') or '',
            'published_at': bet.get('published_at') or '',
            'published_at_local': published_at_local,
            'published_date_local': published_date_local,
            'telegram_sent': bool(bet.get('telegram_sent')),
            'tracking_mode': bet.get('tracking_mode') or '',
            'tracking_reason': bet.get('tracking_reason') or '',
            'shadow_tracked': bool(bet.get('shadow_tracked')),
            'match_key': bet.get('match_key') or '',
            'sport_key': bet.get('sport_key') or '',
            'league_name': bet.get('league_name') or '',
            'home_team': home_team,
            'away_team': away_team,
            'match_name': bet.get('match_name') or bet.get('event_name') or '',
            'commence_time_utc': commence_utc,
            'commence_time_local': commence_local,
            'family': family,
            'selection': selection,
            'selection_key': selection_key,
            'team_side': bet.get('team_side') or '',
            'point': '' if point in (None, '') else point,
            'odds': self._round_value(odds_value, 3),
            'stake_amount': round(stake, 2),
            'status': status,
            'result': self._result_label(status, outcome),
            'is_hit': self._is_hit_value(status, outcome),
            'pnl': '' if pnl is None else round(pnl, 2),
            'roi_pct': roi_pct,
            'final_score': final_score,
            'final_home_goals': '' if final_home in (None, '') else final_home,
            'final_away_goals': '' if final_away in (None, '') else final_away,
            'settled_at': settlement.get('settled_at') or '',
            'settlement_source': settlement.get('source') or '',
            'settlement_note': settlement.get('note') or '',
            'settlement_result_orientation': settlement.get('result_orientation') or settlement_attempt.get('result_orientation') or '',
            'settlement_attempt_at': settlement_attempt.get('attempted_at') or '',
            'settlement_attempt_reason': settlement_attempt.get('reason') or '',
            'settlement_grade_issue': settlement_attempt.get('grade_issue') or '',
            'settlement_match_failure': settlement_attempt.get('match_failure') or '',
            'settlement_match_threshold': settlement_attempt.get('match_threshold') or '',
            'settlement_best_score': settlement_attempt.get('best_score') or '',
            'settlement_best_source': settlement_attempt.get('best_source') or '',
            'settlement_best_status': settlement_attempt.get('best_status') or '',
            'settlement_best_event': settlement_attempt.get('best_event') or '',
            'settlement_best_scoreline': settlement_attempt.get('best_scoreline') or '',
            'settlement_matched_via': settlement_attempt.get('matched_via') or '',
            'settlement_manual_note': settlement_attempt.get('manual_override_note') or '',
            'model_probability_pct': self._pct_value(bet.get('model_probability')),
            'adjusted_probability_pct': self._pct_value(bet.get('adjusted_probability')),
            'market_probability_pct': self._pct_value(bet.get('market_probability')),
            'edge_pct': self._round_value(bet.get('edge_pct'), 3),
            'ev_pct': self._round_value(bet.get('ev_pct'), 3),
            'confidence': self._round_value(bet.get('confidence'), 2),
            'books_count': bet.get('books_count') or '',
            'sources_count': bet.get('sources_count') or '',
            'model_mode': bet.get('model_mode') or '',
            'publication_score': self._round_value(bet.get('publication_score'), 3),
            'expected_home': self._round_value(bet.get('expected_home'), 3),
            'expected_away': self._round_value(bet.get('expected_away'), 3),
            'total_xg': self._round_value(
                self._safe_float(bet.get('expected_home')) + self._safe_float(bet.get('expected_away')),
                3,
            ) if bet.get('expected_home') not in (None, '') and bet.get('expected_away') not in (None, '') else '',
            'selected_bookmaker': source_summary.get('selected_bookmaker') or '',
            'selected_source': source_summary.get('selected_source') or '',
            'context_source': source_summary.get('context_source') or '',
            'context_sources': source_summary.get('context_sources') or '',
            'context_confidence': self._round_value(source_summary.get('context_confidence'), 2),
            'context_mode': source_summary.get('context_mode') or '',
            'market_movement': source_summary.get('market_movement') or '',
            'best_vs_consensus_edge_pct': self._round_value(source_summary.get('best_vs_consensus_edge_pct'), 3),
            'consensus_dispersion_pct': self._round_value(source_summary.get('consensus_dispersion_pct'), 3),
            'match_tier': source_summary.get('match_tier') or '',
            'quality_status': source_summary.get('quality_status') or '',
            'quality_score': self._round_value(source_summary.get('quality_score'), 3),
            'quality_reasons': source_summary.get('quality_reasons') or '',
        }

    def _daily_summary(self, rows: list[dict[str, Any]], settings: Any, report_date: str) -> dict[str, Any]:
        settled_rows = [row for row in rows if str(row.get('status') or '') in {'won', 'lost', 'half_won', 'half_lost', 'push', 'void'}]
        pending_rows = [row for row in rows if str(row.get('status') or '') == 'pending']
        win_rows = [row for row in rows if str(row.get('status') or '') in {'won', 'half_won'}]
        loss_rows = [row for row in rows if str(row.get('status') or '') in {'lost', 'half_lost'}]
        push_rows = [row for row in rows if str(row.get('status') or '') == 'push']
        void_rows = [row for row in rows if str(row.get('status') or '') == 'void']
        stake_total = sum(self._safe_float(row.get('stake_amount')) for row in rows)
        settled_stake = sum(self._safe_float(row.get('stake_amount')) for row in settled_rows)
        pending_stake = sum(self._safe_float(row.get('stake_amount')) for row in pending_rows)
        pnl = sum(self._safe_float(row.get('pnl')) for row in settled_rows if row.get('pnl') not in (None, ''))
        roi_pct = (pnl / settled_stake * 100.0) if settled_stake > 0 else 0.0
        hit_denominator = len(win_rows) + len(loss_rows)
        hit_rate_pct = (len(win_rows) / hit_denominator * 100.0) if hit_denominator else 0.0
        return {
            'report_date': str(report_date),
            'timezone': str(getattr(settings, 'app_timezone', 'UTC') or 'UTC'),
            'currency': str(getattr(settings, 'bankroll_currency', 'units') or 'units'),
            'total_bets': len(rows),
            'settled_bets': len(settled_rows),
            'pending_bets': len(pending_rows),
            'won': len(win_rows),
            'lost': len(loss_rows),
            'push': len(push_rows),
            'void': len(void_rows),
            'stake_total': round(stake_total, 2),
            'settled_stake': round(settled_stake, 2),
            'pending_stake': round(pending_stake, 2),
            'revenue': round(pnl, 2),
            'pnl': round(pnl, 2),
            'roi_pct': round(roi_pct, 2),
            'hit_rate_pct': round(hit_rate_pct, 2),
        }

    @staticmethod
    def _settled_pnl(status: str, settlement: dict[str, Any]) -> float | None:
        if status not in {'won', 'lost', 'half_won', 'half_lost', 'push', 'void'}:
            return None
        return JsonStateStore._safe_float(settlement.get('pnl'))

    @staticmethod
    def _result_label(status: str, outcome: str) -> str:
        value = str(outcome or status or '').strip().lower()
        if value in {'won', 'half_won'}:
            return 'won'
        if value in {'lost', 'half_lost'}:
            return 'lost'
        if value == 'push':
            return 'push'
        if value == 'void':
            return 'void'
        if value in {'pending', 'shadow_pending'}:
            return 'pending'
        return value or 'unknown'

    @staticmethod
    def _is_hit_value(status: str, outcome: str) -> Any:
        value = str(outcome or status or '').strip().lower()
        if value in {'won', 'half_won'}:
            return True
        if value in {'lost', 'half_lost'}:
            return False
        return ''

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            if value in (None, ''):
                return 0.0
            return float(value)
        except Exception:
            return 0.0

    def export_payloads(
        self,
        export_dir: str,
        matches: list[Match],
        candidates: list[CandidateBet],
        forecast_rows: list[dict[str, Any]] | None = None,
        *,
        match_serving_rows: list[dict[str, Any]] | None = None,
        context_observation_rows: list[dict[str, Any]] | None = None,
        line_snapshot_rows: list[dict[str, Any]] | None = None,
        consensus_line_rows: list[dict[str, Any]] | None = None,
        settings: Any | None = None,
        **extra_rows: Any,
    ) -> dict[str, str]:
        """Export latest runtime payloads without breaking on new runner datasets.

        The runner can evolve faster than the state exporter.  Extra keyword
        datasets such as match_serving_rows or line_snapshot_rows are useful
        diagnostics, but missing exporter support must never crash run-once.
        Known datasets are exported as separate latest/date-stamped files;
        unknown list/dict datasets are written generically for diagnostics.
        """
        root = Path(export_dir)
        dated = root / datetime.now(UTC).strftime('%Y-%m-%d')
        dated.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime('%H%M%S')
        forecasts_by_match = self._forecast_rows_by_match(forecast_rows or [])
        match_rows = [self._serialize_match(item, forecasts_by_match.get(item.match_key)) for item in matches]
        pick_rows = [self._serialize_candidate(item) for item in candidates]
        bet_rows = self.prediction_ledger(settings)
        pending = [item for item in (self._state.get('bets') or []) if str(item.get('status') or '') == 'pending']
        settled = [item for item in (self._state.get('bets') or []) if str(item.get('status') or '') not in {'pending', 'generated'}]
        bank = self.bankroll_summary(settings)

        exports: dict[str, str] = {
            'matches_json': str(self._write_json(dated / f'{stamp}-matches.json', match_rows)),
            'picks_json': str(self._write_json(dated / f'{stamp}-picks.json', pick_rows)),
            'bets_json': str(self._write_json(dated / f'{stamp}-bets.json', bet_rows)),
            'matches_csv': str(self._write_csv(dated / f'{stamp}-matches.csv', match_rows)),
            'picks_csv': str(self._write_csv(dated / f'{stamp}-picks.csv', pick_rows)),
            'bets_csv': str(self._write_csv(dated / f'{stamp}-bets.csv', bet_rows)),
            'bankroll_json': str(self._write_json(dated / f'{stamp}-bankroll.json', bank)),
            'pending_bets_json': str(self._write_json(dated / f'{stamp}-pending-bets.json', pending)),
            'settled_bets_json': str(self._write_json(dated / f'{stamp}-settled-bets.json', settled)),
            'latest_matches_json': str(self._write_json(root / 'latest-matches.json', match_rows)),
            'latest_picks_json': str(self._write_json(root / 'latest-picks.json', pick_rows)),
            'latest_bets_json': str(self._write_json(root / 'latest-bets.json', bet_rows)),
            'latest_matches_csv': str(self._write_csv(root / 'latest-matches.csv', match_rows)),
            'latest_picks_csv': str(self._write_csv(root / 'latest-picks.csv', pick_rows)),
            'latest_bets_csv': str(self._write_csv(root / 'latest-bets.csv', bet_rows)),
            'latest_bankroll_json': str(self._write_json(root / 'latest-bankroll.json', bank)),
            'latest_pending_bets_json': str(self._write_json(root / 'latest-pending-bets.json', pending)),
            'latest_settled_bets_json': str(self._write_json(root / 'latest-settled-bets.json', settled)),
        }

        optional_datasets: dict[str, Any] = {
            'match_serving': match_serving_rows or [],
            'context_observations': context_observation_rows or [],
            'line_snapshots': line_snapshot_rows or [],
            'consensus_lines': consensus_line_rows or [],
        }
        for raw_name, value in extra_rows.items():
            if value in (None, ''):
                continue
            if isinstance(value, (list, tuple)):
                optional_datasets[raw_name] = list(value)
            elif isinstance(value, dict):
                optional_datasets[raw_name] = value

        for name, rows in optional_datasets.items():
            if rows in (None, ''):
                continue
            slug = str(name).strip().replace('_', '-')
            exports[f'{name}_json'] = str(self._write_json(dated / f'{stamp}-{slug}.json', rows))
            exports[f'latest_{name}_json'] = str(self._write_json(root / f'latest-{slug}.json', rows))
            if isinstance(rows, list) and all(isinstance(item, dict) for item in rows):
                exports[f'{name}_csv'] = str(self._write_csv(dated / f'{stamp}-{slug}.csv', rows))
                exports[f'latest_{name}_csv'] = str(self._write_csv(root / f'latest-{slug}.csv', rows))

        return exports

    @staticmethod
    def _write_json(path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding='utf-8')
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
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)
        return value

    @staticmethod
    def _forecast_rows_by_match(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        ranked: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            match_key = str(row.get('match_key') or '')
            if not match_key:
                continue
            existing = ranked.get(match_key)
            if existing is None or JsonStateStore._forecast_rank(row) > JsonStateStore._forecast_rank(existing):
                ranked[match_key] = row
        return ranked

    @staticmethod
    def _forecast_rank(row: dict[str, Any]) -> tuple[int, float, float]:
        status = str(row.get('forecast_status') or row.get('model_filter_status') or '')
        priority = {
            'published': 5,
            'publishable_dry_run': 4,
            'publishable': 4,
            'passed': 3,
            'reused_already_in_state': 3,
            'zero_stake': 2,
            'rejected_by_model_filters': 1,
        }.get(status, 0)
        try:
            score = float(row.get('publication_score') or 0.0)
        except Exception:
            score = 0.0
        try:
            ev = float(row.get('ev_pct') or 0.0)
        except Exception:
            ev = 0.0
        return (priority, score, ev)

    @staticmethod
    def _serialize_match(match: Match, forecast: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
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
        row.update(JsonStateStore._forecast_columns(forecast))
        return row

    @staticmethod
    def _forecast_columns(forecast: dict[str, Any] | None) -> dict[str, Any]:
        keys = {
            'forecast_status': '',
            'forecast_family': '',
            'forecast_selection': '',
            'forecast_selection_key': '',
            'forecast_team_side': '',
            'forecast_line': '',
            'forecast_odds': '',
            'forecast_bookmaker': '',
            'forecast_odds_source': '',
            'forecast_model_probability_pct': '',
            'forecast_adjusted_probability_pct': '',
            'forecast_market_probability_pct': '',
            'forecast_edge_pct': '',
            'forecast_ev_pct': '',
            'forecast_confidence': '',
            'forecast_books_count': '',
            'forecast_sources_count': '',
            'forecast_model_mode': '',
            'forecast_publication_score': '',
            'forecast_expected_home': '',
            'forecast_expected_away': '',
            'forecast_total_xg': '',
            'forecast_context_source': '',
            'forecast_context_confidence': '',
            'forecast_market_movement': '',
            'forecast_quality_status': '',
            'forecast_quality_score': '',
            'forecast_quality_reasons': '',
            'forecast_quality_calibration': '',
            'forecast_reasons': '',
            'forecast_analysis_points': '',
        }
        if not forecast:
            return keys
        keys.update({
            'forecast_status': forecast.get('forecast_status') or forecast.get('model_filter_status') or '',
            'forecast_family': forecast.get('family') or '',
            'forecast_selection': forecast.get('selection') or '',
            'forecast_selection_key': forecast.get('selection_key') or '',
            'forecast_team_side': forecast.get('team_side') or '',
            'forecast_line': forecast.get('point') if forecast.get('point') is not None else '',
            'forecast_odds': JsonStateStore._round_value(forecast.get('odds'), 3),
            'forecast_bookmaker': forecast.get('selected_bookmaker') or '',
            'forecast_odds_source': forecast.get('selected_source') or '',
            'forecast_model_probability_pct': JsonStateStore._pct_value(forecast.get('model_probability')),
            'forecast_adjusted_probability_pct': JsonStateStore._pct_value(forecast.get('adjusted_probability')),
            'forecast_market_probability_pct': JsonStateStore._pct_value(forecast.get('market_probability')),
            'forecast_edge_pct': JsonStateStore._round_value(forecast.get('edge_pct'), 3),
            'forecast_ev_pct': JsonStateStore._round_value(forecast.get('ev_pct'), 3),
            'forecast_confidence': JsonStateStore._round_value(forecast.get('confidence'), 2),
            'forecast_books_count': forecast.get('books_count') or '',
            'forecast_sources_count': forecast.get('sources_count') or '',
            'forecast_model_mode': forecast.get('model_mode') or '',
            'forecast_publication_score': JsonStateStore._round_value(forecast.get('publication_score'), 3),
            'forecast_expected_home': JsonStateStore._round_value(forecast.get('expected_home'), 3),
            'forecast_expected_away': JsonStateStore._round_value(forecast.get('expected_away'), 3),
            'forecast_total_xg': JsonStateStore._round_value(forecast.get('total_xg'), 3),
            'forecast_context_source': forecast.get('context_source') or '',
            'forecast_context_confidence': JsonStateStore._round_value(forecast.get('context_confidence'), 2),
            'forecast_market_movement': forecast.get('market_movement') or '',
            'forecast_quality_status': forecast.get('quality_status') or '',
            'forecast_quality_score': JsonStateStore._round_value(forecast.get('quality_score'), 3),
            'forecast_quality_reasons': forecast.get('quality_reasons') or '',
            'forecast_quality_calibration': forecast.get('quality_calibration') or '',
            'forecast_reasons': forecast.get('reasons') or '',
            'forecast_analysis_points': forecast.get('analysis_points') or '',
        })
        return keys

    @staticmethod
    def _round_value(value: Any, digits: int) -> Any:
        try:
            if value is None or value == '':
                return ''
            return round(float(value), digits)
        except Exception:
            return value or ''

    @staticmethod
    def _pct_value(value: Any) -> Any:
        try:
            if value is None or value == '':
                return ''
            number = float(value)
            if number <= 1.0:
                number *= 100.0
            return round(number, 2)
        except Exception:
            return value or ''

    @staticmethod
    def _serialize_candidate(candidate: CandidateBet) -> dict[str, Any]:
        row = asdict(candidate)
        row['commence_time'] = candidate.commence_time.isoformat()
        row['fingerprint'] = JsonStateStore._fingerprint_from_candidate(candidate)
        row['prediction_id'] = row['fingerprint']
        source_summary = dict(row.get('source_summary') or {})
        diagnostics = dict(row.get('diagnostics') or {})
        lifecycle = str(
            source_summary.get('publication_lifecycle_status')
            or source_summary.get('publication_lifecycle_stage')
            or (diagnostics.get('publication_lifecycle') or {}).get('status')
            or 'generated_not_sent'
        )
        row['publication_lifecycle_status'] = lifecycle
        row['publication_lifecycle_stage'] = lifecycle
        row['telegram_sent'] = is_sent_pick_row(row)
        row['status'] = 'pending' if row['telegram_sent'] else 'generated'
        source_summary['telegram_sent'] = bool(row['telegram_sent'])
        source_summary['publication_lifecycle_status'] = lifecycle
        source_summary['publication_lifecycle_stage'] = lifecycle
        row['source_summary'] = source_summary
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
