"""Calibration math tests: Brier score, reliability table, hit rate."""

from __future__ import annotations

from layers.composite.calibration import brier_score, hit_rate, reliability_table


def test_brier_perfect():
    assert brier_score([(1.0, 1), (0.0, 0)]) == 0.0


def test_brier_worst():
    assert brier_score([(0.0, 1), (1.0, 0)]) == 1.0


def test_brier_midpoint():
    assert brier_score([(0.5, 1), (0.5, 0)]) == 0.25


def test_brier_empty():
    assert brier_score([]) is None


def test_reliability_buckets():
    pairs = [(0.1, 0), (0.15, 0), (0.9, 1), (0.85, 1)]
    table = reliability_table(pairs, n_buckets=5)
    lows = [b for b in table if b["bucket"].startswith("0.00")]
    highs = [b for b in table if b["bucket"].startswith("0.80")]
    assert lows and lows[0]["observed_hit_rate"] == 0.0
    assert highs and highs[0]["observed_hit_rate"] == 1.0


def test_hit_rate():
    assert hit_rate([1, 0, 1, 1]) == 0.75
    assert hit_rate([]) is None
