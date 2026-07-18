from __future__ import annotations

import json
from pathlib import Path

from app.services import daily_coverage_bootstrap_restore_patch as restore_patch
from app.services import daily_coverage_state_persistence_patch as state_patch
from scripts import sync_daily_coverage_evidence_into_day_inventory as sync_script


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_bootstrap_restore_merges_latest_and_dated_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    date_key = "2026-07-18"
    latest_evidence = tmp_path / "exports" / "latest-evidence.json"
    dated_evidence = tmp_path / "day" / "dated-evidence.json"
    latest_ledger = tmp_path / "exports" / "latest-ledger.json"
    dated_ledger = tmp_path / "day" / "dated-ledger.json"

    _write(
        latest_evidence,
        {
            "date_local": date_key,
            "matches": {
                "soccer|alpha|beta|2026-07-18": {
                    "odds": {
                        "odds_api_io": {
                            "updated_at_utc": "2026-07-18T10:00:00+00:00",
                            "data": [],
                        }
                    }
                }
            },
        },
    )
    _write(
        dated_evidence,
        {
            "date_local": date_key,
            "matches": {
                "soccer|alpha|beta|2026-07-18": {
                    "odds": {
                        "sstats_pari": {
                            "updated_at_utc": "2026-07-18T11:00:00+00:00",
                            "data": [],
                        }
                    },
                    "context": {
                        "sstats": {
                            "updated_at_utc": "2026-07-18T11:00:00+00:00",
                            "data": {},
                        }
                    },
                }
            },
        },
    )
    _write(
        latest_ledger,
        {
            "date_local": date_key,
            "matches": {
                "soccer|alpha|beta|2026-07-18": {
                    "odds_sources": [],
                    "context_sources": [],
                }
            },
        },
    )
    _write(dated_ledger, {"date_local": date_key, "matches": {}})

    monkeypatch.setattr(restore_patch, "EVIDENCE_PATH", latest_evidence)
    monkeypatch.setattr(restore_patch, "LEDGER_PATH", latest_ledger)
    monkeypatch.setattr(restore_patch, "evidence_path", lambda _date: dated_evidence)
    monkeypatch.setattr(restore_patch, "ledger_path", lambda _date: dated_ledger)
    monkeypatch.setattr(restore_patch, "target_date", lambda: date_key)

    result = restore_patch.restore_state()
    ledger = json.loads(latest_ledger.read_text(encoding="utf-8"))
    row = ledger["matches"]["soccer|alpha|beta|2026-07-18"]
    assert result["evidence_matches"] == 1
    assert row["odds_sources"] == ["odds_api_io", "sstats_pari"]
    assert row["context_sources"] == ["sstats"]


def test_inventory_sync_uses_semantic_identity_and_actual_sources(
    monkeypatch, tmp_path: Path
) -> None:
    date_key = "2026-07-18"
    inventory_path = tmp_path / ".data" / "day_inventory" / f"{date_key}.json"
    evidence_path = tmp_path / ".data" / "exports" / "latest-evidence.json"
    _write(
        inventory_path,
        {
            "date_local": date_key,
            "matches": [
                {
                    "match_key": f"{date_key}|Beta FC|Alpha FC",
                    "home_team": "Beta FC",
                    "away_team": "Alpha FC",
                    "kickoff_utc": "2026-07-18T12:00:00+00:00",
                    "odds_sources": ["odds_api_io_account1"],
                    "context_sources": ["provider_day_discovery_canonical_pool"],
                }
            ],
        },
    )
    _write(
        evidence_path,
        {
            "date_local": date_key,
            "matches": {
                "soccer|alpha|beta|2026-07-18": {
                    "odds": {"odds_api_io": {}, "sstats_pari": {}},
                    "context": {"sstats": {}, "clubelo": {}},
                }
            },
        },
    )

    monkeypatch.setattr(sync_script, "ROOT", tmp_path)
    monkeypatch.setattr(sync_script, "OUT", tmp_path / "sync.json")
    monkeypatch.setattr(sync_script, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(sync_script, "target_date", lambda: date_key)
    monkeypatch.setattr(sync_script, "restore_state", lambda: {"status": "test"})
    monkeypatch.setattr(
        sync_script,
        "write_current_aliases",
        lambda *_args, **_kwargs: {"status": "test"},
    )

    result = sync_script.sync_inventory()
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    row = inventory["matches"][0]
    assert result["inventory_matches_semantically_matched"] == 1
    assert row["odds_sources"] == ["odds_api_io", "sstats_pari"]
    assert row["context_sources"] == ["clubelo", "sstats"]
    assert row["coverage"]["ready_for_model"] is True
    assert "ready_for_publish" not in row["coverage"]


def test_state_mirror_restores_after_cache_miss(tmp_path: Path) -> None:
    date_key = "2026-07-18"
    cache_path = tmp_path / "cache-state.json"
    mirror_path = tmp_path / "latest-state.json"
    _write(
        mirror_path,
        {"date_local": date_key, "run_index": 7, "run_ids": ["1", "2"]},
    )
    assert state_patch._restore(cache_path, mirror_path, date_key) is True
    restored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert restored["run_index"] == 7
    assert state_patch._mirror(cache_path, mirror_path, date_key) is True


def test_bootstrap_restore_prefers_newest_evidence_entry() -> None:
    date_key = "2026-07-18"
    merged = restore_patch._merge_evidence(
        date_key,
        [
            {
                "date_local": date_key,
                "matches": {
                    "m": {
                        "context": {
                            "sstats": {
                                "updated_at_utc": "2026-07-18T10:00:00+00:00",
                                "data": {"value": "old"},
                            }
                        }
                    }
                },
            },
            {
                "date_local": date_key,
                "matches": {
                    "m": {
                        "context": {
                            "sstats": {
                                "updated_at_utc": "2026-07-18T11:00:00+00:00",
                                "data": {"value": "new"},
                            }
                        }
                    }
                },
            },
        ],
    )
    assert merged["matches"]["m"]["context"]["sstats"]["data"]["value"] == "new"
