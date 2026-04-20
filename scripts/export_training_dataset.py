from __future__ import annotations

import asyncio
import sys

from app.cli import _main

if __name__ == '__main__':
    sys.argv = ['export_training_dataset.py', 'training-dataset']
    raise SystemExit(asyncio.run(_main()))
