"""Tests for indication taxonomy."""

import pytest

from layers.layer3.indication_taxonomy import INDICATION_CATEGORIES, categorize_indication

FIXTURES = [
    (["Non-Small Cell Lung Cancer"], "oncology_solid"),
    (["NSCLC"], "oncology_solid"),
    (["Lung Carcinoma, Non-Small-Cell"], "oncology_solid"),
    (["Acute Myeloid Leukemia"], "oncology_heme"),
    (["Diffuse Large B-Cell Lymphoma"], "oncology_heme"),
    (["Alzheimer Disease"], "cns_neurodegenerative"),
    (["Parkinson Disease"], "cns_neurodegenerative"),
    (["Major Depressive Disorder"], "cns_psychiatric"),
    (["Schizophrenia"], "cns_psychiatric"),
    (["Epilepsy"], "cns_other"),
    (["Multiple Sclerosis"], "cns_other"),
    (["Hypertension"], "cardiovascular"),
    (["Heart Failure"], "cardiovascular"),
    (["Type 2 Diabetes Mellitus"], "metabolic"),
    (["Obesity"], "metabolic"),
    (["Rheumatoid Arthritis"], "autoimmune"),
    (["Cystic Fibrosis"], "rare_genetic"),
    (["HIV Infections"], "infectious_disease"),
    (["COVID-19"], "infectious_disease"),
    (["Age-Related Macular Degeneration"], "ophthalmology"),
    (["Atopic Dermatitis"], "dermatology"),
    (["Asthma"], "respiratory"),
    (["Chronic Kidney Disease"], "renal"),
    (["Iron Deficiency Anemia"], "hematology_nonmalig"),
    (["Unknown Rare Condition XYZ123"], "other"),
]


@pytest.mark.parametrize("conditions,expected", FIXTURES)
def test_categorize_indication(conditions, expected):
    assert categorize_indication(conditions) == expected


def test_all_categories_defined():
    assert "oncology_solid" in INDICATION_CATEGORIES
    assert len(INDICATION_CATEGORIES) == 17
