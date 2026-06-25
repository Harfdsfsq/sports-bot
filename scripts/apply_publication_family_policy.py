from __future__ import annotations

# Compatibility wrapper. The active runtime contract is maintained in
# scripts/apply_ab_tier_bookmaker_contract.py so repeated workflow calls do not
# revert the controlled A/B mode.

from scripts.apply_ab_tier_bookmaker_contract import main


if __name__ == '__main__':
    raise SystemExit(main())
