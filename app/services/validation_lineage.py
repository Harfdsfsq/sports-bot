"""Separate the frozen prediction algorithm from collection/transport revisions."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(parts):
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def selected_source(path, names):
    tree = ast.parse((ROOT / path).read_text())
    nodes = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            nodes.append(node)
            found.add(node.name)
        elif isinstance(node, ast.ClassDef):
            found.update(node.name + "." + n.name for n in node.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name + "." + n.name in names)
            nodes.extend(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name + "." + n.name in names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in {"PROTOCOL", "MODELS", "VERSION"} for t in targets):
                nodes.append(node)
    if found != names:
        raise ValueError("Missing registered model functions: " + ",".join(sorted(names - found)))
    return ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False)


def model_fingerprint(rules):
    parts = [json.dumps(rules, sort_keys=True)]
    for name in ("daily_goal_probability", "daily_context_reliability", "daily_quality_policy", "model", "quality"):
        tree = ast.parse((ROOT / "app/services" / (name + ".py")).read_text())
        parts.append(ast.dump(tree, include_attributes=False))
    parts.append(selected_source("app/services/totals_strategy_lab.py", {
        "logit", "calibrated", "fit_calibration", "paired_lines", "distribution", "evaluate_lines",
    }))
    parts.append(selected_source("app/services/goal_research.py", {
        "fit_model", "predict", "resolve_team", "research_rates", "over25", "GoalHistory.eligible",
    }))
    parts.append(selected_source("app/providers/sstats.py", {
        "form_team_key", "conflicting_history_indices", "SStatsContextProvider._build_team_form_contexts",
        "SStatsContextProvider._select_recent_rows", "SStatsContextProvider._resolve_team_key",
        "SStatsContextProvider._blend_expected_goals", "SStatsContextProvider._weighted_average",
        "SStatsContextProvider.chronological_form_context", "SStatsContextProvider._avg_field",
        "SStatsContextProvider._count_league_matches", "SStatsContextProvider._league_related",
        "SStatsContextProvider._extract_result", "SStatsContextProvider._extract_start",
        "SStatsContextProvider._row_to_context", "SStatsContextProvider._to_float",
        "SStatsContextProvider._first_float",
    }))
    # The reviewed profile is part of the predeclared selection policy. Pure
    # collection limits can change without claiming a different statistical model.
    profile = {}
    for line in (ROOT / "config/daily_quality.env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if any(s in key for s in ("MIN_ODDS", "MIN_TEAM_SAMPLE", "MAX_GOAL_DISAGREEMENT", "ALLOWED_MARKET", "LIVE_A_ONLY")):
                profile[key] = value
    parts.append(json.dumps(profile, sort_keys=True))
    return digest(parts)


def pipeline_fingerprint():
    parts = []
    paths = [*sorted((ROOT / "app/providers").glob("*.py")),
             ROOT / "app/services/context_admission.py", ROOT / "app/services/fixture_identity.py",
             ROOT / "app/services/daily_quality_runner.py", ROOT / "app/services/daily_match_registry.py",
             ROOT / "app/services/sstats_targeted_history.py", ROOT / "app/services/team_result_archive.py",
             ROOT / "app/services/sstats_team_lookup.py", ROOT / "app/services/espn_identity.py",
             ROOT / "app/services/bzzoiro_free_enrichment.py", ROOT / "app/services/sstats_standings.py",
             ROOT / "app/utils.py"]
    for path in paths:
        parts.append(ast.dump(ast.parse(path.read_text()), include_attributes=False))
    for name in ("daily_quality.env", "espn_team_aliases.json", "competition_squad_rules.json"):
        parts.append((ROOT / "config" / name).read_text())
    return digest(parts)
