from datetime import datetime, timezone

from app.services import top_inventory_runtime_scope_patch as scope


def test_scope_report_marker_version():
    assert "v4_direct_inventory_fallback" in scope._MARKER


def test_identity_is_ordered_not_sorted():
    assert scope._identity("A Team", "B Team", "2026-05-30") == "soccer|a_team|b_team|2026-05-30"
    assert scope._identity("B Team", "A Team", "2026-05-30") == "soccer|b_team|a_team|2026-05-30"
