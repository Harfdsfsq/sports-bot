from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location(
    'top_inventory_runtime_scope_patch',
    Path(__file__).resolve().parents[1] / 'app' / 'services' / 'top_inventory_runtime_scope_patch.py',
)
scope = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(scope)


def test_key_variants_match_inventory_order_independent():
    variants = scope._key_variants_from_values('FC Alpha', 'Beta Town', '2026-05-30', '')
    match = SimpleNamespace(
        match_key='soccer|beta_town|fc_alpha|2026-05-30',
        home_team='Beta Town',
        away_team='FC Alpha',
        commence_time=datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc),
    )
    assert scope._match_variants(match) & variants


def test_filter_matches_keeps_only_inventory_allowlist():
    allowed = scope._key_variants_from_values('A', 'B', '2026-05-30', '')
    keep = SimpleNamespace(match_key='', home_team='A', away_team='B', commence_time=datetime(2026, 5, 30, 10, 0, tzinfo=timezone.utc))
    drop = SimpleNamespace(match_key='', home_team='C', away_team='D', commence_time=datetime(2026, 5, 30, 10, 0, tzinfo=timezone.utc))
    assert scope._filter_matches([keep, drop], allowed) == [keep]
