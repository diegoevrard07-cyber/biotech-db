"""Tests for catalyst deduplication."""

from layers.layer1.dedupe import dedupe_catalysts


def _cat(
    company_id=1, trial_id=10, ctype="phase_readout", expected="2025-09-01", conf="low", extra=None
):
    c = {
        "company_id": company_id,
        "trial_id": trial_id,
        "catalyst_type": ctype,
        "expected_date": expected,
        "date_confidence": conf,
        "raw_data": extra or {"n": 1},
        "requires_manual_verification": False,
    }
    return c


def test_dedupe_keeps_higher_confidence():
    a = _cat(expected="2025-09-01", conf="low", extra={"source": "a"})
    b = _cat(expected="2025-09-10", conf="high", extra={"source": "b"})
    result, merges = dedupe_catalysts([a, b])
    assert len(result) == 1
    assert merges == 1
    assert result[0]["date_confidence"] == "high"
    assert result[0]["raw_data"]["source"] == "b"


def test_dedupe_different_dates_not_merged():
    a = _cat(expected="2025-01-01")
    b = _cat(expected="2025-06-01")
    result, merges = dedupe_catalysts([a, b])
    assert len(result) == 2
    assert merges == 0


def test_dedupe_different_types_not_merged():
    a = _cat(ctype="phase_readout")
    b = _cat(ctype="pdufa")
    result, merges = dedupe_catalysts([a, b])
    assert len(result) == 2
