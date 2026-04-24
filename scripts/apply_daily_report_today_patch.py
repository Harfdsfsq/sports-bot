from __future__ import annotations

from pathlib import Path

TARGET = Path("app/state.py")


OLD_LINE = "        offset_days = max(0, int(getattr(settings, 'daily_report_target_offset_days', 1) or 1))"
NEW_BLOCK = """        # DAILY_REPORT_TARGET_OFFSET_DAYS=0 must mean today's local date.
        # Previous code used `value or 1`, so explicit 0 was converted to 1 and
        # Telegram daily report was sent for yesterday.
        raw_offset = getattr(settings, 'daily_report_target_offset_days', 0)
        if raw_offset in (None, ''):
            raw_offset = 0
        try:
            offset_days = max(0, int(raw_offset))
        except Exception:
            offset_days = 0"""


def main() -> int:
    if not TARGET.exists():
        print(f"skip: {TARGET} not found")
        return 0

    src = TARGET.read_text(encoding="utf-8")
    original = src

    if OLD_LINE in src:
        src = src.replace(OLD_LINE, NEW_BLOCK, 1)
    elif "daily_report_target_offset_days" in src and "explicit 0 was converted to 1" in src:
        print(f"already patched: {TARGET}")
        return 0
    else:
        print("warn: expected daily_report offset line not found")
        return 0

    if src != original:
        TARGET.write_text(src, encoding="utf-8")
        print(f"patched: {TARGET}")
    else:
        print(f"no changes: {TARGET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
