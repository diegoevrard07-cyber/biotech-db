"""Tests for XBRL quarterly extraction, burn rate, and runway."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from layers.layer4.xbrl_extractor import (
    compute_burn_rate,
    compute_runway,
    extract_quarterly_concept,
)

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_q_only_clean():
    facts = load("q_only_clean.json")
    quarters = extract_quarterly_concept(facts, "ResearchAndDevelopmentExpense")
    assert len(quarters) >= 4
    direct = [q for q in quarters if q.period_end.year == 2024][:3]
    assert len(direct) == 3
    assert all(q.source == "direct" for q in direct)


def test_ytd_only_derives_correctly():
    facts = load("ytd_only.json")
    quarters = extract_quarterly_concept(facts, "ResearchAndDevelopmentExpense")
    by_period = {q.period_end: q for q in quarters if q.period_end.year == 2024}

    assert by_period[date(2024, 3, 31)].value == 10_000_000
    assert by_period[date(2024, 3, 31)].source == "direct"

    assert by_period[date(2024, 6, 30)].value == 12_000_000
    assert by_period[date(2024, 6, 30)].source == "derived_from_ytd"

    assert by_period[date(2024, 9, 30)].value == 14_000_000
    assert by_period[date(2024, 12, 31)].value == 14_000_000
    assert by_period[date(2024, 12, 31)].source == "derived_from_fy"


def test_mixed_periods():
    facts = load("mixed_periods.json")
    quarters = extract_quarterly_concept(facts, "ResearchAndDevelopmentExpense")
    by_period = {q.period_end: q for q in quarters if q.period_end.year == 2024}
    assert by_period[date(2024, 3, 31)].source == "direct"
    assert by_period[date(2024, 6, 30)].source == "derived_from_ytd"
    assert by_period[date(2024, 9, 30)].source == "direct"
    assert date(2024, 12, 31) in by_period


def test_fiscal_year_offset():
    facts = load("fiscal_year_offset.json")
    quarters = extract_quarterly_concept(
        facts,
        "ResearchAndDevelopmentExpense",
        fiscal_year_end_month=6,
        fiscal_year_end_day=30,
    )
    assert any(q.period_end == date(2024, 9, 30) for q in quarters)


def test_missing_quarter_surfaces_gap():
    facts = load("missing_quarters.json")
    quarters = extract_quarterly_concept(facts, "ResearchAndDevelopmentExpense")
    period_ends = [q.period_end for q in quarters if q.period_end.year == 2024]
    assert date(2024, 6, 30) not in period_ends


def test_negative_burn_flags_profitable():
    facts = load("negative_burn.json")
    quarters = extract_quarterly_concept(facts, "NetCashProvidedByUsedInOperatingActivities")
    burn = compute_burn_rate(quarters)
    assert burn is not None
    assert burn < 0
    runway = compute_runway(cash_position=100_000_000, quarterly_burn=burn)
    assert "profitable" in runway["flags"]
    assert runway["runway_months"] == float("inf")


def test_provenance_on_derived_quarters():
    facts = load("ytd_only.json")
    quarters = extract_quarterly_concept(facts, "ResearchAndDevelopmentExpense")
    derived = [q for q in quarters if q.source != "direct"]
    assert derived
    assert all(q.raw_facts for q in derived)
