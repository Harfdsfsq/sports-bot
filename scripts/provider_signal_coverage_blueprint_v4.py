from __future__ import annotations

"""Blueprint v4 shim.

Uses the existing blueprint v3 implementation but swaps provider day discovery to
v2, which reuses latest-sstats-crosswalk.json instead of calling SStats discovery
endpoints again and hitting 429 during provider-smoke.  After the v3 blueprint it
also emits a hard/soft signal-quality artifact for the active inventory.
"""

import json
from pathlib import Path

from scripts import provider_day_discovery_canonical_pool_v2
from scripts import provider_signal_coverage_blueprint as base
from scripts import provider_signal_coverage_blueprint_v3

OUT_DIR = Path(".data/exports")
ART_DIR = Path("artifacts/run-bot")


def _write_signal_quality_artifact() -> None:
    signal_quality = base.signal_quality_summary()
    payload = {
        "status": "ok",
        "mode": "match_signal_quality_features_v1",
        "signal_quality": signal_quality,
        "note": "hard/soft/environment split is diagnostic only; weather/news/metadata do not count as independent price confirmation.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    for path in (OUT_DIR / "latest-match-signal-quality-features.json", ART_DIR / "latest-match-signal-quality-features.json"):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text = ["# Match signal quality features", ""]
    for key, value in (signal_quality.get("tier_counts") or {}).items():
        text.append(f"- {key}: {value}")
    if signal_quality.get("hard_source_counts"):
        text.append(f"- hard_sources: {signal_quality.get('hard_source_counts')}")
    rendered = "\n".join(text) + "\n"
    for path in (OUT_DIR / "latest-match-signal-quality-features.txt", ART_DIR / "latest-match-signal-quality-features.txt"):
        path.write_text(rendered, encoding="utf-8")


def main() -> int:
    provider_signal_coverage_blueprint_v3.provider_day_discovery_canonical_pool = provider_day_discovery_canonical_pool_v2
    code = int(provider_signal_coverage_blueprint_v3.main() or 0)
    try:
        _write_signal_quality_artifact()
    except Exception as exc:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "latest-match-signal-quality-features.json").write_text(
            json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
