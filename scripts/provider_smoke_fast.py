from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _arg_value(name: str) -> str | None:
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return None
    return sys.argv[index + 1]


def _set_or_replace_arg(name: str, value: str) -> None:
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            sys.argv[index + 1] = value
        else:
            sys.argv.append(value)
    else:
        sys.argv.extend([name, value])


try:
    current_timeout = float(_arg_value("--timeout") or os.getenv("PROVIDER_SMOKE_FAST_TIMEOUT") or "0")
except Exception:
    current_timeout = 0.0
if current_timeout < 18.0:
    _set_or_replace_arg("--timeout", "18")

if _arg_value("--repeats") is None and not os.getenv("PROVIDER_SMOKE_REPEATS"):
    _set_or_replace_arg("--repeats", "2")

from scripts.provider_smoke_diagnostics_v3 import main


if __name__ == "__main__":
    raise SystemExit(main())
