from __future__ import annotations

import asyncio
import sys

from app.cli import _main

if __name__ == '__main__':
    sys.argv = ['coverage_audit.py', 'coverage-audit']
    raise SystemExit(asyncio.run(_main()))
