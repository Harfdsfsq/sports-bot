from __future__ import annotations


def main() -> int:
    try:
        import scripts.publish_controlled_fallback_guarded_v18 as v18
        from scripts.patch_reserved_slot_expiring_candidate import install as install_reserved_slot_expiry_override
        from scripts.patch_daily_slot_bundle_cap import install as install_daily_slot_bundle_cap
        install_reserved_slot_expiry_override(v18)
        install_daily_slot_bundle_cap(v18)
    except Exception:
        pass
    from scripts.publish_controlled_fallback_guarded_v20 import main as v20_main
    return int(v20_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
