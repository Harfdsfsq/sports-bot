from __future__ import annotations

from pathlib import Path

PATH = Path('app/providers/oddspapi.py')

MARKER = '''            stats["events_matched"] = len(matched)
            tournament_ids: list[str] = []
'''

PATCHED = '''            stats["events_matched"] = len(matched)
            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            for item in matched.values():
                row = item["row"]
                match = item["match"]
                direct = row.get("bookmakers") if isinstance(row.get("bookmakers"), dict) else {}
                if not direct:
                    continue
                for book_name in direct.keys():
                    parsed = self._parse_fixture_odds(row, match, str(book_name or "oddspapi"))
                    if parsed:
                        offers_by_match[match.match_key].extend(parsed)
                        stats["offers_parsed"] += len(parsed)
            if offers_by_match:
                output = {k: v for k, v in offers_by_match.items() if v}
                self._write_cache(output)
                return output, stats, preview
            if bool(getattr(self, "rapidapi_enabled", False)):
                stats["rapidapi_no_inline_bookmakers"] = True
                stats["tournament_endpoint_skipped"] = True
                stats["last_body_preview"] = "RapidAPI fixtures matched but contain no inline bookmakers; skipping legacy /odds-by-tournaments endpoint."
                output: dict[str, list[Offer]] = {}
                self._write_cache(output)
                return output, stats, preview

            tournament_ids: list[str] = []
'''


def main() -> int:
    if not PATH.exists():
        return 0
    src = PATH.read_text(encoding='utf-8')
    if 'rapidapi_no_inline_bookmakers' in src:
        print('already patched')
        return 0
    if 'direct = row.get("bookmakers") if isinstance(row.get("bookmakers"), dict) else {}' in src and 'tournament_ids: list[str] = []' in src:
        src = src.replace(
            '''            if offers_by_match:
                output = {k: v for k, v in offers_by_match.items() if v}
                self._write_cache(output)
                return output, stats, preview

            tournament_ids: list[str] = []
''',
            '''            if offers_by_match:
                output = {k: v for k, v in offers_by_match.items() if v}
                self._write_cache(output)
                return output, stats, preview
            if bool(getattr(self, "rapidapi_enabled", False)):
                stats["rapidapi_no_inline_bookmakers"] = True
                stats["tournament_endpoint_skipped"] = True
                stats["last_body_preview"] = "RapidAPI fixtures matched but contain no inline bookmakers; skipping legacy /odds-by-tournaments endpoint."
                output: dict[str, list[Offer]] = {}
                self._write_cache(output)
                return output, stats, preview

            tournament_ids: list[str] = []
''',
            1,
        )
        PATH.write_text(src, encoding='utf-8')
        print('patched inline offers rapidapi skip')
        return 0
    if MARKER not in src:
        print('marker not found')
        return 0
    PATH.write_text(src.replace(MARKER, PATCHED, 1), encoding='utf-8')
    print('patched inline offers')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
