"""Expose data health separately from statistically established betting readiness."""

from __future__ import annotations


def assess(summary, coverage):
    providers = summary.get("source_stats") or {}
    failures = {name: s.get("runtime_error") for name, s in providers.items() if s.get("runtime_error")}
    trial = summary.get("totals_trial") or {}
    validation = summary.get("prospective_validation") or {}
    near = int(coverage.get("near") or 0)
    ready = int(coverage.get("near_ready") or 0)
    blockers = []
    if coverage.get("near_runtime_identity_quarantined", 0):
        blockers.append("conflicting_fixture_times")
    if failures:
        blockers.append("provider_deadline_or_runtime_error")
    if near and ready / near < 0.9:
        blockers.append("joint_coverage_below_90_percent")
    if trial.get("settled", 0) and (trial.get("paper_roi_pct") or 0) <= 0:
        blockers.append("native_paper_model_not_profitable")
    if not validation.get("live_ready"):
        blockers.append("independent_forward_validation_incomplete")
    return {
        "data_status": "degraded" if failures or (near and ready / near < 0.9) else "available",
        "betting_status": "review_validated_report" if not blockers else "paper_only",
        "near": near, "near_ready": ready, "provider_failures": failures,
        "native_paper": {k: trial.get(k) for k in ("tracked", "settled", "paper_roi_pct", "brier", "market_brier")},
        "forward": {k: validation.get(k) for k in ("status", "cohort", "metrics", "live_ready")},
        "blockers": blockers, "automatic_live_promotion": False,
        "collection_schedule": "existing_workflow_unchanged",
    }
