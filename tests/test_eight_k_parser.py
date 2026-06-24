"""Fixture-driven tests for 8-K parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layers.layer4.eight_k_parser import parse_8k

FIXTURES = Path(__file__).parent / "fixtures" / "eight_k"


def collect_fixtures():
    cases = []
    for html_path in sorted(FIXTURES.rglob("*.html")):
        expected_path = html_path.with_suffix(".expected.json")
        if not expected_path.exists():
            continue
        cases.append(
            pytest.param(
                html_path,
                json.loads(expected_path.read_text(encoding="utf-8")),
                id=str(html_path.relative_to(FIXTURES)),
            )
        )
    return cases


@pytest.mark.parametrize("html_path,expected", collect_fixtures())
def test_parser_against_fixture(html_path, expected):
    items = expected.get("items", [])
    events = parse_8k(html_path.read_text(encoding="utf-8"), items=items)

    if not expected["should_match"]:
        forbidden = expected.get("event_type")
        if forbidden:
            assert not any(e.event_type == forbidden for e in events), (
                f"False positive: {html_path.name} produced {forbidden}"
            )
        return

    matching = [e for e in events if e.event_type == expected["event_type"]]
    assert matching, f"Missed {expected['event_type']} in {html_path.name}"

    e = matching[0]
    if expected.get("event_date"):
        assert str(e.event_date) == expected["event_date"], (
            f"Date mismatch in {html_path.name}: got {e.event_date}, expected {expected['event_date']}"
        )
    assert e.confidence == expected["confidence"], (
        f"Confidence mismatch in {html_path.name}: got {e.confidence}"
    )
