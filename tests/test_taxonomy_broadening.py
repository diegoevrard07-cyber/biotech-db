"""Universe broadening: oncology/CNS categorization + GBM flag + scope."""

from __future__ import annotations

from layers.layer3.indication_taxonomy import (
    categorize_indication,
    is_gbm_focused,
    is_in_scope,
)


def test_gbm_flagged():
    assert is_gbm_focused(["Glioblastoma Multiforme"]) is True
    assert is_gbm_focused(["High-grade glioma"]) is True
    assert is_gbm_focused(["Breast Cancer"]) is False
    assert is_gbm_focused([]) is False


def test_oncology_cns_categories():
    assert categorize_indication(["Glioblastoma"]) == "oncology_solid"
    assert categorize_indication(["Acute Myeloid Leukemia"]) == "oncology_heme"
    assert categorize_indication(["Alzheimer Disease"]) == "cns_neurodegenerative"
    assert categorize_indication(["Major Depressive Disorder"]) == "cns_psychiatric"


def test_in_scope():
    assert is_in_scope("oncology_solid") is True
    assert is_in_scope("cns_neurodegenerative") is True
    assert is_in_scope("cardiovascular") is False
    assert is_in_scope("other") is False
