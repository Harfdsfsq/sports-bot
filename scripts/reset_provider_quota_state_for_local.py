from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
STATE_PATH = Path(".data/provider_quota_governor_state.json")


MIN_TOKENS = {
    "odds_api_io": 24,
    "oddspapi": 4,
    "allsportsapi": 4,
    "sstats": 16,
    "bzzoiro": 8,
    "api_football": 10,
    "football_data": 8,
    "thesportsdb": 8,
    "futrixmetrics": 3,
    "newsapi": 4,
    "gnews": 4,
    "weather": 8,
    "rapidapi_sportsbook": 1,
    "rapidapi_odds_feed": 1,
    "rapidapi_free_football": 1,
    "rapidapi_sportapi7": 0,
    "rapidapi_meteostat": 3,
}


def main() -> int:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        state = {"providers": {}}

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    providers = state.setdefault("providers", {})

    for key, min_tokens in MIN_TOKENS.items():
        row = providers.setdefault(key, {})
        row["tokens"] = max(int(row.get("tokens") or 0), min_tokens)
        row["last_refill_date"] = today
        row["last_recovery_date"] = today
        row["recovery_reason"] = "local_manual_topup"
        row["updated_at"] = datetime.now(UTC).isoformat()

    state["updated_at"] = datetime.now(UTC).isoformat()
    state["mode"] = "token_bucket_local_topup"
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Top-up written: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
