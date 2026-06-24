"""Tests for outcome extraction."""

import pytest

from layers.layer3.outcome_extractor import extract_outcome, normalize_phase


def _study(outcomes, description=""):
    return {
        "protocolSection": {"identificationModule": {"nctId": "NCT00000001"}},
        "resultsSection": {
            "outcomeMeasuresModule": {
                "outcomeMeasures": outcomes,
            }
        },
    }


def test_pvalue_success():
    study = _study(
        [
            {
                "type": "PRIMARY",
                "title": "OS",
                "analyses": [{"pValue": "0.03"}],
            }
        ]
    )
    r = extract_outcome(study)
    assert r.primary_outcome_met is True
    assert r.primary_outcome_confidence == "high"
    assert r.extraction_method == "pvalue"


def test_pvalue_failure():
    study = _study([{"type": "PRIMARY", "analyses": [{"pValue": "0.42"}]}])
    r = extract_outcome(study)
    assert r.primary_outcome_met is False


def test_pvalue_less_than():
    study = _study([{"type": "PRIMARY", "analyses": [{"pValue": "<0.001"}]}])
    r = extract_outcome(study)
    assert r.primary_outcome_met is True


def test_narrative_success():
    study = _study(
        [
            {
                "type": "PRIMARY",
                "description": "The trial achieved primary endpoint with statistically significant improvement.",
            }
        ]
    )
    r = extract_outcome(study)
    assert r.primary_outcome_met is True
    assert r.extraction_method == "narrative"


def test_narrative_failure():
    study = _study(
        [{"type": "PRIMARY", "description": "The study did not meet its primary endpoint."}]
    )
    r = extract_outcome(study)
    assert r.primary_outcome_met is False


def test_unknown_no_primary():
    study = {"protocolSection": {"identificationModule": {"nctId": "NCT1"}}, "resultsSection": {}}
    r = extract_outcome(study)
    assert r.primary_outcome_met is None
    assert r.primary_outcome_confidence == "low"


def test_hr_ci_success():
    study = _study(
        [
            {
                "type": "PRIMARY",
                "paramType": "Hazard Ratio",
                "classes": [
                    {
                        "categories": [
                            {"measurements": [{"lowerLimit": "0.5", "upperLimit": "0.9"}]}
                        ]
                    }
                ],
            }
        ]
    )
    r = extract_outcome(study)
    assert r.primary_outcome_met is True
    assert r.extraction_method == "effect_ci"


def test_normalize_phase():
    assert normalize_phase("PHASE2/PHASE3") == "PHASE3"
    assert normalize_phase("PHASE1") == "PHASE1"


NARRATIVE_CASES = [
    ("achieved primary endpoint with statistically significant results", True),
    ("did not meet the primary endpoint", False),
    ("failed to demonstrate efficacy on the primary endpoint", False),
    ("primary objective was met in the intent-to-treat population", True),
    ("results were not statistically significant for the primary endpoint", False),
    ("met the primary endpoint of overall survival", True),
    ("the trial failed to meet its primary endpoint of PFS", False),
]


@pytest.mark.parametrize("text,expected", NARRATIVE_CASES)
def test_narrative_variants(text, expected):
    from layers.layer3.outcome_extractor import _extract_from_narrative

    r = _extract_from_narrative(text)
    assert r is not None
    assert r.primary_outcome_met is expected
