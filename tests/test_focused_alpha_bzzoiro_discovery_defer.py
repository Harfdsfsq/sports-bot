from __future__ import annotations

import asyncio
import json

from scripts import enrich_inventory_bzzoiro_v2_targets as enrichment


def test_full_inventory_gap_enrichment_is_deferred_during_focused_prepare(
    monkeypatch,
    tmp_path,
) -> None:
    out = tmp_path / "bzzoiro-gap.json"
    monkeypatch.setattr(enrichment, "OUT", out)
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING", "1")
    monkeypatch.setenv("RUNBOT_FULL_BZZOIRO_GAP_ENRICHMENT_ENABLED", "false")

    report = asyncio.run(enrichment.run())

    assert report["status"] == "deferred_to_prediction_runner"
    assert report["publication_contract_relaxed"] is False
    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert persisted["reason"] == "focused_alpha_bounded_provider_refresh"
