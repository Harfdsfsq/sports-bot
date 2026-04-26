from __future__ import annotations

import re
from pathlib import Path

BALANCED = Path("config/balanced_output.env")
QUOTA = Path("config/api_quota_governor.env")
GOVERNOR = Path("scripts/apply_provider_quota_governor.py")


def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def upsert_env(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"


def patch_balanced() -> bool:
    if not BALANCED.exists():
        return False
    text = read(BALANCED)
    original = text

    updates = {
        # Safe proxy totals relief: lower confidence only when value is stronger.
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "73.5",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "5.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "10.0",
    }
    for key, value in updates.items():
        text = upsert_env(text, key, value)

    marker = "# Safe proxy totals relief after 2026-04-26 no-pick audit"
    if marker not in text:
        text = text.rstrip() + "\n\n" + marker + "\n" + "\n".join(f"{k}={v}" for k, v in updates.items()) + "\n"

    if text != original:
        write(BALANCED, text)
        return True
    return False


def patch_quota() -> bool:
    if not QUOTA.exists():
        return False
    text = read(QUOTA)
    original = text
    updates = {
        # Futrix was observed hitting provider-side 429 around 42/30. Keep it shortlist-only.
        "FUTRIXMETRICS_DAILY_BUDGET": "60",
        "FUTRIXMETRICS_BUCKET_MAX": "160",
        "FUTRIXMETRICS_PER_RUN_MAX": "2",
        "FUTRIXMETRICS_RESERVE_TOKENS": "20",
        "FUTRIXMETRICS_INITIAL_TOKENS": "32",
        "FUTRIXMETRICS_MIN_START_TOKENS": "24",
        "FUTRIXMETRICS_MIN_SPACING_MINUTES": "15",
    }
    for key, value in updates.items():
        text = upsert_env(text, key, value)

    if text != original:
        write(QUOTA, text)
        return True
    return False


def patch_governor() -> bool:
    if not GOVERNOR.exists():
        return False
    text = read(GOVERNOR)
    original = text

    # Target the existing FutrixMetrics defaults.
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
        return True
    return False


def main() -> int:
    changed = []
    for name, func in [
        ("balanced_output.env safe relief", patch_balanced),
        ("api_quota_governor.env Futrix throttle", patch_quota),
        ("apply_provider_quota_governor.py Futrix throttle", patch_governor),
    ]:
        try:
            if func():
                changed.append(name)
        except FileNotFoundError as exc:
            print(f"Skipped {name}: {exc}")

    if changed:
        print("Patched:")
        for item in changed:
            print(f"- {item}")
    else:
        print("No config changes needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
