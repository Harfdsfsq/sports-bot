from __future__ import annotations

import re
from pathlib import Path

RUN_BOT = Path(".github/workflows/run-bot.yml")
DAILY = Path(".github/workflows/daily-report.yml")
BALANCED = Path("config/balanced_output.env")
QUOTA = Path("config/api_quota_governor.env")
GOVERNOR = Path("scripts/apply_provider_quota_governor.py")

SAFE_RELIEF_MARKER = "# Safe proxy totals relief after 2026-04-26 no-pick audit"
SAFE_RELIEF_BLOCK = f"""
{SAFE_RELIEF_MARKER}
# Slight confidence relief only when proxy/single-source candidates compensate
# with stronger value. Does not open spreads/h2h/teamtotals/btts.
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE=73.5
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP=5.0
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT=10.0
"""


def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def upsert_env(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"


def remove_old_no_pick_blocks(text: str) -> str:
    # Remove prior blocks inserted after CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD.
    text = re.sub(
        r'\n\s*if \[ "\$\{\{ github\.event_name \}\}" = "push" \]; then\n'
        r'\s*echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=false" >> "\$GITHUB_ENV"\n'
        r'\s*else\n'
        r'\s*echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=true" >> "\$GITHUB_ENV"\n'
        r'\s*fi',
        "",
        text,
    )
    text = re.sub(
        r'\n\s*if \[ "\$EVENT_NAME" = "schedule" \]; then\n'
        r'\s*echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=true" >> "\$GITHUB_ENV"\n'
        r'\s*else\n'
        r'\s*echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=false" >> "\$GITHUB_ENV"\n'
        r'\s*fi',
        "",
        text,
    )
    return text


def patch_run_bot() -> bool:
    text = read(RUN_BOT)
    original = text

    # Forecast cadence tuned from observed runtime:
    # workflow start -> fallback is ~7-10 min. Keep 30m lead by starting ~46m before kickoff.
    text = re.sub(
        r"- cron: '[^']*3-20 \* \* \*'.*",
        "- cron: '14,44 3-20 * * *' # 06:14-23:44 MSK; fallback lands before :00/:30 kickoff clusters",
        text,
        count=1,
    )

    text = remove_old_no_pick_blocks(text)

    marker = 'echo "CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD=false" >> "$GITHUB_ENV"'
    no_pick_block = marker + """
          # Only scheduled runs may send no-pick reports. Manual/push runs are diagnostics
          # and can start at bad minutes, so they must not spam Telegram with no-pick noise.
          if [ "$EVENT_NAME" = "schedule" ]; then
            echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=true" >> "$GITHUB_ENV"
          else
            echo "CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=false" >> "$GITHUB_ENV"
          fi"""
    if marker in text and "Only scheduled runs may send no-pick reports" not in text:
        text = text.replace(marker, no_pick_block, 1)

    if text != original:
        write(RUN_BOT, text)
        return True
    return False


def patch_daily_report() -> bool:
    if not DAILY.exists():
        return False
    text = read(DAILY)
    original = text

    new_schedule = (
        "  schedule:\n"
        "    # 23:55 MSK: daily operational report for the current local date.\n"
        "    - cron: '55 20 * * *'\n"
        "    # 02:40 MSK: final settlement/revision for the previous local date.\n"
        "    - cron: '40 23 * * *'\n\n"
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
        "          cat .data/exports/latest-daily-ops-report.txt || true\n\n"
    )
    if "Build and send daily operations report" not in text:
        text = text.replace("      - name: Collect daily report artifacts\n", ops_step + "      - name: Collect daily report artifacts\n")

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


def patch_safe_relief() -> bool:
    if not BALANCED.exists():
        return False
    text = read(BALANCED)
    original = text
    # Upsert exact values even if an older block exists.
    for key, value in {
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "73.5",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "5.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "10.0",
    }.items():
        text = upsert_env(text, key, value)
    if SAFE_RELIEF_MARKER not in text:
        text = text.rstrip() + "\n\n" + SAFE_RELIEF_BLOCK.strip() + "\n"
    if text != original:
        write(BALANCED, text)
        return True
    return False


def patch_futrix_quota() -> bool:
    changed = False
    if QUOTA.exists():
        text = read(QUOTA)
        original = text
        for key, value in {
            "FUTRIXMETRICS_DAILY_BUDGET": "60",
            "FUTRIXMETRICS_BUCKET_MAX": "160",
            "FUTRIXMETRICS_PER_RUN_MAX": "2",
            "FUTRIXMETRICS_RESERVE_TOKENS": "20",
            "FUTRIXMETRICS_INITIAL_TOKENS": "32",
            "FUTRIXMETRICS_MIN_START_TOKENS": "24",
            "FUTRIXMETRICS_MIN_SPACING_MINUTES": "15",
        }.items():
            text = upsert_env(text, key, value)
        if text != original:
            write(QUOTA, text)
            changed = True

    if GOVERNOR.exists():
        text = read(GOVERNOR)
        original = text
        # Targeted replacements for the current Futrix block values.
        text = text.replace("default_daily_budget=110", "default_daily_budget=60")
        text = text.replace("default_bucket_max=240", "default_bucket_max=160")
        text = text.replace("default_per_run_max=5", "default_per_run_max=2")
        text = text.replace("default_minute_spacing=3", "default_minute_spacing=15")
        text = text.replace(
            '"FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 4, 4, 20)) if grant > 0 else "0"',
            '"FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 3, 3, 8)) if grant > 0 else "0"',
        )
        if text != original:
            write(GOVERNOR, text)
            changed = True
    return changed


def main() -> int:
    actions = [
        ("run-bot :14/:44 schedule + no-pick only for schedule", patch_run_bot),
        ("daily report evening/finalizer + ops report", patch_daily_report),
        ("safe proxy totals relief", patch_safe_relief),
        ("FutrixMetrics throttle", patch_futrix_quota),
    ]
    changed: list[str] = []
    for label, func in actions:
        try:
            if func():
                changed.append(label)
        except FileNotFoundError as exc:
            print(f"Skipped {label}: {exc}")

    if changed:
        print("Applied:")
        for item in changed:
            print(f"- {item}")
    else:
        print("No changes needed; patch already applied or target files missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
