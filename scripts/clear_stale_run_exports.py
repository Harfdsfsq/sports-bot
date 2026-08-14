from __future__ import annotations

"""Remove volatile latest-run artifacts before a new production run.

Some .data/exports/latest-* files are committed for diagnostics. If a later
step fails to refresh one of them, the detailed Telegram sender can otherwise
send an old report. This cleanup keeps persistent state/caches intact but clears
run-scoped reports so every workflow either sends a fresh report or an explicit
missing-report diagnostic.
"""

from pathlib import Path

PATTERNS = [
    ".logs/debug-last-run.json",
    ".data/exports/latest-run-summary.json",
    ".data/exports/latest-detailed-run-report.json",
    ".data/exports/latest-detailed-run-report.txt",
    ".data/exports/latest-detailed-run-report-cleaned.txt",
    ".data/exports/latest-detailed-run-report-send-clean.json",
    ".data/exports/latest-harizon-ideal-runtime-audit.json",
    ".data/exports/latest-harizon-ideal-runtime-audit.txt",
    ".data/exports/latest-ideal-audit-scorecard-patch.json",
    ".data/exports/latest-controlled-fallback-report.json",
    ".data/exports/latest-harizon-telegram-run-report.json",
    ".data/exports/latest-harizon-telegram-run-report.txt",
]


def main() -> int:
    removed: list[str] = []
    for item in PATTERNS:
        path = Path(item)
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(item)
        except Exception:
            pass
    out = Path(".data/exports/latest-run-export-cleanup.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("removed:\n" + "\n".join(removed) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
