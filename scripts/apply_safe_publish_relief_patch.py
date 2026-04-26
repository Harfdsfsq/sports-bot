from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(".")
BALANCED = ROOT / "config" / "balanced_output.env"
QUOTA = ROOT / "config" / "api_quota_governor.env"
GOVERNOR = ROOT / "scripts" / "apply_provider_quota_governor.py"

SAFE_BLOCK_MARKER = "# Safe proxy totals relief after 2026-04-26 no-pick audit"
SAFE_BLOCK = f"""
{SAFE_BLOCK_MARKER}
# The 2026-04-26 run found a safe totals candidate that missed the proxy confidence
# guard by 0.074 pp: CA Osasuna - Sevilla Over 2.5 @2.20.
# Keep quality strict by requiring stronger EV/edge for proxy single-source picks.
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


def patch_balanced() -> bool:
    text = read(BALANCED)
    if SAFE_BLOCK_MARKER in text:
        return False
    text = text.rstrip() + "\n\n" + SAFE_BLOCK.strip() + "\n"
    write(BALANCED, text)
    return True


def upsert_env(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"


def patch_quota_env() -> bool:
    text = read(QUOTA)
    original = text

    # FutrixMetrics hit 429 in the 2026-04-26 15:42 UTC run:
    # "rate quota exceeded: 42/30". Keep it useful but avoid burst overload.
    updates = {
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
    text = read(GOVERNOR)
    original = text

    # Patch only the FutrixMetrics provider block. This avoids touching global quota behavior.
    block_pattern = re.compile(
        r'(ProviderPlan\(\s*key="futrixmetrics",.*?build_env=lambda grant, tokens: \{.*?\},\s*\),)',
        re.DOTALL,
    )
    match = block_pattern.search(text)
    if not match:
        raise RuntimeError("Could not locate futrixmetrics ProviderPlan block in apply_provider_quota_governor.py")

    block = match.group(1)
    block = re.sub(r"default_daily_budget=\d+", "default_daily_budget=60", block)
    block = re.sub(r"default_bucket_max=\d+", "default_bucket_max=160", block)
    block = re.sub(r"default_per_run_max=\d+", "default_per_run_max=2", block)
    block = re.sub(r"default_reserve_tokens=\d+", "default_reserve_tokens=20", block)
    block = re.sub(r"default_minute_spacing=\d+", "default_minute_spacing=15", block)
    block = re.sub(
        r'"FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str\(clamp_int\(grant \* 4, 4, 20\)\) if grant > 0 else "0"',
        '"FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 3, 3, 8)) if grant > 0 else "0"',
        block,
    )
    text = text[: match.start(1)] + block + text[match.end(1):]

    if text != original:
        write(GOVERNOR, text)
        return True
    return False


def main() -> int:
    changed = []
    if patch_balanced():
        changed.append(str(BALANCED))
    if patch_quota_env():
        changed.append(str(QUOTA))
    if patch_governor():
        changed.append(str(GOVERNOR))

    if changed:
        print("Patched files:")
        for item in changed:
            print(f"- {item}")
    else:
        print("No changes needed; patch was already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
