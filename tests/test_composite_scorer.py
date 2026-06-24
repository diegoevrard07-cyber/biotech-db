"""Run composite scorer tests."""

from __future__ import annotations

from datetime import date, timedelta

from layers.composite.scorer import ScoreInputs, compute_edge_score, score_proximity


def test_proximity_near_term():
    assert score_proximity(date.today() + timedelta(days=14)) >= 0.85


def test_composite_with_financials():
    result = compute_edge_score(
        ScoreInputs(
            catalyst_id=1,
            company_id=1,
            expected_date=date.today() + timedelta(days=60),
            base_rate=0.55,
            runway_months=18,
            quarterly_burn=5_000_000,
            sec_confirmed=True,
        )
    )
    assert 0 < result["composite_score"] <= 1.0
    assert result["science_score"] is None
    assert result["financial_score"] > 0.5


def test_profitable_company_financial_score():
    result = compute_edge_score(
        ScoreInputs(
            catalyst_id=2,
            company_id=2,
            expected_date=None,
            base_rate=0.4,
            runway_months=None,
            quarterly_burn=-1_000_000,
        )
    )
    assert result["financial_score"] == 1.0
