from __future__ import annotations

import re
from pathlib import Path

API_FOOTBALL_PATH = Path("app/providers/api_football.py")
RUNNER_PATH = Path("app/services/runner.py")


API_ERROR_METHOD = '''
    @staticmethod
    def _api_error_detail(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        errors = payload.get("errors")
        if isinstance(errors, dict):
            parts: list[str] = []
            for key, value in errors.items():
                if value in (None, "", [], {}):
                    continue
                parts.append(f"{key}: {value}")
            return "; ".join(parts)
        if isinstance(errors, list):
            return "; ".join(str(item) for item in errors if str(item).strip())
        if isinstance(errors, str):
            return errors
        return ""

'''

API_ERROR_METHODS_REPLACEMENT = '''
    @staticmethod
    def _has_rate_limit_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if isinstance(errors, dict) and bool(errors.get("rateLimit")):
            return True
        detail = ApiFootballContextProvider._api_error_detail(payload).lower()
        return any(token in detail for token in ("rate limit", "ratelimit", "quota", "too many requests"))

    @staticmethod
    def _has_plan_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if isinstance(errors, dict) and bool(errors.get("plan")):
            return True
        detail = ApiFootballContextProvider._api_error_detail(payload).lower()
        return any(token in detail for token in ("plan", "subscription", "subscribe", "free", "not allowed", "endpoint"))

    @staticmethod
    def _has_auth_error(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if isinstance(errors, dict):
            if bool(errors.get("token") or errors.get("authorization") or errors.get("auth") or errors.get("access")):
                return True
        detail = ApiFootballContextProvider._api_error_detail(payload).lower()
        return any(token in detail for token in ("token", "auth", "authorization", "access", "suspended", "account"))

'''


WEATHER_OVERLAY_METHOD = '''
    async def _apply_weather_overlay_contexts(
        self,
        matches: list[Match],
        contexts: dict[str, MatchContext],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        import os
        import httpx
        from app.providers.weather_common import WeatherContextEnricher

        stats: dict[str, Any] = {
            "enabled": False,
            "api_key_present": False,
            "matches_requested": 0,
            "contexts_built": 0,
            "requests": 0,
            "response_errors": 0,
            "cache_hits": 0,
            "weather_provider_hits": {},
        }
        preview: dict[str, Any] = {"sample_weather": []}

        raw_enabled = os.getenv("WEATHER_CONTEXT_ENABLED", "true").strip().lower()
        if raw_enabled not in {"1", "true", "yes", "on"}:
            stats["disabled_reason"] = "WEATHER_CONTEXT_ENABLED=false"
            return contexts, stats, preview

        has_key = bool(
            os.getenv("WEATHERAPI_KEY")
            or os.getenv("OPENWEATHERMAP_API_KEY")
            or os.getenv("OPENWEATHER_API_KEY")
            or os.getenv("OPENWEATHERMAP_KEY")
        )
        stats["enabled"] = True
        stats["api_key_present"] = has_key
        if not has_key:
            stats["disabled_reason"] = "missing_weather_api_key"
            return contexts, stats, preview

        def country_from_league(name: str) -> str:
            text = str(name or "").strip()
            if " - " in text:
                return text.split(" - ", 1)[0].strip()
            return ""

        def priority(match: Match) -> tuple[int, float, str]:
            tier = getattr(match, "tier", "mid")
            tier_rank = 0 if tier == "top" else 1 if tier == "mid" else 2
            try:
                distance = abs((match.commence_time - datetime.now(UTC)).total_seconds()) / 3600.0
            except Exception:
                distance = 999.0
            return (tier_rank, distance, str(match.home_team).lower())

        limit = max(0, int(os.getenv("WEATHER_CONTEXT_MATCH_LIMIT") or getattr(self.settings, "weather_context_match_limit", 8) or 8))
        candidates = [match for match in sorted(matches, key=priority) if match.match_key in contexts]
        if limit:
            candidates = candidates[:limit]
        stats["matches_requested"] = len(candidates)

        if not candidates:
            return contexts, stats, preview

        enricher = WeatherContextEnricher(self.settings)
        updated_contexts = dict(contexts)
        async with httpx.AsyncClient(timeout=float(os.getenv("WEATHER_TIMEOUT_SECONDS") or 8.0)) as client:
            for match in candidates:
                base_context = updated_contexts.get(match.match_key)
                if base_context is None:
                    continue
                fixture_stub = {
                    "fixture": {"venue": {"city": "", "name": ""}},
                    "league": {"country": country_from_league(getattr(match, "league_name", ""))},
                }
                try:
                    enriched_context, weather_stats = await enricher.enrich_context(client, match, fixture_stub, base_context)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_error"] = f"{type(exc).__name__}: {exc}"
                    continue

                stats["requests"] += int(weather_stats.get("requests", 0) or 0)
                stats["response_errors"] += int(weather_stats.get("response_errors", 0) or 0)
                if bool(weather_stats.get("cache_hit")):
                    stats["cache_hits"] += 1
                if bool(weather_stats.get("enriched")):
                    updated_contexts[match.match_key] = enriched_context
                    stats["contexts_built"] += 1
                    provider_name = str(weather_stats.get("provider") or "unknown")
                    stats["weather_provider_hits"][provider_name] = int(stats["weather_provider_hits"].get(provider_name, 0) or 0) + 1
                    details = dict(getattr(enriched_context, "details", {}) or {})
                    if len(preview["sample_weather"]) < 5:
                        preview["sample_weather"].append(
                            {
                                "match_key": match.match_key,
                                "home_team": match.home_team,
                                "away_team": match.away_team,
                                "league_name": match.league_name,
                                "provider": provider_name,
                                "query": details.get("weather_query"),
                                "condition": details.get("weather_condition"),
                                "temp_c": details.get("weather_temp_c"),
                                "wind_kph": details.get("weather_wind_kph"),
                                "precip_mm": details.get("weather_precip_mm"),
                                "factor": details.get("weather_total_factor"),
                                "reasons": details.get("weather_adjustment_reasons"),
                            }
                        )

        return updated_contexts, stats, preview

'''


def patch_api_football() -> bool:
    if not API_FOOTBALL_PATH.exists():
        print(f"skip: {API_FOOTBALL_PATH} not found")
        return False
    src = API_FOOTBALL_PATH.read_text(encoding="utf-8")
    original = src

    if "def _api_error_detail" not in src:
        marker = "    @staticmethod\n    def _has_rate_limit_error"
        if marker in src:
            src = src.replace(marker, API_ERROR_METHOD + marker, 1)

    pattern = re.compile(
        r"    @staticmethod\n    def _has_rate_limit_error\(payload: Any\).*?"
        r"    @staticmethod\n    def _has_auth_error\(payload: Any\).*?"
        r"        return False\n",
        flags=re.DOTALL,
    )
    src = pattern.sub(API_ERROR_METHODS_REPLACEMENT, src, count=1)

    old = '''                if self._has_auth_error(payload) or self._has_plan_error(payload):
                    stats["response_errors"] += 1
                    stats["auth_failed"] = True
'''
    new = '''                if self._has_auth_error(payload) or self._has_plan_error(payload):
                    stats["response_errors"] += 1
                    stats["auth_failed"] = True
                    detail = self._api_error_detail(payload)
                    if detail:
                        stats["auth_error_detail"] = detail[:500]
                        if "suspended" in detail.lower():
                            stats["access_suspended"] = True
                        if self._has_plan_error(payload):
                            stats["plan_limited"] = True
'''
    src = src.replace(old, new)

    if src != original:
        API_FOOTBALL_PATH.write_text(src, encoding="utf-8")
        print("patched: app/providers/api_football.py")
        return True
    print("already patched or no changes: app/providers/api_football.py")
    return False


def patch_runner_weather_overlay() -> bool:
    if not RUNNER_PATH.exists():
        print(f"skip: {RUNNER_PATH} not found")
        return False

    src = RUNNER_PATH.read_text(encoding="utf-8")
    original = src

    if "def _apply_weather_overlay_contexts" not in src:
        marker = "    async def run_once(self) -> dict[str, Any]:\n"
        if marker in src:
            src = src.replace(marker, WEATHER_OVERLAY_METHOD + "\n" + marker, 1)
        else:
            print("warn: run_once marker not found for weather overlay method")

    merge_line = "            contexts = self._merge_context_maps(*context_maps.values())\n"
    overlay_line = (
        "            contexts, weather_overlay_stats, weather_overlay_preview = "
        "await self._apply_weather_overlay_contexts(filtered_matches, contexts)\n"
    )
    if overlay_line not in src:
        if merge_line in src:
            src = src.replace(merge_line, merge_line + overlay_line, 1)
        else:
            print("warn: contexts merge line not found")

    if "'weather_overlay': weather_overlay_stats" not in src:
        marker = "                'api_football': api_football_stats,\n"
        if marker in src:
            src = src.replace(marker, marker + "                'weather_overlay': weather_overlay_stats,\n", 1)
        else:
            print("warn: source_stats api_football marker not found")

    if src != original:
        RUNNER_PATH.write_text(src, encoding="utf-8")
        print("patched: app/services/runner.py weather overlay")
        return True
    print("already patched or no changes: app/services/runner.py")
    return False


def main() -> int:
    patch_api_football()
    patch_runner_weather_overlay()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
