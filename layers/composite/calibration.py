"""
Pure calibration math (Brier score + reliability table). No DB / network.

A prediction `pair` is (predicted_probability, actual_outcome) where actual is
1 for a hit and 0 for a miss. Ambiguous outcomes should be filtered upstream.
"""

from __future__ import annotations

import json
from typing import Any

# A reliability (calibration) curve is only worth drawing once several
# probability buckets each have real outcomes. One resolved catalyst is a point,
# not a curve - the dashboard hides the plot below these floors.
MIN_RELIABILITY_BUCKETS = 3
MIN_RELIABILITY_N = 20


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    """Mean squared error of probabilistic predictions. Lower is better (0..1)."""
    valid = [(p, a) for p, a in pairs if p is not None and a is not None]
    if not valid:
        return None
    return round(sum((p - a) ** 2 for p, a in valid) / len(valid), 6)


def reliability_table(
    pairs: list[tuple[float, int]], *, n_buckets: int = 5
) -> list[dict[str, Any]]:
    """Bucket predictions and compare mean predicted prob vs observed hit rate."""
    valid = [(p, a) for p, a in pairs if p is not None and a is not None]
    table: list[dict[str, Any]] = []
    if not valid:
        return table
    width = 1.0 / n_buckets
    for i in range(n_buckets):
        lo = i * width
        hi = (i + 1) * width if i < n_buckets - 1 else 1.0001
        bucket = [(p, a) for p, a in valid if lo <= p < hi]
        if not bucket:
            continue
        n = len(bucket)
        table.append(
            {
                "bucket": f"{lo:.2f}-{min(hi,1.0):.2f}",
                "n": n,
                "mean_predicted": round(sum(p for p, _ in bucket) / n, 4),
                "observed_hit_rate": round(sum(a for _, a in bucket) / n, 4),
            }
        )
    return table


def hit_rate(actuals: list[int]) -> float | None:
    """Observed fraction of hits; the base rate a calibrated model must beat."""
    if not actuals:
        return None
    return round(sum(actuals) / len(actuals), 4)


def parse_reliability(rel: Any) -> list[dict[str, Any]]:
    """Normalize calibration_runs.reliability_json to a list of bucket dicts."""
    if rel is None:
        return []
    if isinstance(rel, str):
        try:
            rel = json.loads(rel)
        except json.JSONDecodeError:
            return []
    if isinstance(rel, dict):
        rel = list(rel.values())
    if not isinstance(rel, list):
        return []
    return [row for row in rel if isinstance(row, dict)]


def reliability_n(table: list[dict[str, Any]]) -> int:
    """Total outcomes sitting in the reliability buckets."""
    total = 0
    for row in table:
        try:
            total += int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
    return total


def reliability_curve_ready(
    table: list[dict[str, Any]],
    *,
    n_pairs: int | None = None,
    min_buckets: int = MIN_RELIABILITY_BUCKETS,
    min_n: int = MIN_RELIABILITY_N,
) -> bool:
    """True only when a predicted-vs-observed plot would have more than one point."""
    if len(table) < min_buckets:
        return False
    n = reliability_n(table)
    if n_pairs is not None:
        try:
            n = max(n, int(n_pairs))
        except (TypeError, ValueError):
            pass
    return n >= min_n
