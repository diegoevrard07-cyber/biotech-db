"""
PDUFA / catalyst date reconciliation logic.

Layer 4 pre-build hardening pass (Fix 3): safely overwrite CT.gov estimates with
SEC-confirmed dates while preserving provenance in expected_date_history.

See scripts/reconcile_pdufa_dates.py for the DB-facing CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from rapidfuzz import fuzz

EVENT_TO_CATALYST_TYPE: dict[str, str] = {
    "pdufa_assigned": "pdufa",
    "pdufa_delayed": "pdufa",
    "adcom_scheduled": "advisory_committee",
}


def catalyst_type_for_event(event_type: str) -> str | None:
    """Map an SEC 8-K event type to its catalyst_type, or None when unmappable."""
    return EVENT_TO_CATALYST_TYPE.get(event_type)


EVENT_TYPE_MAP: dict[str, tuple[str, ...]] = {
    "pdufa": ("pdufa_assigned", "pdufa_delayed"),
    "advisory_committee": ("adcom_scheduled",),
    "approval": ("approval",),
    "crl": ("crl",),
}


@dataclass
class CatalystRow:
    """A catalysts-table row (CT.gov-estimated date) to reconcile against SEC events."""

    id: int
    ticker: str
    catalyst_type: str
    expected_date: Optional[date]
    drug_name: Optional[str] = None
    sec_confirmed: bool = False
    sec_source_accession: Optional[str] = None
    expected_date_original: Optional[date] = None
    expected_date_history: list[dict] = field(default_factory=list)


@dataclass
class MaterialEvent:
    """An SEC 8-K material event candidate for confirming/overwriting a catalyst date."""

    ticker: str
    event_type: str
    event_date: Optional[date]
    confidence: str
    accession_number: str
    filed_date: date
    drug_name: Optional[str] = None
    extracted_data: dict[str, Any] = field(default_factory=dict)


def fuzzy_match(a: str, b: str, *, threshold: int = 90) -> bool:
    """Return True when two drug names match fuzzily; empty inputs count as matching."""
    if not a or not b:
        return True
    return fuzz.token_set_ratio(a.lower(), b.lower()) >= threshold


def event_matches_catalyst(catalyst: CatalystRow, event: MaterialEvent) -> bool:
    """
    Decide whether an SEC event plausibly refers to the same catalyst.

    Requires same ticker and compatible event family, fuzzy drug-name agreement,
    and (for not-yet-confirmed catalysts) event dates within 90 days.
    """
    allowed = EVENT_TYPE_MAP.get(catalyst.catalyst_type, ())
    if event.event_type not in allowed:
        return False
    if catalyst.ticker.upper() != event.ticker.upper():
        return False
    if catalyst.drug_name and event.drug_name:
        if not fuzzy_match(catalyst.drug_name, event.drug_name):
            return False
    if not catalyst.sec_confirmed and catalyst.expected_date and event.event_date:
        delta = abs((catalyst.expected_date - event.event_date).days)
        if delta > 90:
            return False
    return True


def reconcile_catalyst(
    catalyst: CatalystRow,
    candidate_events: list[MaterialEvent],
    *,
    filing_dates: dict[str, date] | None = None,
    min_confidence: str = "high",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Pick the best SEC event for a catalyst and return (updates, history_entry).

    Returns (None, None) when no change should be applied.
    """
    filing_dates = filing_dates or {}
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank = confidence_rank.get(min_confidence, 3)

    matched = [e for e in candidate_events if event_matches_catalyst(catalyst, e)]
    high_conf = [e for e in matched if confidence_rank.get(e.confidence, 0) >= min_rank]
    if not high_conf:
        return None, None

    high_conf.sort(
        key=lambda e: (e.filed_date, confidence_rank.get(e.confidence, 0)),
        reverse=True,
    )
    best = high_conf[0]

    if catalyst.sec_confirmed and catalyst.sec_source_accession:
        existing_filed = filing_dates.get(catalyst.sec_source_accession)
        if existing_filed and existing_filed >= best.filed_date:
            return None, None

    if catalyst.expected_date == best.event_date and catalyst.sec_confirmed:
        return None, None

    updates: dict[str, Any] = {
        "expected_date": best.event_date,
        "sec_confirmed": True,
        "sec_source_accession": best.accession_number,
    }
    if catalyst.expected_date_original is None and catalyst.expected_date is not None:
        updates["expected_date_original"] = catalyst.expected_date

    history_entry = {
        "date": best.event_date.isoformat() if best.event_date else None,
        "source": "sec_8k",
        "accession": best.accession_number,
        "event_type": best.event_type,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "previous_date": catalyst.expected_date.isoformat() if catalyst.expected_date else None,
    }
    return updates, history_entry
