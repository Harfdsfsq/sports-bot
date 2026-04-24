
from __future__ import annotations

from pathlib import Path

MODEL_PATH = Path("app/services/model.py")
RUNNER_PATH = Path("app/services/runner.py")


def patch_model() -> bool:
    if not MODEL_PATH.exists():
        print(f"skip: {MODEL_PATH} not found")
        return False
    src = MODEL_PATH.read_text(encoding="utf-8")
    original = src

    if "self._latest_rescue_candidates" not in src:
        marker = "        self._market_signals_by_match: dict[str, dict[str, Any]] = {}\n"
        if marker in src:
            src = src.replace(
                marker,
                marker + "        self._latest_rescue_candidates: list[CandidateBet] = []\n",
                1,
            )
        else:
            print("warn: model init marker not found")

    if "rescue pre-filter pool reset" not in src:
        marker = "        self._market_signals_by_match = market_signals_by_match or {}\n"
        if marker in src:
            src = src.replace(
                marker,
                marker + "        # rescue pre-filter pool reset: used only by external controlled publisher\n"
                         "        self._latest_rescue_candidates = []\n",
                1,
            )
        else:
            print("warn: model reset marker not found")

    if "rescue pre-filter pool capture" not in src:
        marker = "            match_candidates.sort(key=lambda item: self._candidate_rank_key(item), reverse=True)\n"
        if marker in src:
            src = src.replace(
                marker,
                marker + "            # rescue pre-filter pool capture: keep fresh near-miss candidates before _filter_and_rank\n"
                         "            if match_candidates:\n"
                         "                rescue_keep_count = max(1, int(getattr(self.settings, 'max_rescue_candidates_per_match', 8) or 8))\n"
                         "                self._latest_rescue_candidates.extend(match_candidates[:rescue_keep_count])\n",
                1,
            )
        else:
            print("warn: model capture marker not found")

    if src != original:
        MODEL_PATH.write_text(src, encoding="utf-8")
        print(f"patched: {MODEL_PATH}")
        return True
    print(f"already patched or no changes: {MODEL_PATH}")
    return False


RUNNER_METHODS = '''
    def _rescue_json_safe(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): self._rescue_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._rescue_json_safe(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _candidate_to_rescue_dict(self, candidate: CandidateBet) -> dict[str, Any]:
        try:
            if hasattr(candidate, 'model_dump'):
                payload = candidate.model_dump(mode='json')
            else:
                payload = asdict(candidate)
        except Exception:
            payload = {}
            for name in (
                'match_key', 'home_team', 'away_team', 'league_name', 'commence_time',
                'family', 'selection', 'selection_key', 'point', 'team_side', 'odds',
                'bookmaker', 'market_probability', 'consensus_probability',
                'model_probability', 'adjusted_probability', 'confidence', 'edge_pct',
                'ev_pct', 'books_count', 'sources_count', 'expected_home',
                'expected_away', 'publication_score', 'model_mode', 'source_summary',
                'diagnostics', 'reasons',
            ):
                payload[name] = getattr(candidate, name, None)
        return self._rescue_json_safe(payload)

    def _export_rescue_prefilter_candidates(self, candidates: list[CandidateBet]) -> None:
        max_items = max(1, int(getattr(self.settings, 'max_rescue_prefilter_candidates_export', 250) or 250))
        rows = [self._candidate_to_rescue_dict(item) for item in candidates[:max_items]]
        for raw_path in (
            Path(self.settings.storage_export_dir) / 'latest-rescue-candidates.json',
            Path('artifacts/run-bot/latest-rescue-candidates.json'),
        ):
            try:
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                continue

'''


def patch_runner() -> bool:
    if not RUNNER_PATH.exists():
        print(f"skip: {RUNNER_PATH} not found")
        return False
    src = RUNNER_PATH.read_text(encoding="utf-8")
    original = src

    if "def _export_rescue_prefilter_candidates" not in src:
        marker = "    async def run_once(self) -> dict[str, Any]:\n"
        if marker in src:
            src = src.replace(marker, RUNNER_METHODS + "\n" + marker, 1)
        else:
            print("warn: run_once marker not found")

    if "rescue_prefilter_candidates = list(getattr(self.factory, '_latest_rescue_candidates'" not in src:
        marker = "            raw_candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts, market_signals)\n"
        if marker in src:
            src = src.replace(
                marker,
                marker + "            rescue_prefilter_candidates = list(getattr(self.factory, '_latest_rescue_candidates', []) or [])\n"
                         "            self._export_rescue_prefilter_candidates(rescue_prefilter_candidates)\n",
                1,
            )
        else:
            print("warn: factory build marker not found")

    if src != original:
        RUNNER_PATH.write_text(src, encoding="utf-8")
        print(f"patched: {RUNNER_PATH}")
        return True
    print(f"already patched or no changes: {RUNNER_PATH}")
    return False


def main() -> int:
    patch_model()
    patch_runner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
