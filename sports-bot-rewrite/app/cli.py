import asyncio
import json
import sys

from app.config import get_settings
from app.services.runner import PredictionRunner


async def _main() -> int:
    settings = get_settings()
    runner = PredictionRunner(settings)
    if len(sys.argv) >= 2 and sys.argv[1] == 'run-once':
        summary = await runner.run_once()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print('Usage: python -m app.cli run-once')
    return 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(_main()))
