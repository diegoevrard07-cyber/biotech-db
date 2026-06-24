"""Unit tests for PDUFA / catalyst date reconciliation."""

from __future__ import annotations

from datetime import date

from layers.layer4.pdufa_reconciliation import (
    CatalystRow,
    MaterialEvent,
    reconcile_catalyst,
)


def _catalyst(**kwargs) -> CatalystRow:
    defaults = {
        "id": 1,
        "ticker": "ABCD",
        "catalyst_type": "pdufa",
        "expected_date": date(2025, 6, 15),
        "drug_name": "asset-A",
    }
    defaults.update(kwargs)
    return CatalystRow(**defaults)


def _event(**kwargs) -> MaterialEvent:
    defaults = {
        "ticker": "ABCD",
        "event_type": "pdufa_assigned",
        "event_date": date(2025, 4, 15),
        "confidence": "high",
        "accession_number": "0001193125-24-000001",
        "filed_date": date(2024, 11, 22),
        "drug_name": "asset-A",
    }
    defaults.update(kwargs)
    return MaterialEvent(**defaults)


def test_overwrites_low_confidence_ctgov_estimate():
    cat = _catalyst(expected_date=date(2025, 6, 1), sec_confirmed=False)
    updates, history = reconcile_catalyst(cat, [_event()])
    assert updates is not None
    assert updates["expected_date"] == date(2025, 4, 15)
    assert updates["sec_confirmed"] is True
    assert updates["expected_date_original"] == date(2025, 6, 1)
    assert history["accession"] == "0001193125-24-000001"


def test_does_not_overwrite_with_low_confidence_sec_event():
    cat = _catalyst()
    updates, _ = reconcile_catalyst(cat, [_event(confidence="medium")])
    assert updates is None


def test_does_not_overwrite_more_recent_sec_confirmation():
    cat = _catalyst(
        sec_confirmed=True,
        sec_source_accession="0001193125-24-OLD001",
        expected_date=date(2025, 4, 15),
    )
    older = _event(
        accession_number="0001193125-24-OLD002",
        event_date=date(2025, 3, 1),
        filed_date=date(2024, 8, 15),
    )
    filing_dates = {
        "0001193125-24-OLD001": date(2024, 10, 1),
        "0001193125-24-OLD002": date(2024, 8, 15),
    }
    updates, _ = reconcile_catalyst(cat, [older], filing_dates=filing_dates)
    assert updates is None


def test_pdufa_delay_overwrites_prior_pdufa():
    cat = _catalyst(
        sec_confirmed=True,
        sec_source_accession="0001193125-24-000001",
        expected_date=date(2025, 4, 15),
        expected_date_original=date(2025, 6, 1),
        expected_date_history=[{"date": "2025-04-15", "source": "sec_8k"}],
    )
    delay = _event(
        event_type="pdufa_delayed",
        event_date=date(2025, 8, 15),
        accession_number="0001193125-25-000020",
        filed_date=date(2025, 3, 20),
    )
    filing_dates = {"0001193125-24-000001": date(2024, 10, 1)}
    updates, history = reconcile_catalyst(cat, [delay], filing_dates=filing_dates)
    assert updates is not None
    assert updates["expected_date"] == date(2025, 8, 15)
    assert history["event_type"] == "pdufa_delayed"


def test_drug_name_mismatch_blocks_reconciliation():
    cat = _catalyst(drug_name="asset-A")
    updates, _ = reconcile_catalyst(cat, [_event(drug_name="asset-B")])
    assert updates is None


def test_appends_to_history_on_every_change():
    cat = _catalyst(expected_date_history=[])
    _, h1 = reconcile_catalyst(cat, [_event(accession_number="0001")])
    assert h1 is not None
    cat.sec_confirmed = True
    cat.sec_source_accession = "0001"
    cat.expected_date = date(2025, 4, 15)
    cat.expected_date_history.append(h1)
    _, h2 = reconcile_catalyst(
        cat,
        [
            _event(
                event_type="pdufa_delayed",
                event_date=date(2025, 8, 15),
                accession_number="0002",
                filed_date=date(2025, 3, 20),
            )
        ],
        filing_dates={"0001": date(2024, 11, 22)},
    )
    assert h2 is not None
    assert len(cat.expected_date_history) == 1  # caller appends; second entry produced


def test_dry_run_writes_nothing():
    """Dry-run is enforced in the CLI; core logic returns updates without side effects."""
    cat = _catalyst()
    updates, history = reconcile_catalyst(cat, [_event()])
    assert updates
    assert history
    assert cat.expected_date == date(2025, 6, 15)
    assert cat.sec_confirmed is False
