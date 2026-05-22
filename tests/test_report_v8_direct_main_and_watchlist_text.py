
from pathlib import Path


def test_v8_report_has_direct_main_guard():
    src = Path("scripts/send_harizon_telegram_run_report_v8.py").read_text(encoding="utf-8")
    assert "def main() -> int:" in src
    assert "payload = build_payload()" in src
    assert "text = render(payload)" in src
    assert "direct_v8_main" in src
    assert "raise SystemExit(main())" in src
    assert "raise SystemExit(v7.v5.main())" not in src


def test_fallback_watchlist_separates_prices_and_sources():
    src = Path("scripts/publish_controlled_fallback.py").read_text(encoding="utf-8")
    assert "odds_sources = int(metrics.get('odds_sources_count')" in src
    assert "контекст {context_confirmations}" in src
    assert "линий {int(metrics.get('books_count')" not in src
