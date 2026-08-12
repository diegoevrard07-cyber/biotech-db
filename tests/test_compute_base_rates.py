"""Tests for Wilson CI and slice keys."""

from layers.layer3.compute_utils import build_slice_key, confidence_tier, wilson_ci


def test_wilson_ci_basic():
    low, high = wilson_ci(27, 100)
    assert 0 <= low <= 0.27 <= high <= 1


def test_wilson_ci_zero():
    low, high = wilson_ci(0, 10)
    assert low == 0
    assert high >= 0


def test_confidence_tier():
    assert confidence_tier(30) == "high"
    assert confidence_tier(15) == "medium"
    assert confidence_tier(5) == "low"


def test_build_slice_key():
    key = build_slice_key(phase="PHASE2", indication="oncology_solid", sponsor="small_cap")
    assert "phase=PHASE2" in key
    assert "indication=oncology_solid" in key
    assert "sponsor=small_cap" in key


def test_build_slice_key_omits_null():
    key = build_slice_key(phase="PHASE2", indication=None, sponsor=None)
    assert key == "phase=PHASE2"
