from __future__ import annotations

"""Guarded fallback v20.

Adds A-cover evidence-quality handling on top of v19 preflight/v18 guards,
persists Telegram-confirmed controlled fallback picks into the durable ledger,
and patches total-xG direction sanity so probability-supported totals are not
rejected by a raw total-xG-vs-line heuristic alone.
"""

import scripts.publish_controlled_fallback_guarded_v19 as v19


def main() -> int:
    v19.run_preflight()
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
    return code


if __name__ == "__main__":
    raise SystemExit(main())
