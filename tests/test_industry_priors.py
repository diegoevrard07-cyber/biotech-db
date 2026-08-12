"""Tests for industry prior seeding."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection
from scripts.seed_industry_priors import PRIORS, seed


@pytest.fixture(scope="module")
def seeded_priors():
    seed()
    yield
    seed()  # leave priors in DB for scripts/verify (tests use shared DB)


def test_all_priors_insert(seeded_priors):
    with get_connection() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM base_rates WHERE source = 'industry_prior'")
        ).scalar()
    assert count >= 4


def test_priors_have_industry_source(seeded_priors):
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT slice_key, source FROM base_rates WHERE source = 'industry_prior'")
        ).fetchall()
    assert len(rows) >= 4
    assert all(r[1] == "industry_prior" for r in rows)


def test_priors_rates_in_unit_interval(seeded_priors):
    with get_connection() as conn:
        rows = conn.execute(text("""
                SELECT success_rate, ci_low, ci_high FROM base_rates
                WHERE source = 'industry_prior'
                """)).fetchall()
    for rate, lo, hi in rows:
        assert 0 <= float(rate) <= 1
        assert 0 <= float(lo) <= 1
        assert 0 <= float(hi) <= 1
        assert float(lo) <= float(rate) <= float(hi)


def test_priors_idempotent(seeded_priors):
    seed()
    with get_connection() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM base_rates WHERE source = 'industry_prior'")
        ).scalar()
    assert count == 4


def test_priors_no_collision_with_computed(seeded_priors):
    with get_connection() as conn:
        rows = conn.execute(text("""
                SELECT slice_key FROM base_rates
                WHERE source = 'industry_prior' AND slice_key LIKE 'phase=%'
                """)).fetchall()
    assert rows == []
