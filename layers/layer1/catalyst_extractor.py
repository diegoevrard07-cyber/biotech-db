"""
TODO LAYER 4: PDUFA and advisory_committee catalysts are currently keyword-stub only.
Real PDUFA/adcom dates must come from SEC 8-K filings parsed in Layer 4.
Expected: when Layer 4 lands, pdufa+adcom counts should jump from ~9 to 30-50.
If they don't, Layer 4 has a bug.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import config

READOUT_BUFFER_DAYS = 90
PHASE_READOUT_PHASES = {"PHASE2", "PHASE3", "PHASE2/PHASE3", "PHASE1/PHASE2"}

APPROVAL_PATTERN = re.compile(
    r"\b(BLA|NDA|biologics license application|new drug application|FDA approval|regulatory approval)\b",
    re.IGNORECASE,
)
ADVISORY_PATTERN = re.compile(
    r"\b(advisory committee|ODAC|adcom|FDA panel)\b",
    re.IGNORECASE,
)


def new_funnel_stats() -> dict[str, int]:
    """Return a zeroed counter dict tracking the catalyst extraction filter funnel."""
    return {
        "raw_extracted": 0,
        "dropped_date_past": 0,
        "dropped_invalid_phase": 0,
        "dropped_no_expected_date": 0,
        "dropped_dedupe_merge": 0,
        "final_upcoming": 0,
    }


def merge_funnel_stats(*parts: dict[str, int]) -> dict[str, int]:
    """Sum per-trial funnel counters into one pipeline-wide attrition summary."""
    merged = new_funnel_stats()
    for part in parts:
        for key in merged:
            merged[key] += part.get(key, 0)
    return merged


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(value[:length], fmt).date()
        except ValueError:
            continue
    return None


def _readout_confidence(status: str | None) -> str:
    if not status:
        return "low"
    s = status.upper()
    if s in ("ACTIVE_NOT_RECRUITING", "COMPLETED"):
        return "high"
    if s == "RECRUITING":
        return "medium"
    return "low"


def _normalize_phase(phase: str | None) -> set[str]:
    if not phase:
        return set()
    parts = {p.strip().upper() for p in phase.replace(",", "/").split("/")}
    return parts


def extract_catalysts(
    trial: dict, company_id: int, trial_id: int | None
) -> tuple[list[dict], dict[str, int]]:
    """Extract phase readout, PDUFA stub, and advisory committee stub catalysts."""
    catalysts: list[dict] = []
    stats = new_funnel_stats()
    stats.pop("dropped_dedupe_merge")
    stats.pop("final_upcoming")

    nct_id = trial.get("nct_id")
    phase_parts = _normalize_phase(trial.get("phase"))
    status = trial.get("status")
    title = trial.get("title") or nct_id
    today = date.today()
    pcd = _parse_date(trial.get("primary_completion_date"))

    # 1. Phase readout catalysts (upcoming only)
    if phase_parts & {"PHASE2", "PHASE3"}:
        stats["raw_extracted"] += 1
        if not pcd:
            stats["dropped_no_expected_date"] += 1
        else:
            expected = pcd + timedelta(days=READOUT_BUFFER_DAYS)
            if expected < today:
                stats["dropped_date_past"] += 1
            else:
                catalysts.append(
                    {
                        "company_id": company_id,
                        "trial_id": trial_id,
                        "catalyst_type": "phase_readout",
                        "expected_date": expected.isoformat(),
                        "date_confidence": _readout_confidence(status),
                        "description": f"Estimated Phase readout for {title} ({nct_id})",
                        "source": "ctgov_v2",
                        "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
                        "raw_data": {
                            "nct_id": nct_id,
                            "primary_completion_date": trial.get("primary_completion_date"),
                            "buffer_days": READOUT_BUFFER_DAYS,
                            "phase": trial.get("phase"),
                            "status": status,
                        },
                        "requires_manual_verification": False,
                    }
                )
    elif pcd and phase_parts and not (phase_parts & {"PHASE2", "PHASE3"}):
        stats["raw_extracted"] += 1
        stats["dropped_invalid_phase"] += 1

    text_blob = " ".join(
        filter(
            None,
            [
                trial.get("brief_summary"),
                trial.get("detailed_description"),
                trial.get("title"),
            ],
        )
    )

    # 2. PDUFA stub
    if APPROVAL_PATTERN.search(text_blob):
        stats["raw_extracted"] += 1
        pdufa_date = (pcd or today) + timedelta(days=180)
        if pdufa_date < today:
            stats["dropped_date_past"] += 1
        else:
            catalysts.append(
                {
                    "company_id": company_id,
                    "trial_id": trial_id,
                    "catalyst_type": "pdufa",
                    "expected_date": pdufa_date.isoformat(),
                    "date_confidence": "low",
                    "description": f"Potential PDUFA/approval event flagged in trial text ({nct_id})",
                    "source": "ctgov_v2",
                    "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
                    "raw_data": {"nct_id": nct_id, "matched_text": "BLA/NDA/approval keyword"},
                    "requires_manual_verification": True,
                }
            )

    # 3. Advisory committee stub
    if ADVISORY_PATTERN.search(text_blob):
        stats["raw_extracted"] += 1
        adcom_date = (pcd or today) + timedelta(days=120)
        if adcom_date < today:
            stats["dropped_date_past"] += 1
        else:
            catalysts.append(
                {
                    "company_id": company_id,
                    "trial_id": trial_id,
                    "catalyst_type": "advisory_committee",
                    "expected_date": adcom_date.isoformat(),
                    "date_confidence": "low",
                    "description": f"Potential advisory committee event flagged in trial text ({nct_id})",
                    "source": "ctgov_v2",
                    "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
                    "raw_data": {"nct_id": nct_id, "matched_text": "advisory committee keyword"},
                    "requires_manual_verification": True,
                }
            )

    for c in catalysts:
        if c["catalyst_type"] not in config.ALLOWED_CATALYST_TYPES:
            raise ValueError(f"Invalid catalyst_type: {c['catalyst_type']}")

    full_stats = new_funnel_stats()
    for k in ("raw_extracted", "dropped_date_past", "dropped_invalid_phase", "dropped_no_expected_date"):
        full_stats[k] = stats[k]
    return catalysts, full_stats
