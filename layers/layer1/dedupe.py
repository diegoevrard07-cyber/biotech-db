"""Deduplicate catalyst records by proximity and confidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import config


def _parse_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _confidence_rank(value: str | None) -> int:
    return config.CONFIDENCE_RANK.get((value or "low").lower(), 0)


def _dates_within_window(d1: date, d2: date, days: int = 14) -> bool:
    return abs((d1 - d2).days) <= days


def _merge_raw_data(a: dict | None, b: dict | None) -> dict:
    merged: dict[str, Any] = {}
    if a:
        merged.update(a)
    if b:
        merged.update(b)
    return merged


def dedupe_catalysts(catalysts: list[dict]) -> tuple[list[dict], int]:
    """
    Collapse duplicates: same (company_id, catalyst_type, trial_id) with expected_date ±14 days.
    Keep higher date_confidence; merge raw_data JSONBs.
    Returns (deduped_list, merge_count).
    """
    if not catalysts:
        return [], 0

    sorted_cats = sorted(
        catalysts,
        key=lambda c: (
            c.get("company_id"),
            c.get("catalyst_type"),
            c.get("trial_id"),
            _parse_date(c.get("expected_date")) or date.min,
        ),
    )

    result: list[dict] = []
    merges = 0

    for cat in sorted_cats:
        cat_date = _parse_date(cat.get("expected_date"))
        merged = False
        for i, existing in enumerate(result):
            if (
                existing.get("company_id") == cat.get("company_id")
                and existing.get("catalyst_type") == cat.get("catalyst_type")
                and existing.get("trial_id") == cat.get("trial_id")
            ):
                ex_date = _parse_date(existing.get("expected_date"))
                if cat_date and ex_date and _dates_within_window(cat_date, ex_date):
                    if _confidence_rank(cat.get("date_confidence")) > _confidence_rank(
                        existing.get("date_confidence")
                    ):
                        existing["date_confidence"] = cat.get("date_confidence")
                        existing["expected_date"] = cat.get("expected_date")
                        existing["description"] = cat.get("description")
                    existing["raw_data"] = _merge_raw_data(
                        existing.get("raw_data"), cat.get("raw_data")
                    )
                    existing["requires_manual_verification"] = bool(
                        existing.get("requires_manual_verification")
                        or cat.get("requires_manual_verification")
                    )
                    merges += 1
                    merged = True
                    break
        if not merged:
            result.append(dict(cat))

    return result, merges
