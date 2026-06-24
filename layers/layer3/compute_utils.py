"""Wilson CI base rate computation and slice key helpers."""

from __future__ import annotations

import math


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval (matches statsmodels method='wilson' at alpha=0.05)."""
    if n == 0:
        return 0.0, 0.0
    if successes < 0 or successes > n:
        return float("nan"), float("nan")

    z = 1.96 if alpha == 0.05 else abs(_z_for_alpha(alpha))
    p_hat = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p_hat + z2 / (2 * n)) / denom
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z2 / (4 * n)) / n) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return low, high


def _z_for_alpha(alpha: float) -> float:
    # Normal inverse CDF approximation for non-0.05 alpha values.
    from math import erfcinv

    return math.sqrt(2) * erfcinv(alpha)


def confidence_tier(n: int) -> str:
    if n >= 30:
        return "high"
    if n >= 10:
        return "medium"
    return "low"


def build_slice_key(**kwargs) -> str:
    parts = []
    for key in sorted(kwargs.keys()):
        val = kwargs[key]
        if val is not None and val != "":
            parts.append(f"{key}={val}")
    return "|".join(parts)
