from __future__ import annotations

"""Guarded fallback v20.

Adds A-cover evidence-quality handling on top of v19 preflight/v18 guards.
"""

import scripts.publish_controlled_fallback_guarded_v19 as v19


def main() -> int:
    v19.run_preflight()
    import scripts.publish_controlled_fallback_guarded_v18 as v18
    from scripts.patch_controlled_fallback_duplicate_matching import install as install_duplicate_matcher
    from scripts.patch_a_cover_evidence_quality import install as install_a_cover_evidence_quality

    install_duplicate_matcher(v18)
    install_a_cover_evidence_quality(v18.base)
    code = int(v18.main() or 0)
    try:
        from scripts.sync_run_report_ledger_export import main as sync_run_ledger
        sync_run_ledger()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
