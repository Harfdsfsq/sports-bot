from __future__ import annotations

import asyncio
import sys

from app.cli import _main

if __name__ == '__main__':
    sys.argv = ['reporting_sqlite.py', 'reporting-sqlite']
    raise SystemExit(asyncio.run(_main()))
