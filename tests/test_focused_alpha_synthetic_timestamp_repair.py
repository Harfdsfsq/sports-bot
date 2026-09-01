from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import repair_synthetic_publication_timestamps as repair


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure(monkeypatch, tmp_path: Path, rows: list[dict]) -> Path:
    sync = tmp_path / "sync.json"
    picks = tmp_path / "picks.json"
    out = tmp_path / "report.json"
    now = datetime.now(UTC)
    _write(sync, {"created_at_utc": now.isoformat()})
    _write(picks, rows)
    monkeypatch.setattr(repair, "SYNC_REPORT", sync)
    monkeypatch.setattr(repair, "OUT", out)
    monkeypatch.setattr(repair, "JSON_PATHS", (picks,))
    monkeypatch.setattr(repair, "JSONL_PATHS", ())
    return picks


def test_sync_cluster_publication_timestamp_is_removed(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC)
    picks = _configure(
        monkeypatch,
        tmp_path,
        [
            {
                "match_key": "synthetic",
                "telegram_sent": True,
                "publication_lifecycle_status": "telegram_sent",
                "published_at_utc": now.isoformat(),
            }
        ],
    )

    result = repair.repair_exports(tolerance_minutes=10)
    row = json.loads(picks.read_text(encoding="utf-8"))[0]

    assert result["rows_repaired"] == 1
    assert "published_at_utc" not in row
    assert row["publication_time_missing"] is True
    assert row["synthetic_publication_timestamp_removed"] is True
    assert row["invalid_published_at_utc"]


def test_explicit_send_timestamp_preserves_publication_timestamp(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC)
    picks = _configure(
        monkeypatch,
        tmp_path,
        [
            {
                "match_key": "real-send",
                "telegram_sent": True,
                "publication_lifecycle_status": "telegram_sent",
                "sent_at_utc": now.isoformat(),
                "published_at_utc": now.isoformat(),
            }
        ],
    )

    result = repair.repair_exports(tolerance_minutes=10)
    row = json.loads(picks.read_text(encoding="utf-8"))[0]

    assert result["rows_repaired"] == 0
    assert row["published_at_utc"] == now.isoformat()
    assert "synthetic_publication_timestamp_removed" not in row
