__all__ = []

try:
    from app.providers import bzzoiro_v2_date_window_patch as _bzzoiro_v2_date_window_patch
    _bzzoiro_v2_date_window_patch.install()
except Exception:
    pass

try:
    from app.providers import bzzoiro_v2_odds_comparison_patch as _bzzoiro_v2_odds_comparison_patch
    _bzzoiro_v2_odds_comparison_patch.install()
except Exception:
    pass
