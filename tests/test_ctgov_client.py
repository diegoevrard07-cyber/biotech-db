"""Tests for ClinicalTrials.gov client."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layers.layer1 import ctgov_client

SAMPLE_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT00000001",
            "briefTitle": "Test GBM Study",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "startDateStruct": {"date": "2024-01"},
            "primaryCompletionDateStruct": {"date": "2025-06"},
        },
        "designModule": {
            "phases": ["PHASE2"],
            "enrollmentInfo": {"count": 120},
            "designInfo": {"allocation": "RANDOMIZED"},
        },
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "Test Pharma Inc", "class": "INDUSTRY"},
            "collaborators": [],
        },
        "conditionsModule": {"conditions": ["Glioblastoma"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Drug-X"}],
            "armGroups": [{"type": "PLACEBO_COMPARATOR"}],
        },
        "outcomesModule": {"primaryOutcomes": [{"measure": "Overall Survival"}]},
        "descriptionModule": {"briefSummary": "A phase 2 study"},
    }
}


def test_parse_study_record():
    parsed = ctgov_client.parse_study_record(SAMPLE_STUDY)
    assert parsed["nct_id"] == "NCT00000001"
    assert parsed["phase"] == "PHASE2"
    assert parsed["status"] == "RECRUITING"
    assert parsed["sponsor"] == "Test Pharma Inc"
    assert parsed["is_randomized"] is True
    assert parsed["has_control_arm"] is True


@patch("layers.layer1.ctgov_client._get")
def test_search_by_sponsor(mock_get):
    mock_get.return_value = {"studies": [SAMPLE_STUDY], "nextPageToken": None}
    results, strategy = ctgov_client.search_by_sponsor("Test Pharma Inc", ticker="TST")
    assert len(results) == 1
    assert results[0]["nct_id"] == "NCT00000001"
    assert strategy == "name"


@patch("layers.layer1.ctgov_client._get")
def test_search_caching(mock_get, tmp_path, monkeypatch):
    monkeypatch.setattr(ctgov_client.config, "CTGOV_CACHE_DIR", tmp_path)
    mock_get.return_value = {"studies": [SAMPLE_STUDY], "nextPageToken": None}

    first, _ = ctgov_client.search_by_sponsor("Test Pharma Inc")
    calls_after_first = mock_get.call_count
    second, _ = ctgov_client.search_by_sponsor("Test Pharma Inc")
    assert first == second
    assert mock_get.call_count == calls_after_first


@patch("layers.layer1.ctgov_client._get")
def test_get_study(mock_get, tmp_path, monkeypatch):
    monkeypatch.setattr(ctgov_client.config, "CTGOV_CACHE_DIR", tmp_path)
    mock_get.return_value = SAMPLE_STUDY
    result = ctgov_client.get_study("NCT00000001")
    assert result["nct_id"] == "NCT00000001"
    nct_file = tmp_path / "NCT00000001.json"
    assert nct_file.exists()
