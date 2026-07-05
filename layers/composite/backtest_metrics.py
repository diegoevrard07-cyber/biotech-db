"""
Pure portfolio metrics for the backtest. No DB / network -> unit-testable.

A "trade" here is a realized per-trade return already adjusted for direction and
weight (i.e., the contribution to the book). Sharpe is per-trade (not annualized)
and labeled as such, because catalyst trades are event-spaced, not periodic.
"""

from __future__ import annotations

import math
from typing import Any


def equity_curve(weighted_returns: list[float], *, start: float = 1.0) -> list[float]:
    """Compounded equity path of the book from per-trade weighted returns."""
    eq = start
    curve = [start]
    for r in weighted_returns:
        eq *= 1.0 + r
        curve.append(eq)
    return curve


def max_drawdown(curve: list[float]) -> float:
    """Worst peak-to-trough loss of an equity curve (negative fraction, 0.0 if none)."""
    if not curve:
        return 0.0
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return round(mdd, 6)


def per_trade_sharpe(returns: list[float]) -> float | None:
    """Mean/std of per-trade returns; None if fewer than 2 trades or zero variance."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd < 1e-12:  # effectively constant returns -> undefined Sharpe
        return None
    return round(mean / sd, 4)


def summarize(trade_returns: list[float], weighted_returns: list[float]) -> dict[str, Any]:
    """trade_returns = raw directional returns; weighted_returns = after sizing."""
    n = len(trade_returns)
    if n == 0:
        return {"n_trades": 0}
    wins = sum(1 for r in trade_returns if r > 0)
    curve = equity_curve(weighted_returns)
    return {
        "n_trades": n,
        "hit_rate": round(wins / n, 4),
        "avg_return": round(sum(trade_returns) / n, 6),
        "avg_weighted_return": round(sum(weighted_returns) / n, 6),
        "total_return": round(curve[-1] - 1.0, 6),
        "max_drawdown": max_drawdown(curve),
        "per_trade_sharpe": per_trade_sharpe(weighted_returns),
        "final_equity": round(curve[-1], 4),
    }
