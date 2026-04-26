from __future__ import annotations

import re
from pathlib import Path

RUN_BOT = Path(".github/workflows/run-bot.yml")
DAILY = Path(".github/workflows/daily-report.yml")


def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def patch_run_bot() -> bool:
    text = read(RUN_BOT)
    original = text

    text = re.sub(
        r"- cron: '[^']*3-20 \* \* \*'.*",
        "- cron: '2,32 3-20 * * *' # 06:02-23:32 MSK; starts before common :00/:30 kickoffs",
        text,
        count=1,
    )
    text = text.replace(
        "# MSK = UTC+3. Run before common :00/:30 kickoffs, preserving\n"
        "    # MIN_KICKOFF_LEAD_MINUTES=30 while the quota governor prevents API burn.\n",
        "# MSK = UTC+3. Start before common :00/:30 kickoffs.\n"
        "    # Keeps MIN_KICKOFF_LEAD_MINUTES=30 while reducing match_time_outside_window noise.\n",
    )

    if text != original:
        write(RUN_BOT, text)
        return True
    return False


def patch_daily_report() -> bool:
    text = read(DAILY)
    original = text

    new_schedule = (
        "  schedule:\n"
        "    # 23:55 MSK: daily operational report for the current local date.\n"
        "    - cron: '55 20 * * *'\n"
        "    # 02:40 MSK: final settlement/revision for the previous local date.\n"
        "    - cron: '40 23 * * *'\n"
        "\n"
    )
    text = re.sub(
        r"  schedule:\n(?:    #.*\n|    - cron:.*\n)+\npermissions:",
        new_schedule + "permissions:",
        text,
        count=1,
    )

    text = text.replace("DAILY_REPORT_HOUR_LOCAL: '22'", "DAILY_REPORT_HOUR_LOCAL: '0'")
    text = text.replace("  DAILY_REPORT_HOUR_LOCAL: '22'", "  DAILY_REPORT_HOUR_LOCAL: '0'")
    text = text.replace('echo "DAILY_REPORT_HOUR_LOCAL=22" >> "$GITHUB_ENV"', 'echo "DAILY_REPORT_HOUR_LOCAL=0" >> "$GITHUB_ENV"')

    target_line = 'echo "DAILY_REPORT_TARGET_OFFSET_DAYS=${{ github.event_name == \'workflow_dispatch\' && inputs.target_offset_days || \'0\' }}" >> "$GITHUB_ENV"'
    if target_line in text and "REPORT_OFFSET=" not in text:
        replacement = (
            'REPORT_OFFSET="${{ github.event_name == \'workflow_dispatch\' && inputs.target_offset_days || \'0\' }}"\n'
            '          if [ "${{ github.event.schedule }}" = "40 23 * * *" ]; then\n'
            '            REPORT_OFFSET="1"\n'
            '          fi\n'
            '          echo "DAILY_REPORT_TARGET_OFFSET_DAYS=${REPORT_OFFSET}" >> "$GITHUB_ENV"'
        )
        text = text.replace(target_line, replacement)

    if "SETTLEMENT_GRACE_MINUTES: '180'" not in text:
        text = text.replace(
            "  SETTLEMENT_ENABLED: 'true'\n  SETTLEMENT_SEND_TELEGRAM_SUMMARY: 'false'\n",
            "  SETTLEMENT_ENABLED: 'true'\n  SETTLEMENT_GRACE_MINUTES: '180'\n  SETTLEMENT_LOOKBACK_DAYS: '7'\n  SETTLEMENT_SEND_TELEGRAM_SUMMARY: 'false'\n",
        )

    if 'echo "SETTLEMENT_GRACE_MINUTES=180" >> "$GITHUB_ENV"' not in text:
        text = text.replace(
            'echo "SETTLEMENT_ENABLED=true" >> "$GITHUB_ENV"\n          echo "SETTLEMENT_SEND_TELEGRAM_SUMMARY=false" >> "$GITHUB_ENV"',
            'echo "SETTLEMENT_ENABLED=true" >> "$GITHUB_ENV"\n          echo "SETTLEMENT_GRACE_MINUTES=180" >> "$GITHUB_ENV"\n          echo "SETTLEMENT_LOOKBACK_DAYS=7" >> "$GITHUB_ENV"\n          echo "SETTLEMENT_SEND_TELEGRAM_SUMMARY=false" >> "$GITHUB_ENV"',
        )

    ops_step = (
        "      - name: Build and send daily operations report\n"
        "        if: always()\n"
        "        shell: bash\n"
        "        run: |\n"
        "          python scripts/build_daily_ops_report.py --send-telegram || true\n"
        "          echo \"---- latest daily operations report ----\"\n"
        "          cat .data/exports/latest-daily-ops-report.txt || true\n"
        "\n"
    )
    if "Build and send daily operations report" not in text:
        text = text.replace(
            "      - name: Collect daily report artifacts\n",
            ops_step + "      - name: Collect daily report artifacts\n",
        )

    if "artifacts/daily-report/latest-daily-ops-report.json" not in text:
        text = text.replace(
            "          cp -f .data/exports/latest-daily-summary.csv artifacts/daily-report/latest-daily-summary.csv 2>/dev/null || true\n",
            "          cp -f .data/exports/latest-daily-summary.csv artifacts/daily-report/latest-daily-summary.csv 2>/dev/null || true\n"
            "          cp -f .data/exports/latest-daily-ops-report.json artifacts/daily-report/latest-daily-ops-report.json 2>/dev/null || true\n"
            "          cp -f .data/exports/latest-daily-ops-report.txt artifacts/daily-report/latest-daily-ops-report.txt 2>/dev/null || true\n",
        )
        text = text.replace(
            "            .data/exports/latest-daily-summary.csv\n",
            "            .data/exports/latest-daily-summary.csv\n"
            "            .data/exports/latest-daily-ops-report.json\n"
            "            .data/exports/latest-daily-ops-report.txt\n",
        )

    if text != original:
        write(DAILY, text)
        return True
    return False


def main() -> int:
    changed = []
    if patch_run_bot():
        changed.append(str(RUN_BOT))
    if patch_daily_report():
        changed.append(str(DAILY))

    if changed:
        print("Patched:")
        for item in changed:
            print(f"- {item}")
    else:
        print("No workflow changes needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
