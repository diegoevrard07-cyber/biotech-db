"""Calibration math tests: Brier score, reliability table, hit rate."""

from __future__ import annotations

from layers.composite.calibration import (
    brier_score,
    hit_rate,
    parse_reliability,
    reliability_curve_ready,
    reliability_n,
    reliability_table,
)


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


def test_parse_reliability_accepts_json_string_and_list():
    raw = '[{"bucket":"0.80-1.00","n":1,"mean_predicted":0.82,"observed_hit_rate":1.0}]'
    parsed = parse_reliability(raw)
    assert len(parsed) == 1
    assert parsed[0]["n"] == 1
    assert parse_reliability(None) == []
    assert parse_reliability("not-json") == []


def test_reliability_curve_hidden_for_single_point():
    one = [{"bucket": "0.80-1.00", "n": 1, "mean_predicted": 0.82, "observed_hit_rate": 1.0}]
    assert reliability_n(one) == 1
    assert reliability_curve_ready(one) is False
    assert reliability_curve_ready(one, n_pairs=1) is False


def test_reliability_curve_ready_needs_buckets_and_n():
    table = [
        {"bucket": "0.00-0.20", "n": 8, "mean_predicted": 0.12, "observed_hit_rate": 0.13},
        {"bucket": "0.40-0.60", "n": 7, "mean_predicted": 0.51, "observed_hit_rate": 0.43},
        {"bucket": "0.80-1.00", "n": 9, "mean_predicted": 0.88, "observed_hit_rate": 0.78},
    ]
    assert reliability_curve_ready(table) is True
    assert reliability_curve_ready(table[:2]) is False  # only two buckets
    fat = [{"bucket": "0.80-1.00", "n": 50, "mean_predicted": 0.9, "observed_hit_rate": 0.8}]
    assert reliability_curve_ready(fat, n_pairs=50) is False
