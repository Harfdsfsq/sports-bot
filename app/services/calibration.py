from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(slots=True)
class CalibrationDiagnostics:
    raw_probability: float
    market_probability: float
    blended_probability: float
    final_probability: float
    alpha: float
    max_gap_pp: float
    segment_delta_probability: float
    family: str
    odds: float
    confidence: float
    books_count: int
    sources_count: int
    model_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'raw_probability': round(self.raw_probability, 6),
            'market_probability': round(self.market_probability, 6),
            'blended_probability': round(self.blended_probability, 6),
            'final_probability': round(self.final_probability, 6),
            'alpha': round(self.alpha, 4),
            'max_gap_pp': round(self.max_gap_pp, 3),
            'segment_delta_probability': round(self.segment_delta_probability, 6),
            'family': self.family,
            'odds': round(self.odds, 4),
            'confidence': round(self.confidence, 3),
            'books_count': self.books_count,
            'sources_count': self.sources_count,
            'model_mode': self.model_mode,
        }


class ProbabilityCalibrationService:
    """Soft probability calibration.

    Goals:
    - prevent extremely optimistic edges against the market;
    - preserve model signal when confidence/books/sources are solid;
    - allow segment-specific penalties or boosts through a JSON profile.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.profile_path = Path(
            os.getenv('CALIBRATION_PROFILE_PATH')
            or getattr(settings, 'calibration_profile_path', None)
            or '.data/calibration-profile.json'
        )
        self.profile = self._load_profile()

    def adjust(
        self,
        *,
        raw_probability: float,
        market_probability: float,
        family: str,
        odds: float,
        confidence: float,
        books_count: int,
        sources_count: int,
        model_mode: str,
    ) -> tuple[float, dict[str, Any]]:
        raw_probability = _clamp(float(raw_probability), 0.02, 0.98)
        market_probability = _clamp(float(market_probability), 0.02, 0.98)
        family = str(family or 'unknown')
        odds = float(odds or 0.0)
        confidence = float(confidence or 0.0)
        books_count = max(0, int(books_count or 0))
        sources_count = max(0, int(sources_count or 0))
        model_mode = str(model_mode or '')

        alpha = self._alpha(confidence=confidence, books_count=books_count, sources_count=sources_count)
        blended = market_probability + alpha * (raw_probability - market_probability)

        segment_delta = self._segment_delta(
            family=family,
            odds=odds,
            confidence=confidence,
            books_count=books_count,
        )
        blended = _clamp(blended + segment_delta, 0.02, 0.98)

        max_gap_pp = self._max_gap_pp(family=family, confidence=confidence)
        final_probability = self._cap_gap_to_market(blended, market_probability, max_gap_pp)

        diagnostics = CalibrationDiagnostics(
            raw_probability=raw_probability,
            market_probability=market_probability,
            blended_probability=blended,
            final_probability=final_probability,
            alpha=alpha,
            max_gap_pp=max_gap_pp,
            segment_delta_probability=segment_delta,
            family=family,
            odds=odds,
            confidence=confidence,
            books_count=books_count,
            sources_count=sources_count,
            model_mode=model_mode,
        )
        return final_probability, diagnostics.as_dict()

    def refresh_profile(self) -> None:
        self.profile = self._load_profile()

    def _load_profile(self) -> dict[str, Any]:
        if not self.profile_path.exists():
            return {'segments': {}}
        try:
            return json.loads(self.profile_path.read_text(encoding='utf-8'))
        except Exception:
            return {'segments': {}}

    def _alpha(self, *, confidence: float, books_count: int, sources_count: int) -> float:
        base = float(os.getenv('CALIBRATION_BLEND_ALPHA_BASE') or getattr(self.settings, 'calibration_blend_alpha_base', 0.40) or 0.40)
        alpha_max = float(os.getenv('CALIBRATION_BLEND_ALPHA_MAX') or getattr(self.settings, 'calibration_blend_alpha_max', 0.70) or 0.70)

        confidence_bonus = 0.0
        if confidence >= 62:
            confidence_bonus += 0.12
        elif confidence >= 57:
            confidence_bonus += 0.08
        elif confidence >= 52:
            confidence_bonus += 0.04

        books_bonus = 0.0
        if books_count >= 3:
            books_bonus += 0.08
        elif books_count == 2:
            books_bonus += 0.05
        elif books_count == 1:
            books_bonus += 0.02

        sources_bonus = 0.0
        if sources_count >= 3:
            sources_bonus += 0.06
        elif sources_count == 2:
            sources_bonus += 0.04
        elif sources_count == 1:
            sources_bonus += 0.02

        return _clamp(base + confidence_bonus + books_bonus + sources_bonus, 0.15, alpha_max)

    def _max_gap_pp(self, *, family: str, confidence: float) -> float:
        if family == 'totals':
            default = float(os.getenv('CALIBRATION_MAX_GAP_TOTALS_PP') or getattr(self.settings, 'calibration_max_gap_totals_pp', 6.5) or 6.5)
        elif family == 'spreads':
            default = float(os.getenv('CALIBRATION_MAX_GAP_SPREADS_PP') or getattr(self.settings, 'calibration_max_gap_spreads_pp', 7.0) or 7.0)
        else:
            default = float(os.getenv('CALIBRATION_MAX_GAP_H2H_PP') or getattr(self.settings, 'calibration_max_gap_h2h_pp', 8.0) or 8.0)

        min_conf_for_max = float(os.getenv('CALIBRATION_MIN_CONFIDENCE_FOR_MAX_GAP') or getattr(self.settings, 'calibration_min_confidence_for_max_gap', 62.0) or 62.0)
        if confidence >= min_conf_for_max:
            return default
        if confidence >= min_conf_for_max - 5:
            return max(3.5, default - 1.0)
        return max(3.0, default - 2.0)

    def _cap_gap_to_market(self, probability: float, market_probability: float, max_gap_pp: float) -> float:
        max_gap = float(max_gap_pp) / 100.0
        delta = probability - market_probability
        if delta > max_gap:
            return _clamp(market_probability + max_gap, 0.02, 0.98)
        if delta < -max_gap:
            return _clamp(market_probability - max_gap, 0.02, 0.98)
        return _clamp(probability, 0.02, 0.98)

    def _segment_delta(self, *, family: str, odds: float, confidence: float, books_count: int) -> float:
        segments = dict((self.profile or {}).get('segments') or {})
        delta = 0.0
        for key in (
            f'family:{family}',
            f'odds_bucket:{self._odds_bucket(odds)}',
            f'books_bucket:{books_count}',
            f'confidence_bucket:{self._confidence_bucket(confidence)}',
        ):
            payload = segments.get(key)
            if not isinstance(payload, dict):
                continue
            value = payload.get('delta_probability')
            try:
                delta += float(value)
            except Exception:
                continue
        return _clamp(delta, -0.08, 0.08)

    @staticmethod
    def _odds_bucket(odds: float) -> str:
        if odds < 1.8:
            return '1.00-1.79'
        if odds < 2.2:
            return '1.80-2.19'
        if odds < 3.0:
            return '2.20-2.99'
        if odds < 4.5:
            return '3.00-4.50'
        return '4.50+'

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        if confidence < 45:
            return '0-44'
        if confidence < 52:
            return '45-52'
        if confidence < 58:
            return '52-58'
        if confidence < 64:
            return '58-64'
        return '64+'
