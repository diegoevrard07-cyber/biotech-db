"""
XBRL companyfacts extractor with YTD-aware quarterly derivation.

Layer 4 pre-build hardening pass: handles YTD-only filers, Q4-from-FY derivation,
fiscal-year offsets, missing-quarter gaps, and burn/runway helpers.

Addresses issues called out in the Layer 4 Pre-Build Hardening spec (Fix 2):
period-span classification must not rely on the unreliable `fp` field alone.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

SpanKind = Literal["quarter", "h1", "ytd9", "fy", "unknown"]
SourceKind = Literal["direct", "derived_from_ytd", "derived_from_fy"]
ConfidenceKind = Literal["high", "medium", "low"]


@dataclass
class QuarterlyValue:
    period_end: date
    value: float
    source: SourceKind
    confidence: ConfidenceKind
    raw_facts: list[dict] = field(default_factory=list)


def _parse_date(value: str) -> date:
    y, m, d = (int(x) for x in value.split("-"))
    return date(y, m, d)


def _span_days(start: date, end: date) -> int:
    return (end - start).days + 1


def _classify_span(start: date, end: date) -> SpanKind:
    days = _span_days(start, end)
    if 83 <= days <= 97:
        return "quarter"
    if 173 <= days <= 187:
        return "h1"
    if 263 <= days <= 277:
        return "ytd9"
    if 358 <= days <= 372:
        return "fy"
    return "unknown"


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _fiscal_year_window(
    period_end: date,
    *,
    fiscal_year_end_month: int,
    fiscal_year_end_day: int,
) -> tuple[int, date, date]:
    """Return (fiscal_year_label, fy_start, fy_end) containing period_end."""
    fy_end_year = period_end.year
    if (period_end.month, period_end.day) > (fiscal_year_end_month, fiscal_year_end_day):
        fy_end_year += 1
    fy_end = date(fy_end_year, fiscal_year_end_month, fiscal_year_end_day)
    prev_fy_end = date(fy_end_year - 1, fiscal_year_end_month, fiscal_year_end_day)
    fy_start = prev_fy_end + timedelta(days=1)
    return fy_end_year, fy_start, fy_end


def _quarter_end_dates(fy_start: date, fy_end: date) -> list[date]:
    ends: list[date] = []
    cursor = fy_start
    for _ in range(4):
        nxt = _add_months(cursor, 3)
        q_end = nxt - timedelta(days=1)
        if q_end > fy_end:
            q_end = fy_end
        ends.append(q_end)
        cursor = q_end + timedelta(days=1)
    return ends


def _dates_close(a: date, b: date, *, tolerance: int = 7) -> bool:
    return abs((a - b).days) <= tolerance


def _get_usd_facts(companyfacts: dict, concept: str) -> list[dict]:
    try:
        units = companyfacts["facts"]["us-gaap"][concept]["units"]["USD"]
    except (KeyError, TypeError):
        return []
    return list(units)


def _dedupe_facts(facts: list[dict]) -> list[dict]:
    """Keep the most recently filed fact per (start, end) pair."""
    best: dict[tuple[str, str], dict] = {}
    for fact in facts:
        if "start" not in fact or "end" not in fact:
            continue
        key = (fact["start"], fact["end"])
        prev = best.get(key)
        if prev is None or (fact.get("filed", "") > prev.get("filed", "")):
            best[key] = fact
    return list(best.values())


def extract_quarterly_concept(
    companyfacts: dict,
    concept: str,
    *,
    fiscal_year_end_month: int = 12,
    fiscal_year_end_day: int = 31,
) -> list[QuarterlyValue]:
    """
    Return true 3-month quarterly values for a us-gaap concept.

    Handles YTD-only filers via subtraction and Q4-from-FY derivation.
    """
    parsed: list[dict] = []
    for fact in _dedupe_facts(_get_usd_facts(companyfacts, concept)):
        start = _parse_date(fact["start"])
        end = _parse_date(fact["end"])
        span = _classify_span(start, end)
        fy_label, fy_start, fy_end = _fiscal_year_window(
            end,
            fiscal_year_end_month=fiscal_year_end_month,
            fiscal_year_end_day=fiscal_year_end_day,
        )
        parsed.append(
            {
                **fact,
                "_start": start,
                "_end": end,
                "_span": span,
                "_fy": fy_label,
                "_fy_start": fy_start,
                "_fy_end": fy_end,
            }
        )

    by_fy: dict[int, list[dict]] = {}
    for item in parsed:
        by_fy.setdefault(item["_fy"], []).append(item)

    results: list[QuarterlyValue] = []

    for fy_label in sorted(by_fy):
        items = by_fy[fy_label]
        fy_start = items[0]["_fy_start"]
        fy_end = items[0]["_fy_end"]
        q_ends = _quarter_end_dates(fy_start, fy_end)

        direct: dict[date, dict] = {}
        ytd: dict[date, dict] = {}
        fy_fact: dict | None = None

        for item in items:
            end = item["_end"]
            span = item["_span"]
            if span == "quarter":
                for qe in q_ends:
                    if _dates_close(end, qe):
                        prev = direct.get(qe)
                        if prev is None or item.get("filed", "") > prev.get("filed", ""):
                            direct[qe] = item
                        break
            elif span == "h1" and len(q_ends) > 1 and _dates_close(end, q_ends[1]):
                prev = ytd.get(q_ends[1])
                if prev is None or item.get("filed", "") > prev.get("filed", ""):
                    ytd[q_ends[1]] = item
            elif span == "ytd9" and len(q_ends) > 2 and _dates_close(end, q_ends[2]):
                prev = ytd.get(q_ends[2])
                if prev is None or item.get("filed", "") > prev.get("filed", ""):
                    ytd[q_ends[2]] = item
            elif span == "fy" and _dates_close(end, fy_end):
                if fy_fact is None or item.get("filed", "") > fy_fact.get("filed", ""):
                    fy_fact = item

        for item in items:
            if item["_start"] == fy_start:
                days = _span_days(fy_start, item["_end"])
                if 173 <= days <= 187 and len(q_ends) > 1:
                    ytd[q_ends[1]] = item
                elif 263 <= days <= 277 and len(q_ends) > 2:
                    ytd[q_ends[2]] = item
                elif days >= 358 and _dates_close(item["_end"], fy_end):
                    fy_fact = item

        quarter_values: dict[date, QuarterlyValue] = {}

        for qe, fact in direct.items():
            quarter_values[qe] = QuarterlyValue(
                period_end=qe,
                value=float(fact["val"]),
                source="direct",
                confidence="high",
                raw_facts=[fact],
            )

        if len(q_ends) > 1 and q_ends[1] in ytd:
            h1 = ytd[q_ends[1]]
            q1_val = quarter_values.get(q_ends[0])
            if q1_val is not None and q_ends[1] not in quarter_values:
                quarter_values[q_ends[1]] = QuarterlyValue(
                    period_end=q_ends[1],
                    value=float(h1["val"]) - q1_val.value,
                    source="derived_from_ytd",
                    confidence="high",
                    raw_facts=[h1, q1_val.raw_facts[0]],
                )

        if len(q_ends) > 2 and q_ends[2] in ytd:
            ytd9 = ytd[q_ends[2]]
            if q_ends[2] not in quarter_values:
                prior_ytd = ytd.get(q_ends[1])
                if prior_ytd is not None:
                    quarter_values[q_ends[2]] = QuarterlyValue(
                        period_end=q_ends[2],
                        value=float(ytd9["val"]) - float(prior_ytd["val"]),
                        source="derived_from_ytd",
                        confidence="high",
                        raw_facts=[ytd9, prior_ytd],
                    )
                else:
                    prior_val = quarter_values.get(q_ends[1])
                    if prior_val is not None:
                        q1_val = quarter_values.get(q_ends[0])
                        if q1_val is not None:
                            cumulative = q1_val.value + prior_val.value
                            quarter_values[q_ends[2]] = QuarterlyValue(
                                period_end=q_ends[2],
                                value=float(ytd9["val"]) - cumulative,
                                source="derived_from_ytd",
                                confidence="medium",
                                raw_facts=[ytd9, prior_val.raw_facts[0]],
                            )

        if fy_fact is not None and len(q_ends) == 4 and q_ends[3] not in quarter_values:
            q4_end = q_ends[3]
            if q_ends[2] in ytd:
                q4_val = float(fy_fact["val"]) - float(ytd[q_ends[2]]["val"])
                quarter_values[q4_end] = QuarterlyValue(
                    period_end=q4_end,
                    value=q4_val,
                    source="derived_from_fy",
                    confidence="high",
                    raw_facts=[fy_fact, ytd[q_ends[2]]],
                )
            else:
                known = [quarter_values[qe] for qe in q_ends[:3] if qe in quarter_values]
                if len(known) == 3:
                    q4_val = float(fy_fact["val"]) - sum(v.value for v in known)
                    quarter_values[q4_end] = QuarterlyValue(
                        period_end=q4_end,
                        value=q4_val,
                        source="derived_from_fy",
                        confidence="medium",
                        raw_facts=[fy_fact, *[v.raw_facts[0] for v in known]],
                    )

        results.extend(sorted(quarter_values.values(), key=lambda q: q.period_end))

    return results


def compute_burn_rate(
    quarterly_ocf: list[QuarterlyValue],
    quarters_to_average: int = 4,
) -> float | None:
    """
    Quarterly burn = -mean(operating_cash_flow) over last N quarters.

    Returns None if <2 quarters available. Negative burn => cash-flow positive.
    """
    if len(quarterly_ocf) < 2:
        return None
    recent = sorted(quarterly_ocf, key=lambda q: q.period_end)[-quarters_to_average:]
    if len(recent) < 2:
        return None
    avg_ocf = sum(q.value for q in recent) / len(recent)
    return -avg_ocf


def compute_runway(
    cash_position: float,
    quarterly_burn: float | None,
    *,
    recent_dilution: float = 0.0,
) -> dict:
    """
    Estimate runway from cash and quarterly burn.

    Returns runway_quarters, runway_months, and flags (profitable, low_data, etc.).
    """
    flags: list[str] = []
    if recent_dilution:
        flags.append("recent_dilution_unconfirmed")

    if quarterly_burn is None:
        flags.append("low_data")
        return {
            "runway_quarters": None,
            "runway_months": None,
            "flags": flags,
        }

    if quarterly_burn <= 0:
        flags.append("profitable")
        return {
            "runway_quarters": float("inf"),
            "runway_months": float("inf"),
            "flags": flags,
        }

    adjusted_cash = cash_position + recent_dilution
    quarters = adjusted_cash / quarterly_burn
    return {
        "runway_quarters": quarters,
        "runway_months": quarters * 3.0,
        "flags": flags,
    }
