from __future__ import annotations

from pathlib import Path

MODEL_PATH = Path("app/services/model.py")
RUNNER_PATH = Path("app/services/runner.py")


HELPER_METHOD = """
    @staticmethod
    def _rescue_json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, 'isoformat'):
            try:
                return value.isoformat()
            except Exception:
                return str(value)
        if isinstance(value, dict):
            return {str(k): CandidateFactory._rescue_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [CandidateFactory._rescue_json_safe(v) for v in value]
        return str(value)

    def _rescue_candidate_debug_payload(self, candidate: CandidateBet) -> dict[str, Any]:
        source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
        diagnostics = dict(getattr(candidate, 'diagnostics', {}) or {})
        analysis = dict(getattr(candidate, 'analysis', {}) or {})
        return {
            'match_key': str(getattr(candidate, 'match_key', '') or ''),
            'sport_key': str(getattr(candidate, 'sport_key', '') or ''),
            'league_name': str(getattr(candidate, 'league_name', '') or ''),
            'home_team': str(getattr(candidate, 'home_team', '') or ''),
            'away_team': str(getattr(candidate, 'away_team', '') or ''),
            'commence_time': self._rescue_json_safe(getattr(candidate, 'commence_time', None)),
            'family': str(getattr(candidate, 'family', '') or ''),
            'selection': str(getattr(candidate, 'selection', '') or ''),
            'selection_key': str(getattr(candidate, 'selection_key', '') or ''),
            'point': self._rescue_json_safe(getattr(candidate, 'point', None)),
            'team_side': self._rescue_json_safe(getattr(candidate, 'team_side', None)),
            'odds': float(getattr(candidate, 'odds', 0.0) or 0.0),
            'fair_odds': float(getattr(candidate, 'fair_odds', 0.0) or 0.0),
            'implied_probability': float(getattr(candidate, 'implied_probability', 0.0) or 0.0),
            'market_probability': float(getattr(candidate, 'market_probability', 0.0) or 0.0),
            'consensus_probability': float(getattr(candidate, 'consensus_probability', 0.0) or 0.0),
            'model_probability': float(getattr(candidate, 'model_probability', 0.0) or 0.0),
            'final_probability': float(getattr(candidate, 'final_probability', 0.0) or 0.0),
            'adjusted_probability': float(getattr(candidate, 'adjusted_probability', 0.0) or 0.0),
            'edge_pct': float(getattr(candidate, 'edge_pct', 0.0) or 0.0),
            'ev_pct': float(getattr(candidate, 'ev_pct', 0.0) or 0.0),
            'confidence': float(getattr(candidate, 'confidence', 0.0) or 0.0),
            'books_count': int(getattr(candidate, 'books_count', 0) or 0),
            'sources_count': int(getattr(candidate, 'sources_count', 0) or 0),
            'model_mode': str(getattr(candidate, 'model_mode', '') or ''),
            'expected_home': self._rescue_json_safe(getattr(candidate, 'expected_home', None)),
            'expected_away': self._rescue_json_safe(getattr(candidate, 'expected_away', None)),
            'publication_score': float(getattr(candidate, 'publication_score', 0.0) or 0.0),
            'source_event_id': self._rescue_json_safe(getattr(candidate, 'source_event_id', None)),
            'bookmaker': self._rescue_json_safe(getattr(candidate, 'bookmaker', None)),
            'reasons': self._rescue_json_safe(list(getattr(candidate, 'reasons', []) or [])),
            'source_summary': self._rescue_json_safe(source_summary),
            'diagnostics': self._rescue_json_safe(diagnostics),
            'analysis': self._rescue_json_safe(analysis),
        }

"""


def patch_model() -> bool:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(MODEL_PATH)
    text = MODEL_PATH.read_text(encoding="utf-8")
    changed = False

    if "_rescue_candidate_debug_payload" not in text:
        anchor = "    def _build_totals_candidates(\n"
        if anchor not in text:
            raise RuntimeError("Cannot find _build_totals_candidates anchor in model.py")
        text = text.replace(anchor, HELPER_METHOD + anchor, 1)
        changed = True

    old_return = (
        "        candidates = self._filter_and_rank(candidates, rejections)\n"
        "        return candidates, dict(rejections), {'matches': debug_rows[:200]}\n"
    )
    new_return = (
        "        try:\n"
        "            rescue_limit = int(float(__import__('os').getenv('RESCUE_DEBUG_CANDIDATE_LIMIT', '220') or 220))\n"
        "        except Exception:\n"
        "            rescue_limit = 220\n"
        "        rescue_pre_filter_candidates = [\n"
        "            self._rescue_candidate_debug_payload(item)\n"
        "            for item in candidates[:max(0, rescue_limit)]\n"
        "        ]\n"
        "        candidates = self._filter_and_rank(candidates, rejections)\n"
        "        return candidates, dict(rejections), {\n"
        "            'matches': debug_rows[:200],\n"
        "            'rescue_pre_filter_candidates': rescue_pre_filter_candidates,\n"
        "            'rescue_pre_filter_candidates_count': len(rescue_pre_filter_candidates),\n"
        "        }\n"
    )
    if "rescue_pre_filter_candidates" not in text:
        if old_return not in text:
            raise RuntimeError("Cannot find build_candidates return block in model.py")
        text = text.replace(old_return, new_return, 1)
        changed = True

    if changed:
        MODEL_PATH.write_text(text, encoding="utf-8")
    return changed


def patch_runner() -> bool:
    if not RUNNER_PATH.exists():
        raise FileNotFoundError(RUNNER_PATH)
    text = RUNNER_PATH.read_text(encoding="utf-8")
    if "latest-rescue-candidates.json" in text:
        return False

    anchor = (
        "            raw_candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts, market_signals)\n"
    )
    insert = anchor + (
        "            rescue_pre_filter_candidates = list((model_debug or {}).get('rescue_pre_filter_candidates') or [])\n"
        "            try:\n"
        "                rescue_payload = {\n"
        "                    'created_at': datetime.now(UTC).isoformat(),\n"
        "                    'source': 'model_debug.rescue_pre_filter_candidates',\n"
        "                    'count': len(rescue_pre_filter_candidates),\n"
        "                    'candidates': rescue_pre_filter_candidates,\n"
        "                }\n"
        "                Path(self.settings.storage_export_dir).mkdir(parents=True, exist_ok=True)\n"
        "                Path('artifacts/run-bot').mkdir(parents=True, exist_ok=True)\n"
        "                rescue_text = json.dumps(rescue_payload, ensure_ascii=False, indent=2)\n"
        "                Path(self.settings.storage_export_dir, 'latest-rescue-candidates.json').write_text(rescue_text, encoding='utf-8')\n"
        "                Path('artifacts/run-bot/latest-rescue-candidates.json').write_text(rescue_text, encoding='utf-8')\n"
        "            except Exception:\n"
        "                rejections['rescue_candidate_export_failed'] = rejections.get('rescue_candidate_export_failed', 0) + 1\n"
    )
    if anchor not in text:
        raise RuntimeError("Cannot find factory.build_candidates anchor in runner.py")
    text = text.replace(anchor, insert, 1)
    RUNNER_PATH.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    model_changed = patch_model()
    runner_changed = patch_runner()
    print(f"rescue pre-filter candidate pool patch: model_changed={model_changed}, runner_changed={runner_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
