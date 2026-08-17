"""Tests for base rate lookup fallback chain."""

from unittest.mock import MagicMock, patch

import pytest

from layers.layer3.base_rate_lookup import (
    BaseRateResult,
    get_base_rate,
    get_base_rate_for_catalyst,
)


def _row(slice_key: str, n: int = 40, succ: int = 10, tier: str = "high"):
    return {
        "slice_key": slice_key,
        "n_trials": n,
        "n_successes": succ,
        "success_rate": succ / n,
        "ci_low": 0.1,
        "ci_high": 0.4,
        "confidence_tier": tier,
        "source": "computed",
    }


@patch("layers.layer3.base_rate_lookup.get_engine")
def test_most_specific_match(mock_engine):
    mock_conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.mappings.return_value.first.return_value = _row(
        "phase=PHASE2|indication=oncology_solid|sponsor=small_cap"
    )

    r = get_base_rate("PHASE2", "oncology_solid", "small_cap", min_confidence="low")
    assert r is not None
    assert r.slice_key == "phase=PHASE2|indication=oncology_solid|sponsor=small_cap"
    assert r.fallback_used is False


@patch("layers.layer3.base_rate_lookup.get_engine")
def test_fallback_to_broader_slice(mock_engine):
    mock_conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.mappings.return_value.first.side_effect = [
        None,
        _row("phase=PHASE2|indication=oncology_solid"),
    ]

    r = get_base_rate("PHASE2", "oncology_solid", "mid_cap", min_confidence="low")
    assert r is not None
    assert r.slice_key == "phase=PHASE2|indication=oncology_solid"
    assert r.fallback_used is True


@patch("layers.layer3.base_rate_lookup.get_engine")
def test_min_confidence_blocks_low_tier(mock_engine):
    mock_conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.mappings.return_value.first.side_effect = [
        _row("phase=PHASE2|indication=oncology_solid|sponsor=small_cap", tier="low"),
        None,
        None,
        _row("phase=PHASE2", tier="high"),
    ]

    r = get_base_rate("PHASE2", "oncology_solid", "small_cap", min_confidence="medium")
    assert r is not None
    assert r.slice_key == "phase=PHASE2"
    assert r.fallback_used is True


def test_none_phase():
    assert get_base_rate(None, "oncology_solid") is None


def test_base_rate_result_model():
    m = BaseRateResult(
        slice_key="phase=PHASE2",
        n_trials=10,
        n_successes=3,
        success_rate=0.3,
        ci_low=0.1,
        ci_high=0.5,
        confidence_tier="medium",
        fallback_used=False,
    )
    assert m.success_rate == 0.3


@patch("layers.layer3.base_rate_lookup._lookup_slice_key")
def test_pdufa_returns_industry_prior(mock_lookup):
    mock_lookup.return_value = BaseRateResult(
        slice_key="pdufa|novel_nda_bla",
        n_trials=100,
        n_successes=85,
        success_rate=0.85,
        ci_low=0.77,
        ci_high=0.91,
        confidence_tier="high",
        fallback_used=False,
        rate_source="industry_prior",
    )
    r = get_base_rate_for_catalyst("pdufa", min_confidence="medium")
    assert r is not None
    assert r.slice_key == "pdufa|novel_nda_bla"
    assert r.rate_source == "industry_prior"
    mock_lookup.assert_called_once_with("pdufa|novel_nda_bla", "medium")


@patch("layers.layer3.base_rate_lookup._lookup_slice_key")
def test_adcom_returns_industry_prior(mock_lookup):
    mock_lookup.return_value = BaseRateResult(
        slice_key="adcom|any_vote_held",
        n_trials=100,
        n_successes=75,
        success_rate=0.75,
        ci_low=0.66,
        ci_high=0.83,
        confidence_tier="high",
        fallback_used=False,
        rate_source="industry_prior",
    )
    r = get_base_rate_for_catalyst("advisory_committee", min_confidence="medium")
    assert r is not None
    assert "adcom" in r.slice_key


@patch("layers.layer3.base_rate_lookup.get_base_rate")
def test_readout_uses_computed_rates(mock_get):
    mock_get.return_value = BaseRateResult(
        slice_key="phase=PHASE2",
        n_trials=100,
        n_successes=45,
        success_rate=0.45,
        ci_low=0.35,
        ci_high=0.55,
        confidence_tier="high",
        fallback_used=False,
        rate_source="computed",
    )
    r = get_base_rate_for_catalyst(
        "phase_readout",
        phase="PHASE2",
        indication_category="oncology_solid",
        min_confidence="medium",
    )
    assert r is not None
    assert r.rate_source == "computed"
    mock_get.assert_called_once()


@patch("layers.layer3.base_rate_lookup.get_base_rate_by_indication")
@patch("layers.layer3.base_rate_lookup.get_base_rate")
def test_unknown_type_falls_back_to_phase(mock_get, mock_ind):
    mock_get.return_value = None
    mock_ind.return_value = None
    r = get_base_rate_for_catalyst("other_type", phase="PHASE3", min_confidence="low")
    assert r is None
    mock_get.assert_called_once()
