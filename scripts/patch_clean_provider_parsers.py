from __future__ import annotations

from pathlib import Path

PATCH_VERSION = 'v1-clean-provider-live-payload-parsers'


def patch_bzzoiro() -> bool:
    path = Path('app/providers/bzzoiro_v2.py')
    text = path.read_text(encoding='utf-8')
    original = text

    text = text.replace(
        'start = parse_datetime(event.get("event_date"))',
        'start = parse_datetime(event.get("event_date") or event.get("date") or event.get("startTime") or event.get("commence_time"))',
    )
    text = text.replace(
        '"line", "point", "price", "odds", "decimal", "decimal_odds", "bookmaker"',
        '"line", "point", "price", "odds", "decimal", "decimal_odds", "value", "bookmaker"',
    )
    text = text.replace(
        'if any(k in row for k in ("price", "odds", "decimal", "decimal_odds"))',
        'if any(k in row for k in ("price", "odds", "decimal", "decimal_odds", "value"))',
    )
    text = text.replace(
        'return cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds"))',
        'return cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds") or row.get("value"))',
    )
    text = text.replace(
        'cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds"))',
        'cls._to_float(row.get("price") or row.get("odds") or row.get("decimal") or row.get("decimal_odds") or row.get("value"))',
    )
    old_team_candidates = '''    @staticmethod
    def _team_candidates(event: dict[str, Any]) -> tuple[list[str], list[str]]:
        def names(prefix: str) -> list[str]:
            out: list[str] = []
            direct = event.get(f"{prefix}_team")
            if isinstance(direct, str) and direct.strip():
                out.append(direct.strip())
            obj = event.get(f"{prefix}_team_obj")
            if isinstance(obj, dict):
                for key in ("name", "short_name", "display_name"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
            return list(dict.fromkeys(out))
        return names("home"), names("away")
'''
    new_team_candidates = '''    @staticmethod
    def _team_candidates(event: dict[str, Any]) -> tuple[list[str], list[str]]:
        def names(prefix: str) -> list[str]:
            out: list[str] = []
            for key in (
                f"{prefix}_team", f"{prefix}Team", f"{prefix}_name", f"{prefix}Name",
                prefix, "team1" if prefix == "home" else "team2",
                "participant1Name" if prefix == "home" else "participant2Name",
            ):
                value = event.get(key)
                if isinstance(value, dict):
                    value = value.get("name") or value.get("Name") or value.get("displayName")
                if isinstance(value, str) and value.strip():
                    out.append(value.strip())
            obj = event.get(f"{prefix}_team_obj") or event.get(f"{prefix}TeamObj")
            if isinstance(obj, dict):
                for key in ("name", "Name", "short_name", "shortName", "display_name", "displayName"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
            return list(dict.fromkeys(out))
        return names("home"), names("away")
'''
    if old_team_candidates in text:
        text = text.replace(old_team_candidates, new_team_candidates, 1)

    old_league = '''    @staticmethod
    def _league_name(event: dict[str, Any]) -> str:
        league = event.get("league")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("league_name") or "")
        return str(event.get("league_name") or event.get("league") or "")
'''
    new_league = '''    @staticmethod
    def _league_name(event: dict[str, Any]) -> str:
        league = event.get("league") or event.get("competition") or event.get("tournament")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or league.get("league_name") or league.get("displayName") or "")
        return str(event.get("league_name") or event.get("leagueName") or event.get("competitionName") or event.get("tournamentName") or event.get("league") or "")
'''
    if old_league in text:
        text = text.replace(old_league, new_league, 1)

    # Make odds-market names from Bzzoiro v2 usable when market is encoded as marketId.
    marker = '    @classmethod\n    def _total_price(cls, rows: list[dict[str, Any]], line: float, *, over: bool) -> float | None:\n'
    if 'def _market_label_from_row' not in text and marker in text:
        helper = '''    @staticmethod
    def _market_label_from_row(row: dict[str, Any]) -> str:
        market = str(row.get("market") or row.get("market_key") or row.get("market_name") or row.get("marketName") or "")
        market_id = str(row.get("marketId") or row.get("market_id") or "").strip()
        mapping = {"1": "1x2", "5": "totals", "16": "totals", "17": "totals", "18": "totals"}
        return market or mapping.get(market_id, "")

'''
        text = text.replace(marker, helper + marker, 1)
    text = text.replace(
        'market = cls._norm(row.get("market") or row.get("market_key") or row.get("market_name"))',
        'market = cls._norm(cls._market_label_from_row(row))',
    )

    if text != original:
        path.write_text(text, encoding='utf-8')
        return True
    return False


def patch_sstats() -> bool:
    path = Path('app/providers/sstats_v1.py')
    text = path.read_text(encoding='utf-8')
    original = text
    if 'import math\n' not in text:
        text = text.replace('import os\n', 'import os\nimport math\n', 1)

    text = text.replace(
        'home_prob = self._first_float(row, ["HomeWinProbability", "homeWinProbability", "probHome", "Winner1Probability"])\n        away_prob = self._first_float(row, ["AwayWinProbability", "awayWinProbability", "probAway", "Winner2Probability"])',
        'home_prob = self._first_float(row, ["HomeWinProbability", "homeWinProbability", "probHome", "Winner1Probability"])\n        away_prob = self._first_float(row, ["AwayWinProbability", "awayWinProbability", "probAway", "Winner2Probability"])\n        if home_prob is None or away_prob is None:\n            h2h_home, h2h_away = self._h2h_probs_from_odds(row)\n            home_prob = home_prob if home_prob is not None else h2h_home\n            away_prob = away_prob if away_prob is not None else h2h_away',
    )
    text = text.replace(
        'expected_home = home_xg if home_xg is not None else float(hg) if hg is not None else None\n        expected_away = away_xg if away_xg is not None else float(ag) if ag is not None else None',
        'expected_home = home_xg if home_xg is not None else float(hg) if hg is not None else None\n        expected_away = away_xg if away_xg is not None else float(ag) if ag is not None else None\n        if expected_home is not None and expected_away is not None and (expected_home + expected_away) <= 0.25:\n            expected_home = None\n            expected_away = None\n        if expected_home is None or expected_away is None:\n            total_lambda = self._total_lambda_from_odds(row)\n            if total_lambda is not None:\n                share = 0.5\n                if home_prob is not None and away_prob is not None and (home_prob + away_prob) > 0:\n                    share = clamp(home_prob / (home_prob + away_prob), 0.28, 0.72)\n                expected_home = expected_home if expected_home is not None else total_lambda * share\n                expected_away = expected_away if expected_away is not None else total_lambda * (1.0 - share)',
    )

    old_league = '''    @staticmethod
    def _league(row: dict[str, Any]) -> str:
        league = row.get("league") or row.get("League")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or "")
        return str(row.get("leagueName") or row.get("LeagueName") or row.get("league") or "")
'''
    new_league = '''    @staticmethod
    def _league(row: dict[str, Any]) -> str:
        league = row.get("league") or row.get("League")
        if isinstance(league, dict):
            return str(league.get("name") or league.get("Name") or "")
        season = row.get("season")
        if isinstance(season, dict):
            league_obj = season.get("league") or season.get("League")
            if isinstance(league_obj, dict):
                return str(league_obj.get("name") or league_obj.get("Name") or "")
        return str(row.get("leagueName") or row.get("LeagueName") or row.get("league") or "")
'''
    if old_league in text:
        text = text.replace(old_league, new_league, 1)

    text = text.replace(
        'f"{side}Goals", f"{side.capitalize()}Goals", f"{side}Score", f"{side.capitalize()}Score",',
        'f"{side}Goals", f"{side.capitalize()}Goals", f"{side}Score", f"{side.capitalize()}Score", f"{side}Result", f"{side.capitalize()}Result", f"{side}FTResult", f"{side.capitalize()}FTResult",',
    )

    insert_before = '    @staticmethod\n    def _rows(payload: Any) -> list[dict[str, Any]]:\n'
    if 'def _h2h_probs_from_odds' not in text and insert_before in text:
        helpers = '''    @classmethod
    def _h2h_probs_from_odds(cls, row: dict[str, Any]) -> tuple[float | None, float | None]:
        home = draw = away = None
        for market in row.get("odds") or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("marketId") or market.get("id") or "")
            market_name = str(market.get("marketName") or market.get("name") or "").lower()
            if market_id not in {"1"} and "1x2" not in market_name and "winner" not in market_name:
                continue
            for outcome in market.get("odds") or market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                name = str(outcome.get("name") or outcome.get("outcome") or "").strip().lower()
                value = cls._float_any(outcome.get("value") or outcome.get("price") or outcome.get("odds"))
                if value is None or value <= 1.0:
                    continue
                if name in {"home", "1"}:
                    home = value
                elif name in {"draw", "x"}:
                    draw = value
                elif name in {"away", "2"}:
                    away = value
        if home and away:
            inv_home = 1.0 / home
            inv_draw = 1.0 / draw if draw and draw > 1.0 else 0.0
            inv_away = 1.0 / away
            total = inv_home + inv_draw + inv_away
            if total > 0:
                return clamp(inv_home / total, 0.02, 0.96), clamp(inv_away / total, 0.02, 0.96)
        return None, None

    @classmethod
    def _total_lambda_from_odds(cls, row: dict[str, Any]) -> float | None:
        over = under = None
        for market in row.get("odds") or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("marketId") or market.get("id") or "")
            market_name = str(market.get("marketName") or market.get("name") or "").lower()
            if market_id not in {"5"} and "2.5" not in market_name and "total" not in market_name and "over" not in market_name:
                continue
            for outcome in market.get("odds") or market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                name = str(outcome.get("name") or outcome.get("outcome") or "").strip().lower()
                if "2.5" not in name and market_id != "5":
                    continue
                value = cls._float_any(outcome.get("value") or outcome.get("price") or outcome.get("odds"))
                if value is None or value <= 1.0:
                    continue
                if "over" in name:
                    over = value
                elif "under" in name:
                    under = value
        if over and under:
            inv_over = 1.0 / over
            inv_under = 1.0 / under
            prob_over = inv_over / (inv_over + inv_under)
        elif over:
            prob_over = 1.0 / over
        else:
            return None
        return cls._infer_total_lambda(clamp(prob_over, 0.01, 0.99), 2.5)

    @staticmethod
    def _infer_total_lambda(over_probability: float, line: float) -> float | None:
        target = clamp(float(over_probability), 0.01, 0.99)
        lo, hi = 0.2, 6.0
        k = int(math.floor(line))
        for _ in range(36):
            mid = (lo + hi) / 2.0
            cdf = math.exp(-mid) * sum((mid ** i) / math.factorial(i) for i in range(k + 1))
            over_mid = 1.0 - cdf
            if over_mid < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    @staticmethod
    def _float_any(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(str(value).replace(",", "."))
        except Exception:
            return None

'''
        text = text.replace(insert_before, helpers + insert_before, 1)

    if text != original:
        path.write_text(text, encoding='utf-8')
        return True
    return False


def patch_clean_system() -> bool:
    path = Path('scripts/apply_clean_runtime_system.py')
    text = path.read_text(encoding='utf-8')
    original = text
    if 'import re\n' not in text:
        text = text.replace('import os\n', 'import os\nimport re\n', 1)
    old = '''def patch_runner_provider(module_from: str, module_to: str, attr: str, cls: str) -> bool:
    path = ROOT / 'app' / 'services' / 'runner.py'
    text = path.read_text(encoding='utf-8')
    old = f"self.{attr} = self._safe_provider('{module_from}', '{cls}')"
    new = f"self.{attr} = self._safe_provider('{module_to}', '{cls}')"
    if new in text:
        return False
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    return True
'''
    new = '''def patch_runner_provider(module_from: str, module_to: str, attr: str, cls: str) -> bool:
    path = ROOT / 'app' / 'services' / 'runner.py'
    text = path.read_text(encoding='utf-8')
    new = f"self.{attr} = self._safe_provider('{module_to}', '{cls}')"
    if new in text:
        return False
    pattern = rf"self\\.{re.escape(attr)}\\s*=\\s*self\\._safe_provider\\([^\\n]+\\)"
    updated, count = re.subn(pattern, new, text, count=1)
    if count <= 0:
        old = f"self.{attr} = self._safe_provider('{module_from}', '{cls}')"
        if old not in text:
            return False
        updated = text.replace(old, new, 1)
    path.write_text(updated, encoding='utf-8')
    return True
'''
    if old in text:
        text = text.replace(old, new, 1)
    if text != original:
        path.write_text(text, encoding='utf-8')
        return True
    return False


def main() -> int:
    changed = {
        'bzzoiro_v2': patch_bzzoiro(),
        'sstats_v1': patch_sstats(),
        'clean_runtime_system': patch_clean_system(),
    }
    print({'patch': PATCH_VERSION, 'changed': changed})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
