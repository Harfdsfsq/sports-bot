from __future__ import annotations

from pathlib import Path

TARGET = Path("app/services/telegram_i18n.py")


def main() -> int:
    # No-op by design.
    # Older versions of the workflow used this script to generate/overwrite telegram_i18n.py
    # at runtime. That caused the curated Russian i18n dictionary to be replaced by an older
    # partial version. The i18n file is now committed directly and should not be regenerated.
    if TARGET.exists():
        print(f"telegram_i18n.py already exists: {TARGET}; keeping committed version")
    else:
        print(f"telegram_i18n.py is missing: {TARGET}; no runtime generation performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
