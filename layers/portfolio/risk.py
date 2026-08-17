"""Pure risk-overlay logic for the paper autopilot: stop-loss, graded drawdown
de-risking, and the benchmark regime filter.

No DB, no I/O — plain values in, plain values out (unit-testable)."""

from __future__ import annotations

from layers.portfolio.tracker import LONG


def stop_loss_hit(side: str, entry_price: float, last_price: float | None, stop_pct: float) -> bool:
    """True when a LONG's mark is down more than stop_pct from entry.

    Shorts are not stopped here (long-only book; covers happen via the book sync).
    """
    if side != LONG or last_price is None or entry_price <= 0:
        return False
    return (float(last_price) / float(entry_price) - 1.0) <= -abs(stop_pct)


def drawdown_scale(equity: float, peak: float, tiers: list[tuple[float, float]]) -> float:
    """Target-size multiplier for the current drawdown from peak.

    tiers: [(drawdown_pct, scale)] sorted ascending by drawdown; the deepest
    breached tier wins. Returns 1.0 when above the first tier or peak unknown.
    """
    if peak <= 0 or equity >= peak:
        return 1.0
    dd = 1.0 - float(equity) / float(peak)
    scale = 1.0
    for level, s in sorted(tiers):
        if dd >= level:
            scale = s
    return scale


def regime_scale(benchmark_closes: list[float], sma_days: int, derisk_factor: float) -> float:
    """1.0 when the benchmark's latest close is at/above its sma_days SMA,
    otherwise derisk_factor. closes: oldest -> newest. Needs sma_days closes;
    returns 1.0 (no opinion) on insufficient data."""
    closes = [float(c) for c in benchmark_closes if c is not None]
    if len(closes) < sma_days:
        return 1.0
    window = closes[-sma_days:]
    sma = sum(window) / len(window)
    return 1.0 if closes[-1] >= sma else float(derisk_factor)
