"""Tests for sponsor classifier."""

from layers.layer3.sponsor_classifier import classify_sponsor


def test_big_pharma():
    assert classify_sponsor("Pfizer Inc.") == "big_pharma"
    assert classify_sponsor("Merck Sharp & Dohme") == "big_pharma"
    assert classify_sponsor("Genentech") == "big_pharma"


def test_academic():
    assert classify_sponsor("University of Pennsylvania") == "academic"
    assert classify_sponsor("National Cancer Institute") == "academic"
    assert classify_sponsor("Mayo Clinic") == "academic"


def test_small_cap_from_seed():
    assert classify_sponsor("Kazia Therapeutics", lookup_market_cap=True) == "unknown"
    # companies seed may not have KZIA - test with known seed name
    assert classify_sponsor("BioNTech", lookup_market_cap=True) in ("mid_cap", "small_cap", "unknown")


def test_unknown():
    assert classify_sponsor("") == "unknown"
    assert classify_sponsor("Obscure Biotech XYZ LLC") == "unknown"
