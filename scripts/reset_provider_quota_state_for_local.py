from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
STATE_PATH = Path(".data/provider_quota_governor_state.json")

MIN_TOKENS = {
    "odds_api_io": 48,
    "oddspapi": 10,
    "allsportsapi": 12,
    "sstats": 36,
    "bzzoiro": 1000,
    "api_football": 32,
    "football_data": 60,
    "thesportsdb": 96,
    "futrixmetrics": 50,
    "newsapi": 18,
    "gnews": 18,
    "weather": 96,
    "rapidapi_sportsbook": 12,
    "rapidapi_odds_feed": 10,
    "rapidapi_free_football": 4,
    "rapidapi_sportapi7": 0,
    "rapidapi_meteostat": 8,
}


def main() -> int:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        state = {"providers": {}}

    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    providers = state.setdefault("providers", {})

    for key, min_tokens in MIN_TOKENS.items():
        row = providers.setdefault(key, {})
        row["tokens"] = max(float(row.get("tokens") or 0), float(min_tokens))
        row["last_refill_date"] = today
        row["last_refill_at"] = now.isoformat()
        row["last_recovery_version"] = "real-quota-v1"
        row["recovery_reason"] = "local_real_quota_topup"
        row["updated_at"] = now.isoformat()

    state["updated_at"] = now.isoformat()
    state["mode"] = "continuous_token_bucket_real_quotas_local_topup"
    state["recovery_version"] = "real-quota-v1"
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Real-quota top-up written: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
