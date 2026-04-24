from __future__ import annotations

from pathlib import Path

STATE_TARGET = Path("app/state.py")
RUNNER_TARGET = Path("app/services/runner.py")

REPLACEMENTS: list[tuple[Path, str, str]] = [
    (
        STATE_TARGET,
        "        offset_days = max(0, int(getattr(settings, 'daily_report_target_offset_days', 1) or 1))",
        """        # DAILY_REPORT_TARGET_OFFSET_DAYS=0 must mean today's local date.
        # Do not use `value or default`: explicit 0 is a valid setting.
        raw_offset = getattr(settings, 'daily_report_target_offset_days', 0)
        if raw_offset in (None, ''):
            raw_offset = 0
        try:
            offset_days = max(0, int(raw_offset))
        except Exception:
            offset_days = 0""",
    ),
    (
        STATE_TARGET,
        "        report_hour = min(23, max(0, int(getattr(settings, 'daily_report_hour_local', 8) or 8)))",
        """        # DAILY_REPORT_HOUR_LOCAL=0 must mean midnight.
        # Do not use `value or default`: explicit 0 is a valid setting.
        raw_report_hour = getattr(settings, 'daily_report_hour_local', 8)
        if raw_report_hour in (None, ''):
            raw_report_hour = 8
        try:
            report_hour = min(23, max(0, int(raw_report_hour)))
        except Exception:
            report_hour = 8""",
    ),
    (
        RUNNER_TARGET,
        "        report_hour_local = min(23, max(0, int(getattr(self.settings, 'daily_report_hour_local', 22) or 22)))",
        """        # DAILY_REPORT_HOUR_LOCAL=0 must mean midnight.
        # Do not use `value or default`: explicit 0 is a valid setting.
        raw_report_hour = getattr(self.settings, 'daily_report_hour_local', 22)
        if raw_report_hour in (None, ''):
            raw_report_hour = 22
        try:
            report_hour_local = min(23, max(0, int(raw_report_hour)))
        except Exception:
            report_hour_local = 22""",
    ),
]


def apply_replacements(path: Path, replacements: list[tuple[str, str]]) -> bool:
    if not path.exists():
        print(f"skip: {path} not found")
        return False

    src = path.read_text(encoding="utf-8")
    original = src

    for old, new in replacements:
        if old in src:
            src = src.replace(old, new, 1)
        elif new in src:
            print(f"already patched: {path}")
        else:
            print(f"warn: expected block not found in {path}: {old[:90]}...")

    if src != original:
        path.write_text(src, encoding="utf-8")
        print(f"patched: {path}")
        return True

    print(f"no changes: {path}")
    return False


def main() -> int:
    grouped: dict[Path, list[tuple[str, str]]] = {}
    for path, old, new in REPLACEMENTS:
        grouped.setdefault(path, []).append((old, new))

    changed = 0
    for path, replacements in grouped.items():
        if apply_replacements(path, replacements):
            changed += 1

    print(f"changed_files={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
