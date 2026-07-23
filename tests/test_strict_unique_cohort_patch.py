from __future__ import annotations

from typing import Any

import pytest

from app.services import strict_unique_cohort_patch as patch


def row(key: str, label: str) -> dict[str, Any]:
    date, home, away = key.split("|")
    return {
        "match_key": key,
        "date_local": date,
        "home_team": home,
        "away_team": away,
        "label": label,
    }


def test_unique_ranked_keeps_first_richer_ranked_row() -> None:
    duplicate = "2026-07-23|nashville|montreal"
    ranked = [
        ((3,), row(duplicate, "best")),
        ((2,), row(duplicate, "alias")),
        ((1,), row("2026-07-23|other home|other away", "other")),
    ]

    unique, duplicate_keys = patch._unique_ranked(ranked)

    assert [item[1]["label"] for item in unique] == ["best", "other"]
    assert duplicate_keys == [duplicate]


def test_select_deduplicates_before_original_and_fills_next_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = "2026-07-23|ca lanus|cienciano"
    ranked = [
        ((4,), row(duplicate, "best")),
        ((3,), row(duplicate, "duplicate")),
        ((2,), row("2026-07-23|home two|away two", "two")),
        ((1,), row("2026-07-23|home three|away three", "three")),
    ]
    captured: list[list[tuple[tuple[Any, ...], dict[str, Any]]]] = []

    def original(unique: list[tuple[tuple[Any, ...], dict[str, Any]]]):
        captured.append(unique)
        return [item[1] for item in unique[:3]], 7

    monkeypatch.setattr(patch, "_ORIGINAL_SELECT", original)
    monkeypatch.setattr(patch, "_write", lambda *_args, **_kwargs: None)

    selected, offset = patch._select(ranked)

    assert offset == 7
    assert [item["label"] for item in selected] == ["best", "two", "three"]
    assert len(captured[0]) == 3
    assert len({item["match_key"] for item in selected}) == 3


def test_unkeyed_rows_are_not_collapsed() -> None:
    ranked = [
        ((2,), {"label": "first"}),
        ((1,), {"label": "second"}),
    ]

    unique, duplicate_keys = patch._unique_ranked(ranked)

    assert len(unique) == 2
    assert duplicate_keys == []
