from __future__ import annotations

from collections import defaultdict
from typing import Any
import os

_PATCH_APPLIED = False


def _pct_aware_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", ".")
    if text.endswith("%"):
        text = text[:-1].strip()
    if text in {"", ".", "-", "+"}:
        return None
    return text


def _pct_aware_float(value: Any, default: float | None = None) -> float | None:
    try:
        text = _pct_aware_text(value)
        if text is None:
            return default
        return float(text)
    except Exception:
        return default


def _deep_strip_percent_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_strip_percent_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_strip_percent_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_strip_percent_strings(item) for item in value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            return text[:-1].strip().replace(",", ".")
    return value


def _build_fallback_api_football_context(self: Any, row: dict[str, Any], fixture: dict[str, Any]):
    from app.schemas import MatchContext
    from app.utils import clamp, normalize_probability_percent

    safe_row = _deep_strip_percent_strings(row if isinstance(row, dict) else {})
    safe_fixture = _deep_strip_percent_strings(fixture if isinstance(fixture, dict) else {})

    preds = safe_row.get("predictions") or safe_row.get("prediction") or {}
    percent = preds.get("percent") or {}

    home_prob = normalize_probability_percent(percent.get("home") or percent.get("Home"))
    draw_prob = normalize_probability_percent(percent.get("draw") or percent.get("Draw"))
    away_prob = normalize_probability_percent(percent.get("away") or percent.get("Away"))

    expected_home, expected_away, xg_source = self._derive_expected_goals(
        safe_row,
        preds,
        home_prob,
        draw_prob,
        away_prob,
    )

    home_form = self._team_percent(safe_row, "home", "form")
    away_form = self._team_percent(safe_row, "away", "form")
    home_att = self._team_percent(safe_row, "home", "att")
    away_att = self._team_percent(safe_row, "away", "att")
    home_def = self._team_percent(safe_row, "home", "def")
    away_def = self._team_percent(safe_row, "away", "def")

    confidence = 58.0
    if home_prob is not None and away_prob is not None:
        confidence += 5.0
    if expected_home is not None and expected_away is not None:
        confidence += 4.0
    if home_form is not None and away_form is not None:
        confidence += 1.0
    confidence = clamp(confidence, 56.0, 74.0)

    return MatchContext(
        source="api_football",
        payload={"prediction": safe_row, "fixture": safe_fixture, "runtime_fallback": True},
        expected_home=expected_home,
        expected_away=expected_away,
        home_win_probability=home_prob,
        away_win_probability=away_prob,
        confidence=confidence,
        details={
            "api_football_runtime_fallback": True,
            "api_football_draw_probability": draw_prob,
            "api_football_advice": preds.get("advice"),
            "api_football_under_over": preds.get("under_over"),
            "api_football_expected_goals_source": xg_source,
            "api_football_home_form": home_form,
            "api_football_away_form": away_form,
            "api_football_home_attack": home_att,
            "api_football_away_attack": away_att,
            "api_football_home_defense": home_def,
            "api_football_away_defense": away_def,
            "home_form": home_form,
            "away_form": away_form,
            "home_attack": home_att,
            "away_attack": away_att,
            "home_defense": home_def,
            "away_defense": away_def,
            "home_gf_pg": self._team_goal_average(safe_row, "home", "for", "home"),
            "away_gf_pg": self._team_goal_average(safe_row, "away", "for", "away"),
            "home_ga_pg": self._team_goal_average(safe_row, "home", "against", "home"),
            "away_ga_pg": self._team_goal_average(safe_row, "away", "against", "away"),
        },
    )


class _NoWeatherEnricher:
    async def enrich_context(self, client, match, fixture, context):
        return context, {
            "enabled": False,
            "cache_hit": False,
            "requests": 0,
            "response_errors": 0,
            "provider": "disabled_on_retry",
            "enriched": False,
            "runtime_recovery": True,
        }


def apply_runtime_code_and_logs_fix() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _PATCH_APPLIED = True

    # 1) Hard fail guard for weather parsing inside api_football.
    try:
        from app.providers.weather_common import WeatherContextEnricher

        WeatherContextEnricher._to_float = staticmethod(_pct_aware_float)

        _orig_weather_enrich = WeatherContextEnricher.enrich_context

        async def _patched_weather_enrich(self, client, match, fixture, context):
            try:
                return await _orig_weather_enrich(self, client, match, fixture, context)
            except Exception as exc:
                return context, {
                    "enabled": bool(getattr(self, "weatherapi_key", "") or getattr(self, "openweather_key", "")),
                    "cache_hit": False,
                    "requests": 0,
                    "response_errors": 1,
                    "provider": "weather_runtime_error",
                    "enriched": False,
                    "runtime_error": f"{type(exc).__name__}: {exc}",
                }

        WeatherContextEnricher.enrich_context = _patched_weather_enrich
    except Exception:
        pass

    # 2) api_football: percent-safe parsing + runtime fallback + retry without weather.
    try:
        from app.providers.api_football import ApiFootballContextProvider
        from app.utils import clamp

        ApiFootballContextProvider._to_float = staticmethod(_pct_aware_float)

        def _patched_to_unit_percent(value: Any) -> float | None:
            number = _pct_aware_float(value, None)
            if number is None:
                return None
            if number > 1.0:
                number /= 100.0
            return clamp(number, 0.0, 1.0)

        ApiFootballContextProvider._to_unit_percent = staticmethod(_patched_to_unit_percent)

        _orig_prediction_to_context = ApiFootballContextProvider._prediction_to_context
        _orig_fetch_context = ApiFootballContextProvider.fetch_context

        def _patched_prediction_to_context(self, row, fixture):
            safe_row = _deep_strip_percent_strings(row)
            safe_fixture = _deep_strip_percent_strings(fixture)
            try:
                return _orig_prediction_to_context(self, safe_row, safe_fixture)
            except Exception:
                return _build_fallback_api_football_context(self, safe_row, safe_fixture)

        async def _patched_fetch_context(self, matches):
            try:
                return await _orig_fetch_context(self, matches)
            except Exception as exc:
                original_weather = getattr(self, "weather_enricher", None)
                try:
                    self.weather_enricher = _NoWeatherEnricher()
                    contexts, stats, preview = await _orig_fetch_context(self, matches)
                    if isinstance(stats, dict):
                        stats["runtime_recovered_after_retry"] = True
                        stats["runtime_error_initial"] = f"{type(exc).__name__}: {exc}"
                    return contexts, stats, preview
                except Exception as retry_exc:
                    return {}, {
                        "enabled": bool(getattr(self, "api_key", "")),
                        "api_key_present": bool(getattr(self, "api_key", "")),
                        "requests": 0,
                        "response_errors": 1,
                        "fixtures_fetched": 0,
                        "contexts_built": 0,
                        "rate_limited": False,
                        "runtime_error_initial": f"{type(exc).__name__}: {exc}",
                        "runtime_error": f"{type(retry_exc).__name__}: {retry_exc}",
                    }, {
                        "sample_fixtures": [],
                        "sample_predictions": [],
                        "sample_weather": [],
                    }
                finally:
                    if original_weather is not None:
                        self.weather_enricher = original_weather

        ApiFootballContextProvider._prediction_to_context = _patched_prediction_to_context
        ApiFootballContextProvider.fetch_context = _patched_fetch_context
    except Exception:
        pass

    # 3) Candidate generation: real fallback when derived-market guards are too strict.
    try:
        from app.services.model import CandidateFactory
        from app.utils import clamp, russian_selection

        _orig_ready = CandidateFactory._market_signal_ready_for_derived
        _orig_simple_market_model_probability = CandidateFactory._simple_market_model_probability
        _orig_build_simple_market_h2h = CandidateFactory._build_simple_market_h2h_candidates

        def _patched_market_signal_ready_for_derived(self, family: str, market_signal: dict[str, Any] | None, offers):
            if _orig_ready(self, family, market_signal, offers):
                return True

            books_count = 0
            sources_count = 0
            if offers is not None:
                books_count = len({self._norm_book(item.bookmaker) for item in offers if str(getattr(item, "bookmaker", "") or "").strip()})
                sources_count = len({str(getattr(item, "source", "") or "").strip().lower() for item in offers if str(getattr(item, "source", "") or "").strip()})

            if not isinstance(market_signal, dict):
                if family in {"totals", "spreads"} and books_count >= 2:
                    return True
                return False

            books_count = max(books_count, int(market_signal.get("books_count") or 0))
            sources_count = max(sources_count, int(market_signal.get("sources_count") or 0))
            edge_pct = self._to_float_safe(market_signal.get("best_vs_consensus_edge_pct")) or 0.0
            delta_prob_pp = self._to_float_safe(market_signal.get("delta_prob_pp")) or 0.0
            dispersion_pct = self._to_float_safe(market_signal.get("consensus_dispersion_pct"))
            selection_key = str(market_signal.get("selection_key") or "").strip().lower()

            if family == "h2h" and selection_key == "draw":
                return False
            if books_count < 1 or sources_count < 1:
                return False
            if dispersion_pct is not None and dispersion_pct > 16.0:
                return False
            if edge_pct < 0.15:
                return False
            if delta_prob_pp < -0.75:
                return False
            return True

        def _patched_simple_market_model_probability(self, *, family: str, market_prob: float, market_signal: dict[str, Any] | None, books_count: int):
            value = _orig_simple_market_model_probability(
                self,
                family=family,
                market_prob=market_prob,
                market_signal=market_signal,
                books_count=books_count,
            )
            if value is not None:
                return value

            market_prob = clamp(float(market_prob), 0.02, 0.98)
            edge_pct = self._to_float_safe((market_signal or {}).get("best_vs_consensus_edge_pct")) or 0.0
            steam_delta = self._to_float_safe((market_signal or {}).get("delta_prob_pp")) or 0.0
            dispersion_pct = self._to_float_safe((market_signal or {}).get("consensus_dispersion_pct"))

            if market_signal is None:
                if books_count < 1:
                    return None
                if family == "h2h" and (market_prob < 0.18 or market_prob > 0.82):
                    return None
                base_boost = 1.20 if family == "totals" else 0.95 if family == "spreads" else 0.60
                if books_count >= 2:
                    base_boost += 0.25
                return clamp(market_prob + base_boost / 100.0, 0.02, 0.98)

            if dispersion_pct is not None and dispersion_pct > 15.0:
                return None
            if edge_pct < 0.10 and steam_delta < 0.0:
                return None

            signal_boost_pct = 0.60
            if edge_pct > 0:
                signal_boost_pct += min(2.2, edge_pct * 0.55)
            if steam_delta > 0:
                signal_boost_pct += min(1.4, steam_delta * 0.40)
            if books_count >= 2:
                signal_boost_pct += 0.35

            family_cap = 4.0 if family == "totals" else 3.2 if family == "h2h" else 3.0
            return clamp(market_prob + min(family_cap, signal_boost_pct) / 100.0, 0.02, 0.98)

        def _patched_build_simple_market_h2h_candidates(self, match, offers, rejections):
            buckets = defaultdict(list)
            for offer in offers:
                buckets[offer.selection].append(offer)
            result = []
            high_odds_skip_at = float(os.getenv("SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_AT") or 4.35)
            for selection, bucket in buckets.items():
                selection_key = self._h2h_selection_key(match, selection)
                if selection_key not in {"home", "away"}:
                    continue
                required_books = self._required_books_for_bucket("h2h", None, bucket, None)
                unique_books = len({self._norm_book(item.bookmaker) for item in bucket})
                if unique_books < required_books:
                    continue
                best_offer = self._select_best_offer(bucket)
                market_signal = self._market_signal_for_bucket(match.match_key, "h2h", bucket, None)

                # Only hard-skip very high prices when support is minimal and no usable signal exists.
                if float(best_offer.price) >= high_odds_skip_at and unique_books <= 1 and not isinstance(market_signal, dict):
                    rejections["simple_market_h2h_high_odds_skip"] += 1
                    continue

                market_prob = self._fair_market_probability_h2h(match, offers, selection)
                model_prob = self._simple_market_model_probability(
                    family="h2h",
                    market_prob=market_prob,
                    market_signal=market_signal,
                    books_count=unique_books,
                )
                if model_prob is None:
                    rejections["simple_market_signal_missing_h2h"] += 1
                    continue
                candidate = self._candidate_from_bucket(
                    match=match,
                    family="h2h",
                    selection=russian_selection("h2h", selection),
                    point=None,
                    offers=bucket,
                    market_prob=market_prob,
                    model_prob=model_prob,
                    reasons=[
                        "mode=market_fallback",
                        "model=market_signal_consensus_relaxed",
                        "signals=market+consensus",
                        "context=none",
                    ],
                    expected_home=None,
                    expected_away=None,
                    model_mode="market_simple_h2h",
                    context=None,
                    market_signal=market_signal,
                )
                if candidate:
                    result.append(candidate)
            return result

        CandidateFactory._market_signal_ready_for_derived = _patched_market_signal_ready_for_derived
        CandidateFactory._simple_market_model_probability = _patched_simple_market_model_probability
        CandidateFactory._build_simple_market_h2h_candidates = _patched_build_simple_market_h2h_candidates
    except Exception:
        pass


apply_runtime_code_and_logs_fix()
