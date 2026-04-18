from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, Offer
from app.utils import candidate_selection_key


class MarketMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_dir = Path(settings.storage_export_dir) / settings.market_monitor_subdir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.latest_snapshot_path = self.base_dir / 'latest-market-snapshot.json'
        self.pending_clv_path = self.base_dir / 'pending-clv.json'
        self.clv_results_path = self.base_dir / 'clv-results.json'

    def build_signals(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        now_utc: datetime,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
        previous = self._load_json(self.latest_snapshot_path, default={})
        current_snapshot = self._build_snapshot(matches, offers_by_match, now_utc)
        signals = self._build_signal_map(current_snapshot, previous)
        self._write_snapshot(current_snapshot)
        clv_summary = self._update_pending_clv(now_utc, current_snapshot)
        stats = {
            'enabled': True,
            'snapshots_written': 1,
            'matches_snapshotted': len(current_snapshot.get('matches', {})),
            'markets_snapshotted': sum(len(rows or {}) for rows in current_snapshot.get('matches', {}).values()),
            'signals_built': sum(len(rows or {}) for rows in signals.values()),
            'clv_pending': clv_summary.get('pending_count', 0),
            'clv_resolved': clv_summary.get('resolved_count', 0),
            'avg_open_clv_pct': clv_summary.get('avg_open_clv_pct'),
            'avg_resolved_clv_pct': clv_summary.get('avg_resolved_clv_pct'),
        }
        preview = {
            'sample_signals': self._sample_signals(signals, limit=12),
            'clv_summary': clv_summary,
        }
        return signals, stats, preview

    def record_published_candidates(self, candidates: list[CandidateBet], now_utc: datetime) -> dict[str, Any]:
        if not candidates:
            return {'tracked': 0}
        payload = self._load_json(self.pending_clv_path, default=[])
        rows = payload if isinstance(payload, list) else []
        seen = {str(item.get('fingerprint')) for item in rows if isinstance(item, dict)}
        added = 0
        for candidate in candidates:
            summary = dict(getattr(candidate, 'source_summary', {}) or {})
            signal_key = summary.get('market_signal_key')
            if not signal_key:
                continue
            fingerprint = self._candidate_fingerprint(candidate)
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            rows.append(
                {
                    'fingerprint': fingerprint,
                    'tracked_at': now_utc.isoformat(),
                    'match_key': candidate.match_key,
                    'family': candidate.family,
                    'selection': candidate.selection,
                    'selection_key': getattr(candidate, 'selection_key', ''),
                    'team_side': getattr(candidate, 'team_side', None),
                    'point': candidate.point,
                    'commence_time': candidate.commence_time.isoformat(),
                    'entry_odds': candidate.odds,
                    'entry_fair_odds': candidate.fair_odds,
                    'entry_market_probability': candidate.market_probability,
                    'entry_adjusted_probability': candidate.adjusted_probability,
                    'market_signal_key': signal_key,
                    'best_vs_consensus_edge_pct': summary.get('best_vs_consensus_edge_pct'),
                    'context_source': summary.get('context_source'),
                }
            )
            added += 1
        self._write_json(self.pending_clv_path, rows)
        return {'tracked': added}

    def resolved_clv_rows(self) -> list[dict[str, Any]]:
        payload = self._load_json(self.clv_results_path, default=[])
        return [dict(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _update_pending_clv(self, now_utc: datetime, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = self._load_json(self.pending_clv_path, default=[])
        rows = payload if isinstance(payload, list) else []
        resolved_payload = self._load_json(self.clv_results_path, default=[])
        resolved_rows = resolved_payload if isinstance(resolved_payload, list) else []

        unresolved: list[dict[str, Any]] = []
        open_clv_values: list[float] = []
        resolved_values: list[float] = []
        newly_resolved = 0

        snapshot_matches = snapshot.get('matches', {}) if isinstance(snapshot, dict) else {}
        grace = timedelta(minutes=max(int(getattr(self.settings, 'clv_resolve_grace_minutes', 45) or 45), 0))

        for row in rows:
            if not isinstance(row, dict):
                continue
            match_key = str(row.get('match_key') or '')
            signal_key = str(row.get('market_signal_key') or '')
            market_row = ((snapshot_matches.get(match_key) or {}) if isinstance(snapshot_matches, dict) else {}).get(signal_key)
            entry_odds = self._to_float(row.get('entry_odds'))
            current_best = self._to_float((market_row or {}).get('best_price'))
            current_consensus = self._to_float((market_row or {}).get('consensus_fair_odds'))
            open_clv_pct = self._clv_pct(entry_odds, current_best)
            consensus_clv_pct = self._clv_pct(entry_odds, current_consensus)
            if open_clv_pct is not None:
                row['open_clv_pct'] = round(open_clv_pct, 3)
                open_clv_values.append(open_clv_pct)
            if consensus_clv_pct is not None:
                row['consensus_clv_pct'] = round(consensus_clv_pct, 3)
            row['last_seen_at'] = now_utc.isoformat()
            row['current_best_price'] = current_best
            row['current_consensus_fair_odds'] = current_consensus

            commence_time = self._parse_dt(row.get('commence_time'))
            should_resolve = commence_time is not None and commence_time <= now_utc + grace
            if should_resolve:
                row['resolved_at'] = now_utc.isoformat()
                resolved_rows.append(row)
                if open_clv_pct is not None:
                    resolved_values.append(open_clv_pct)
                newly_resolved += 1
            else:
                unresolved.append(row)

        self._write_json(self.pending_clv_path, unresolved)
        self._write_json(self.clv_results_path, resolved_rows[-1000:])
        return {
            'pending_count': len(unresolved),
            'resolved_count': newly_resolved,
            'avg_open_clv_pct': round(mean(open_clv_values), 3) if open_clv_values else None,
            'avg_resolved_clv_pct': round(mean(resolved_values), 3) if resolved_values else None,
            'results_kept': len(resolved_rows[-1000:]),
        }

    def _build_snapshot(self, matches: list[Match], offers_by_match: dict[str, list[Offer]], now_utc: datetime) -> dict[str, Any]:
        match_index = {match.match_key: match for match in matches}
        payload: dict[str, Any] = {
            'created_at': now_utc.isoformat(),
            'matches': {},
        }
        for match_key, offers in (offers_by_match or {}).items():
            rows: dict[str, Any] = {}
            for signature, bucket in self._group_offers(offers).items():
                prices = [float(item.price) for item in bucket if self._to_float(getattr(item, 'price', None)) and float(item.price) > 1.0]
                if not prices:
                    continue
                implieds = [1.0 / price for price in prices if price > 1.0]
                consensus_prob = self._weighted_consensus_probability(bucket)
                consensus_fair_odds = (1.0 / consensus_prob) if consensus_prob and consensus_prob > 0 else None
                rows[signature] = {
                    'family': bucket[0].family,
                    'selection': bucket[0].selection,
                    'point': bucket[0].point,
                    'team_side': bucket[0].team_side,
                    'books': sorted({str(item.bookmaker) for item in bucket}),
                    'sources': sorted({str(item.source) for item in bucket}),
                    'offers_count': len(bucket),
                    'best_price': max(prices),
                    'median_price': round(median(prices), 4),
                    'mean_price': round(mean(prices), 4),
                    'min_price': min(prices),
                    'max_price': max(prices),
                    'consensus_probability': round(consensus_prob, 6) if consensus_prob else None,
                    'consensus_fair_odds': round(consensus_fair_odds, 4) if consensus_fair_odds else None,
                    'dispersion_pct': round((max(implieds) - min(implieds)) * 100.0, 3) if len(implieds) >= 2 else 0.0,
                }
            if not rows:
                continue
            match = match_index.get(match_key)
            payload['matches'][match_key] = rows
            if match is not None:
                payload.setdefault('meta', {})[match_key] = {
                    'league_name': match.league_name,
                    'home_team': match.home_team,
                    'away_team': match.away_team,
                    'commence_time': match.commence_time.isoformat(),
                }
        return payload

    def _build_signal_map(self, current: dict[str, Any], previous: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        current_matches = current.get('matches', {}) if isinstance(current, dict) else {}
        prev_matches = previous.get('matches', {}) if isinstance(previous, dict) else {}
        min_delta = float(getattr(self.settings, 'line_movement_min_delta_pct', 1.75) or 1.75)
        for match_key, rows in current_matches.items():
            per_match: dict[str, Any] = {}
            prev_rows = prev_matches.get(match_key, {}) if isinstance(prev_matches, dict) else {}
            for signature, row in (rows or {}).items():
                prev_row = (prev_rows or {}).get(signature) if isinstance(prev_rows, dict) else None
                current_best = self._to_float(row.get('best_price'))
                previous_best = self._to_float((prev_row or {}).get('best_price'))
                current_consensus = self._to_float(row.get('consensus_fair_odds'))
                books_count = len(row.get('books') or [])
                sources_count = len(row.get('sources') or [])
                history_ready = bool(current_best and previous_best and current_best > 1.0 and previous_best > 1.0)
                enough_sample = books_count >= max(1, int(getattr(self.settings, 'line_movement_min_books', 2) or 2)) and sources_count >= max(1, int(getattr(self.settings, 'line_movement_min_sources', 1) or 1))
                delta_prob_pp = None
                delta_odds_pct = None
                if history_ready:
                    delta_prob_pp = ((1.0 / current_best) - (1.0 / previous_best)) * 100.0
                    delta_odds_pct = ((current_best - previous_best) / previous_best) * 100.0
                best_vs_consensus_edge_pct = None
                if current_best and current_consensus and current_consensus > 1.0:
                    best_vs_consensus_edge_pct = ((current_best / current_consensus) - 1.0) * 100.0
                movement_label = 'flat'
                if not history_ready:
                    movement_label = 'insufficient_history'
                elif not enough_sample:
                    movement_label = 'low_sample'
                elif delta_prob_pp is not None:
                    if delta_prob_pp >= min_delta:
                        movement_label = 'steam'
                    elif delta_prob_pp <= -min_delta:
                        movement_label = 'drift'
                per_match[signature] = {
                    'best_price': current_best,
                    'previous_best_price': previous_best,
                    'consensus_fair_odds': current_consensus,
                    'consensus_dispersion_pct': self._to_float(row.get('dispersion_pct')),
                    'delta_prob_pp': round(delta_prob_pp, 3) if delta_prob_pp is not None else None,
                    'delta_odds_pct': round(delta_odds_pct, 3) if delta_odds_pct is not None else None,
                    'best_vs_consensus_edge_pct': round(best_vs_consensus_edge_pct, 3) if best_vs_consensus_edge_pct is not None else None,
                    'movement_label': movement_label,
                    'history_ready': history_ready,
                    'observation_count': 2 if history_ready else 1,
                    'books_count': books_count,
                    'sources_count': sources_count,
                }
            result[match_key] = per_match
        return result

    def _group_offers(self, offers: list[Offer]) -> dict[str, list[Offer]]:
        grouped: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers or []:
            key = self.market_signal_key(offer.family, offer.selection, offer.point, getattr(offer, 'team_side', None))
            grouped[key].append(offer)
        return grouped

    def _weighted_consensus_probability(self, offers: list[Offer]) -> float | None:
        weighted: list[tuple[float, float]] = []
        for offer in offers or []:
            price = self._to_float(getattr(offer, 'price', None))
            if price is None or price <= 1.0:
                continue
            weight = float(self.settings.bookmaker_weight(getattr(offer, 'bookmaker', '')))
            weight *= float(self.settings.source_weight(getattr(offer, 'source', '')))
            weighted.append((1.0 / price, weight))
        if not weighted:
            return None
        total_w = sum(weight for _, weight in weighted)
        if total_w <= 0:
            return None
        return sum(prob * weight for prob, weight in weighted) / total_w

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        stamp = self._snapshot_stamp(snapshot.get('created_at'))
        path = self.base_dir / f'{stamp}-market-snapshot.json'
        self._write_json(path, snapshot)
        self._write_json(self.latest_snapshot_path, snapshot)
        self._trim_snapshot_history()

    def _trim_snapshot_history(self) -> None:
        files = sorted(self.base_dir.glob('*-market-snapshot.json'))
        limit = max(int(getattr(self.settings, 'market_snapshot_history_limit', 96) or 96), 8)
        for path in files[:-limit]:
            try:
                path.unlink()
            except Exception:
                pass

    @staticmethod
    def market_signal_key(family: Any, selection: Any, point: Any, team_side: Any) -> str:
        fam = str(family or '').strip()
        sel = str(selection or '').strip().lower()
        team = str(team_side or '').strip().lower()
        pt = '' if point in (None, '') else f'{float(point):.2f}'
        return '|'.join([fam, sel, pt, team])

    @staticmethod
    def _candidate_fingerprint(candidate: CandidateBet) -> str:
        point = candidate.point
        if isinstance(point, float):
            point = round(point, 4)
        selection_key = getattr(candidate, 'selection_key', '') or candidate_selection_key(
            str(candidate.family or ''),
            str(candidate.selection or ''),
            point=candidate.point,
            team_side=getattr(candidate, 'team_side', None),
            home_team=str(candidate.home_team or ''),
            away_team=str(candidate.away_team or ''),
        )
        team_side = str(getattr(candidate, 'team_side', '') or '').strip().lower()
        return '|'.join(
            [
                str(candidate.match_key),
                str(candidate.family),
                str(selection_key or candidate.selection),
                team_side,
                str(point),
                candidate.commence_time.isoformat(),
            ]
        )

    @staticmethod
    def _snapshot_stamp(value: Any) -> str:
        dt = MarketMonitor._parse_dt(value) or datetime.now(UTC)
        return dt.astimezone(UTC).strftime('%Y%m%d-%H%M%S')

    @staticmethod
    def _clv_pct(entry_odds: float | None, close_odds: float | None) -> float | None:
        if not entry_odds or not close_odds or entry_odds <= 1.0 or close_odds <= 1.0:
            return None
        return ((entry_odds / close_odds) - 1.0) * 100.0

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                return None
            return number
        except Exception:
            return None

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
        return default

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _sample_signals(signals: dict[str, dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for match_key, per_match in signals.items():
            for signal_key, signal in per_match.items():
                rows.append({'match_key': match_key, 'signal_key': signal_key, **signal})
                if len(rows) >= limit:
                    return rows
        return rows
