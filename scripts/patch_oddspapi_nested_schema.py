from __future__ import annotations

from pathlib import Path

PATH = Path('app/providers/oddspapi.py')

FETCH_HOME_OLD = '''            home = str(row.get("homeTeam") or row.get("home_name") or row.get("home") or "").strip()
            away = str(row.get("awayTeam") or row.get("away_name") or row.get("away") or "").strip()
            league = str(row.get("tournamentName") or row.get("leagueName") or row.get("league") or "").strip()
            raw_time = row.get("startsAt") or row.get("startTime") or row.get("date")
'''

FETCH_HOME_NEW = '''            participants = row.get("participants") if isinstance(row.get("participants"), dict) else {}
            tournament = row.get("tournament") if isinstance(row.get("tournament"), dict) else {}
            home = str(
                participants.get("participant1Name")
                or row.get("participant1Name")
                or row.get("homeTeam")
                or row.get("home_name")
                or row.get("home")
                or ""
            ).strip()
            away = str(
                participants.get("participant2Name")
                or row.get("participant2Name")
                or row.get("awayTeam")
                or row.get("away_name")
                or row.get("away")
                or ""
            ).strip()
            league = str(
                tournament.get("tournamentName")
                or row.get("tournamentName")
                or row.get("leagueName")
                or row.get("league")
                or ""
            ).strip()
            raw_time = row.get("startsAt") or row.get("startTime") or row.get("date")
'''

PARSE_COMMENCE_OLD = '''            try:
                commence = parse_datetime(str(raw_time))
            except Exception:
                continue
'''

PARSE_COMMENCE_NEW = '''            try:
                commence = self._parse_oddspapi_time(raw_time)
            except Exception:
                continue
'''

TOURNAMENT_OLD = '''                tid = str(row.get("tournamentId") or "")
                if tid and tid not in tournament_ids:
                    tournament_ids.append(tid)
'''

TOURNAMENT_NEW = '''                tournament = row.get("tournament") if isinstance(row.get("tournament"), dict) else {}
                tid = str(row.get("tournamentId") or tournament.get("tournamentId") or "")
                if tid and tid not in tournament_ids:
                    tournament_ids.append(tid)
'''

MATCH_HOME_OLD = '''        home = str(
            row.get("participant1Name")
            or row.get("homeTeam")
            or row.get("home_name")
            or row.get("home")
            or ""
        ).strip()
        away = str(
            row.get("participant2Name")
            or row.get("awayTeam")
            or row.get("away_name")
            or row.get("away")
            or ""
        ).strip()
        league = str(row.get("tournamentName") or row.get("leagueName") or row.get("league") or "").strip()
        if not home or not away:
            return None
        try:
            start = parse_datetime(row.get("startsAt") or row.get("startTime") or row.get("date"))
        except Exception:
            return None
'''

MATCH_HOME_NEW = '''        participants = row.get("participants") if isinstance(row.get("participants"), dict) else {}
        tournament = row.get("tournament") if isinstance(row.get("tournament"), dict) else {}
        home = str(
            participants.get("participant1Name")
            or row.get("participant1Name")
            or row.get("homeTeam")
            or row.get("home_name")
            or row.get("home")
            or ""
        ).strip()
        away = str(
            participants.get("participant2Name")
            or row.get("participant2Name")
            or row.get("awayTeam")
            or row.get("away_name")
            or row.get("away")
            or ""
        ).strip()
        league = str(
            tournament.get("tournamentName")
            or row.get("tournamentName")
            or row.get("leagueName")
            or row.get("league")
            or ""
        ).strip()
        if not home or not away:
            return None
        try:
            start = self._parse_oddspapi_time(row.get("startsAt") or row.get("startTime") or row.get("date"))
        except Exception:
            return None
'''

HELPER_MARKER = '''    def _fixture_params(self, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
'''

HELPER_BLOCK = '''    @staticmethod
    def _parse_oddspapi_time(value: Any) -> datetime:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            raw = float(value)
            if raw > 10_000_000_000:
                raw = raw / 1000.0
            return datetime.fromtimestamp(raw, tz=UTC)
        dt = parse_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

'''

PARSE_INSERT_MARKER = '''        for market_key, market_value in markets.items():
'''

PARSE_INLINE_BLOCK = '''        direct_bookmakers = row.get("bookmakers") if isinstance(row.get("bookmakers"), dict) else {}
        if direct_bookmakers:
            for raw_book_name, market_list in direct_bookmakers.items():
                book_name = str(raw_book_name or bookmaker_slug or "Oddspapi")
                if not isinstance(market_list, list):
                    continue
                for market in market_list:
                    if not isinstance(market, dict):
                        continue
                    name = str(market.get("name") or "").strip().lower()
                    for odds_row in market.get("odds") or []:
                        if not isinstance(odds_row, dict):
                            continue
                        point = odds_row.get("hdp")
                        if name in {"ml", "moneyline", "match odds"}:
                            add("h2h", match.home_team, odds_row.get("home"))
                            add("h2h", "Draw", odds_row.get("draw"))
                            add("h2h", match.away_team, odds_row.get("away"))
                        elif "draw no bet" in name:
                            add("dnb", match.home_team, odds_row.get("home"))
                            add("dnb", match.away_team, odds_row.get("away"))
                        elif "both teams to score" in name or "btts" in name:
                            add("btts", "Yes", odds_row.get("yes"))
                            add("btts", "No", odds_row.get("no"))
                        elif "total" in name or "goals over/under" in name:
                            add("totals", "Over", odds_row.get("over"), as_float(point, 0.0) if point not in (None, "") else None)
                            add("totals", "Under", odds_row.get("under"), as_float(point, 0.0) if point not in (None, "") else None)
                        elif "spread" in name or "handicap" in name:
                            add("spreads", match.home_team, odds_row.get("home"), as_float(point, 0.0) if point not in (None, "") else None)
                            add("spreads", match.away_team, odds_row.get("away"), as_float(point, 0.0) if point not in (None, "") else None)
            if offers:
                return offers

'''


def main() -> int:
    if not PATH.exists():
        print('skip: target missing')
        return 0
    src = PATH.read_text(encoding='utf-8')
    original = src
    src = src.replace(FETCH_HOME_OLD, FETCH_HOME_NEW, 1)
    src = src.replace(PARSE_COMMENCE_OLD, PARSE_COMMENCE_NEW, 1)
    src = src.replace(TOURNAMENT_OLD, TOURNAMENT_NEW, 1)
    src = src.replace(MATCH_HOME_OLD, MATCH_HOME_NEW, 1)
    if 'def _parse_oddspapi_time(' not in src and HELPER_MARKER in src:
        src = src.replace(HELPER_MARKER, HELPER_BLOCK + HELPER_MARKER, 1)
    if 'direct_bookmakers = row.get("bookmakers")' not in src and PARSE_INSERT_MARKER in src:
        src = src.replace(PARSE_INSERT_MARKER, PARSE_INLINE_BLOCK + PARSE_INSERT_MARKER, 1)
    if src != original:
        PATH.write_text(src, encoding='utf-8')
        print('patched: oddspapi nested schema')
    else:
        print('already patched or no changes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
