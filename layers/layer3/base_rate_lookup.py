"""Base rate lookup with fallback chain and catalyst-type routing."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import text

from db import get_engine

TIER_RANK = {"low": 0, "medium": 1, "high": 2}

READOUT_TYPES = {"readout", "phase_readout"}
PDUFA_TYPES = {"pdufa"}
ADCOM_TYPES = {"adcom", "advisory_committee"}


class BaseRateResult(BaseModel):
    """Empirical success-rate estimate for one slice: rate, CI, tier, and provenance."""

    slice_key: str
    n_trials: int
    n_successes: int
    success_rate: float
    ci_low: float
    ci_high: float
    confidence_tier: str
    fallback_used: bool
    rate_source: str = "computed"


def _meets_tier(tier: str, min_confidence: str) -> bool:
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(min_confidence, 0)


def _row_to_result(row: dict, fallback_used: bool, rate_source: str) -> BaseRateResult:
    return BaseRateResult(
        slice_key=row["slice_key"],
        n_trials=row["n_trials"],
        n_successes=row["n_successes"],
        success_rate=float(row["success_rate"]),
        ci_low=float(row["ci_low"]),
        ci_high=float(row["ci_high"]),
        confidence_tier=row["confidence_tier"],
        fallback_used=fallback_used,
        rate_source=rate_source,
    )


def _lookup_slice_key(slice_key: str, min_confidence: str) -> BaseRateResult | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("""
                SELECT slice_key, n_trials, n_successes, success_rate, ci_low, ci_high,
                       confidence_tier, COALESCE(source, 'computed') AS source
                FROM base_rates
                WHERE slice_key = :slice_key
                LIMIT 1
                """),
                {"slice_key": slice_key},
            )
            .mappings()
            .first()
        )
        if row and _meets_tier(row["confidence_tier"], min_confidence):
            return _row_to_result(row, fallback_used=False, rate_source=row["source"])
    return None


def get_base_rate(
    phase: str | None,
    indication_category: str | None = None,
    sponsor_class: str | None = None,
    min_confidence: str = "low",
) -> BaseRateResult | None:
    """
    Return the most specific base rate available for the given filters.
    Falls back to less specific slices if no match meets min_confidence.
    """
    if not phase:
        return None

    candidates: list[dict] = []
    if indication_category and sponsor_class:
        candidates.append(
            {
                "phase": phase,
                "indication_category": indication_category,
                "sponsor_class": sponsor_class,
            }
        )
    if indication_category:
        candidates.append({"phase": phase, "indication_category": indication_category})
    if sponsor_class:
        candidates.append({"phase": phase, "sponsor_class": sponsor_class})
    candidates.append({"phase": phase})

    engine = get_engine()
    with engine.connect() as conn:
        for i, filt in enumerate(candidates):
            clauses = ["phase = :phase", "(source IS NULL OR source = 'computed')"]
            params: dict = {"phase": filt["phase"]}
            if "indication_category" in filt:
                clauses.append("indication_category = :indication_category")
                params["indication_category"] = filt["indication_category"]
            else:
                clauses.append("indication_category IS NULL")
            if "sponsor_class" in filt:
                clauses.append("sponsor_class = :sponsor_class")
                params["sponsor_class"] = filt["sponsor_class"]
            else:
                clauses.append("sponsor_class IS NULL")

            sql = f"""
                SELECT slice_key, n_trials, n_successes, success_rate, ci_low, ci_high,
                       confidence_tier, COALESCE(source, 'computed') AS source
                FROM base_rates
                WHERE {' AND '.join(clauses)}
                LIMIT 1
            """
            row = conn.execute(text(sql), params).mappings().first()
            if row and _meets_tier(row["confidence_tier"], min_confidence):
                return _row_to_result(row, fallback_used=i > 0, rate_source=row["source"])
    return None


def get_base_rate_by_indication(
    indication_category: str | None,
    min_confidence: str = "medium",
) -> BaseRateResult | None:
    """Indication-only slice (phase IS NULL) for trials without a parseable phase."""
    if not indication_category:
        return None
    engine = get_engine()
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("""
                SELECT slice_key, n_trials, n_successes, success_rate, ci_low, ci_high,
                       confidence_tier, COALESCE(source, 'computed') AS source
                FROM base_rates
                WHERE phase IS NULL
                  AND indication_category = :indication_category
                  AND sponsor_class IS NULL
                  AND (source IS NULL OR source = 'computed')
                LIMIT 1
                """),
                {"indication_category": indication_category},
            )
            .mappings()
            .first()
        )
        if row and _meets_tier(row["confidence_tier"], min_confidence):
            return _row_to_result(row, fallback_used=True, rate_source=row["source"])
    return None


def get_base_rate_for_catalyst(
    catalyst_type: str,
    phase: str | None = None,
    indication_category: str | None = None,
    sponsor_class: str | None = None,
    submission_type: str | None = None,
    min_confidence: str = "medium",
) -> BaseRateResult | None:
    """
    Routes to phase-based slices for trial readouts, or industry priors for
    pdufa/adcom catalysts. Falls back through specificity chain as before.
    """
    ctype = (catalyst_type or "").lower()

    if ctype in PDUFA_TYPES:
        key = (
            "pdufa|snda_efficacy_supplement" if submission_type == "snda" else "pdufa|novel_nda_bla"
        )
        return _lookup_slice_key(key, min_confidence)

    if ctype in ADCOM_TYPES:
        return _lookup_slice_key("adcom|any_vote_held", min_confidence)

    if ctype in READOUT_TYPES or ctype == "":
        if phase:
            return get_base_rate(phase, indication_category, sponsor_class, min_confidence)
        return get_base_rate_by_indication(indication_category, min_confidence)

    if phase:
        return get_base_rate(phase, indication_category, sponsor_class, min_confidence)
    return get_base_rate_by_indication(indication_category, min_confidence)
