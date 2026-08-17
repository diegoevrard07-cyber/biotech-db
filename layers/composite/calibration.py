"""
Pure calibration math (Brier score + reliability table). No DB / network.

A prediction `pair` is (predicted_probability, actual_outcome) where actual is
1 for a hit and 0 for a miss. Ambiguous outcomes should be filtered upstream.
"""

from __future__ import annotations

from typing import Any


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    """Mean squared error of probabilistic predictions. Lower is better (0..1)."""
    valid = [(p, a) for p, a in pairs if p is not None and a is not None]
    if not valid:
        return None
    return round(sum((p - a) ** 2 for p, a in valid) / len(valid), 6)


def reliability_table(pairs: list[tuple[float, int]], *, n_buckets: int = 5) -> list[dict[str, Any]]:
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
