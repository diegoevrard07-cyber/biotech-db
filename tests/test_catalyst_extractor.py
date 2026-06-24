"""Tests for catalyst extraction logic."""

from datetime import date, timedelta

from layers.layer1.catalyst_extractor import extract_catalysts, merge_funnel_stats, new_funnel_stats

BASE_TRIAL = {
    "nct_id": "NCT00000001",
    "title": "Phase 2 GBM Study",
    "phase": "PHASE2",
    "status": "ACTIVE_NOT_RECRUITING",
    "primary_completion_date": "2027-06",
    "brief_summary": "",
    "detailed_description": "",
}


def test_phase_readout_extraction():
    cats, stats = extract_catalysts(BASE_TRIAL, company_id=1, trial_id=10)
    readouts = [c for c in cats if c["catalyst_type"] == "phase_readout"]
    assert len(readouts) == 1
    assert readouts[0]["date_confidence"] == "high"
    expected = date(2027, 6, 1) + timedelta(days=90)
    assert readouts[0]["expected_date"] == expected.isoformat()
    assert stats["raw_extracted"] >= 1


def test_pdufa_stub_extraction():
    trial = dict(BASE_TRIAL)
    trial["brief_summary"] = "Company plans NDA submission following trial completion"
    cats, _ = extract_catalysts(trial, company_id=1, trial_id=10)
    pdufa = [c for c in cats if c["catalyst_type"] == "pdufa"]
    assert len(pdufa) == 1
    assert pdufa[0]["requires_manual_verification"] is True
    assert pdufa[0]["date_confidence"] == "low"


def test_advisory_committee_stub():
    trial = dict(BASE_TRIAL)
    trial["detailed_description"] = "FDA advisory committee review expected"
    cats, _ = extract_catalysts(trial, company_id=1, trial_id=10)
    adcom = [c for c in cats if c["catalyst_type"] == "advisory_committee"]
    assert len(adcom) == 1
    assert adcom[0]["requires_manual_verification"] is True


def test_non_phase2_3_skips_readout():
    trial = dict(BASE_TRIAL, phase="PHASE1")
    cats, stats = extract_catalysts(trial, company_id=1, trial_id=10)
    assert not any(c["catalyst_type"] == "phase_readout" for c in cats)
    assert stats["dropped_invalid_phase"] >= 1


def test_funnel_merge():
    a = new_funnel_stats()
    a["raw_extracted"] = 2
    b = new_funnel_stats()
    b["dropped_date_past"] = 1
    merged = merge_funnel_stats(a, b)
    assert merged["raw_extracted"] == 2
    assert merged["dropped_date_past"] == 1
