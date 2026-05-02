from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_daily_ops_report() -> bool:
    path = ROOT / "scripts" / "build_daily_ops_report.py"
    if not path.exists():
        return False
    src = path.read_text(encoding="utf-8")
    original = src

    import_block = """try:\n    from app.services.telegram_i18n import (\n        normalize_telegram_text,\n        translate_league_name,\n        translate_selection_text,\n        translate_team_name,\n    )\nexcept Exception:\n"""
    replacement_block = """try:\n    from app.services.telegram_i18n import (\n        normalize_telegram_text,\n        translate_league_name,\n        translate_selection_text,\n        translate_team_name,\n    )\n    from app.services.telegram_market_display import market_display_from_mapping\nexcept Exception:\n"""
    if import_block in src and "telegram_market_display" not in src:
        src = src.replace(import_block, replacement_block, 1)

    fallback_marker = """    def translate_team_name(name: Any) -> str: return str(name or \"\")\n"""
    fallback_replacement = fallback_marker + """    def market_display_from_mapping(row: dict[str, Any], *, include_family: bool = True) -> str:\n        return translate_selection_text(row.get(\"selection\") or row.get(\"market\") or \"\", row.get(\"home_team\"), row.get(\"away_team\"))\n"""
    if fallback_marker in src and "def market_display_from_mapping" not in src:
        src = src.replace(fallback_marker, fallback_replacement, 1)

    old = """    selection = translate_selection_text(bet.get(\"selection\") or bet.get(\"market\") or \"\", bet.get(\"home_team\"), bet.get(\"away_team\"))\n"""
    new = """    selection = market_display_from_mapping(bet, include_family=True)\n"""
    if old in src:
        src = src.replace(old, new, 1)

    if src != original:
        path.write_text(src, encoding="utf-8")
        return True
    return False


def patch_bankroll_report() -> bool:
    path = ROOT / "scripts" / "send_bankroll_report_block.py"
    if not path.exists():
        return False
    src = path.read_text(encoding="utf-8")
    original = src

    if "from app.services.telegram_market_display import market_display_from_mapping" not in src:
        marker = "from urllib import parse, request\n"
        src = src.replace(marker, marker + "\ntry:\n    from app.services.telegram_market_display import market_display_from_mapping\n    from app.services.telegram_i18n import translate_team_name\nexcept Exception:\n    def market_display_from_mapping(row, *, include_family=True): return str((row or {}).get('selection') or '')\n    def translate_team_name(name): return str(name or '')\n", 1)

    old = """        home = str(row.get('home_team') or '').strip()\n        away = str(row.get('away_team') or '').strip()\n        selection = str(row.get('selection') or '').strip()\n        match = f'{home} — {away}'.strip(' —') or str(row.get('match_key') or 'матч')\n"""
    new = """        home = translate_team_name(str(row.get('home_team') or '').strip())\n        away = translate_team_name(str(row.get('away_team') or '').strip())\n        selection = market_display_from_mapping(row, include_family=True).strip()\n        match = f'{home} — {away}'.strip(' —') or str(row.get('match_key') or 'матч')\n"""
    if old in src:
        src = src.replace(old, new, 1)

    if src != original:
        path.write_text(src, encoding="utf-8")
        return True
    return False


def main() -> int:
    result = {
        "daily_ops_report": patch_daily_ops_report(),
        "bankroll_report": patch_bankroll_report(),
    }
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
