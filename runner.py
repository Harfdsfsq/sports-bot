from __future__ import annotations

from collections.abc import Sequence

from app.cli import main_sync
from app.services.runner import PredictionRunner

__all__ = ['PredictionRunner', 'main_sync', 'run']


def run(argv: Sequence[str] | None = None) -> int:
    return main_sync(argv)


if __name__ == '__main__':
    raise SystemExit(run())
