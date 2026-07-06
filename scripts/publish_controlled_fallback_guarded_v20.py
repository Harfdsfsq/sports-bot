from __future__ import annotations

import scripts.publish_controlled_fallback_guarded_v19 as v19


def main() -> int:
    v19.run_preflight()
    try:
        from scripts.apply_fallback_price_floor import main as apply_price_floor
        apply_price_floor()
    except Exception:
        pass
    import scripts.publish_controlled_fallback_guarded_v18 as v18
    from scripts.patch_controlled_fallback_duplicate_matching import install as install_duplicate_matcher
    from scripts.patch_a_cover_evidence_quality import install as install_a_cover_evidence_quality
    from scripts.patch_xg_sanity_probability_support import install as install_xg_probability_support

    install_duplicate_matcher(v18)
    install_a_cover_evidence_quality(v18.base)
    install_xg_probability_support(v18.base)
    code = int(v18.main() or 0)
    try:
        from scripts.sync_run_report_ledger_export import main as sync_run_ledger
        sync_run_ledger()
    except Exception:
        pass
    try:
        from scripts.sync_publication_ledger import main as sync_publication_ledger
        sync_publication_ledger()
    except Exception:
        pass
    try:
        from scripts.sync_controlled_fallback_selected_to_ledger import main as sync_selected_fallback
        sync_selected_fallback()
    except Exception:
        pass
    try:
        from scripts.sync_run_report_ledger_export import main as sync_run_ledger
        sync_run_ledger()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
