"""Outcome math tests: returns, abnormal return vs benchmark, labeling."""

from __future__ import annotations

from layers.composite.outcomes import (
    LABEL_AMBIGUOUS,
    LABEL_HIT,
    LABEL_MISS,
    compute_outcome,
    label_outcome,
)


def test_label_thresholds():
    assert label_outcome(0.2, 0.1) == LABEL_HIT
    assert label_outcome(-0.2, 0.1) == LABEL_MISS
    assert label_outcome(0.05, 0.1) == LABEL_AMBIGUOUS
    assert label_outcome(None, 0.1) is None


def test_compute_outcome_abnormal_hit():
    out = compute_outcome(10.0, 13.0, 100.0, 105.0, threshold=0.1)
    assert out["raw_return"] == 0.3
    assert out["benchmark_return"] == 0.05
    assert out["abnormal_return"] == 0.25
    assert out["outcome_label"] == LABEL_HIT


def test_compute_outcome_miss():
    out = compute_outcome(10.0, 8.0, 100.0, 101.0, threshold=0.1)
    assert out["raw_return"] == -0.2
    assert out["outcome_label"] == LABEL_MISS


def test_compute_outcome_no_benchmark_uses_raw():
    out = compute_outcome(10.0, 12.0, None, None, threshold=0.1)
    assert out["benchmark_return"] is None
    assert out["abnormal_return"] == 0.2
    assert out["outcome_label"] == LABEL_HIT


def test_compute_outcome_missing_price():
    out = compute_outcome(None, 12.0, 100.0, 105.0, threshold=0.1)
    assert out["raw_return"] is None
    assert out["outcome_label"] is None


def test_compute_outcome_zero_pre_guard():
    out = compute_outcome(0.0, 12.0, 100.0, 105.0, threshold=0.1)
    assert out["raw_return"] is None
