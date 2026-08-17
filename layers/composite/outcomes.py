"""
Pure outcome math for catalyst labeling. No DB / network -> unit-testable.

The label is reaction-based: it measures the market's abnormal move around the
catalyst (vs a biotech benchmark), which for a binary event is a reasonable proxy
for hit/miss. It is NOT ground-truth trial success - that caveat matters and is
surfaced in the source field ('price_reaction').
"""

from __future__ import annotations

from typing import Any

LABEL_HIT = "hit"
LABEL_MISS = "miss"
LABEL_AMBIGUOUS = "ambiguous"


def _ret(pre: float | None, post: float | None) -> float | None:
    if pre is None or post is None:
        return None
    try:
        pre_f, post_f = float(pre), float(post)
    except (TypeError, ValueError):
        return None
    if pre_f <= 0:
        return None
    return post_f / pre_f - 1.0


def label_outcome(abnormal_return: float | None, threshold: float) -> str | None:
    """Classify a catalyst as hit/miss/ambiguous from its benchmark-adjusted move."""
    if abnormal_return is None:
        return None
    if abnormal_return >= threshold:
        return LABEL_HIT
    if abnormal_return <= -threshold:
        return LABEL_MISS
    return LABEL_AMBIGUOUS


def compute_outcome(
    pre: float | None,
    post: float | None,
    bench_pre: float | None,
    bench_post: float | None,
    *,
    threshold: float,
) -> dict[str, Any]:
    """Return raw/benchmark/abnormal returns + label for a catalyst window."""
    raw = _ret(pre, post)
    bench = _ret(bench_pre, bench_post)
    abnormal = None
    if raw is not None:
        abnormal = raw - bench if bench is not None else raw
    return {
        "raw_return": round(raw, 6) if raw is not None else None,
        "benchmark_return": round(bench, 6) if bench is not None else None,
        "abnormal_return": round(abnormal, 6) if abnormal is not None else None,
        "outcome_label": label_outcome(abnormal, threshold),
    }
