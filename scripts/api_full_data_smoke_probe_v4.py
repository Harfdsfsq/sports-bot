from __future__ import annotations

"""Compatibility shim.

provider_smoke_fast imports v4.  The implementation is routed through the
quota-safe v5 wrapper so existing workflow code does not burn the odds-api.io
hourly quota while probing extra endpoints.
"""

from scripts.api_full_data_smoke_probe_v5 import main, run

__all__ = ["run", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
