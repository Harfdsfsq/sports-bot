from __future__ import annotations

# Wrapper kept intentionally small: import the restored builder and add the new
# evidence/integrity patch diagnostic line without changing Telegram sending.
import importlib.util
from pathlib import Path

_BASE = Path(__file__).with_name('build_detailed_run_report_restored.py')
if not _BASE.exists():
    # First deployment after earlier simplification: fall back to the current
    # generated implementation copied by the previous commit if the restored file
    # has not been split yet.
    _BASE = Path(__file__).with_name('build_detailed_run_report_legacy.py')

if not _BASE.exists():
    raise SystemExit('missing detailed report base builder')

spec = importlib.util.spec_from_file_location('harizon_detailed_report_base', _BASE)
module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
assert spec and spec.loader
spec.loader.exec_module(module)

if __name__ == '__main__':
    raise SystemExit(module.main())
