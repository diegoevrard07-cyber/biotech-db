"""Pure performance/risk statistics for the paper book.

Shared by the ``scripts/risk_report.py`` CLI and the Streamlit terminal so both
surfaces always show the same numbers. Stdlib-only, DB-free, fully unit-tested.

Conventions (match the original CLI report):
- Sharpe uses rf=0 and annualizes daily returns with sqrt(252).
- Population statistics (pstdev/pvariance), consistent with small samples.
- Functions return ``None`` when the sample is too small to say anything.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

ANNUALIZE = math.sqrt(252)


def simple_returns(series: Sequence[float]) -> list[float]:
    """Period-over-period simple returns, skipping zero/None-like bases."""
    return [series[i] / series[i - 1] - 1 for i in range(1, len(series)) if series[i - 1]]


def max_drawdown(series: Sequence[float]) -> float:
    """Worst peak-to-trough decline of a value series (0.0 if it never falls)."""
    if not series:
        return 0.0
    peak, mdd = series[0], 0.0
    for p in series:
        peak = max(peak, p)
        if peak:
            mdd = min(mdd, p / peak - 1)
    return mdd


def sharpe(rets: Sequence[float]) -> float | None:
    """Annualized Sharpe (rf=0) of daily returns; None when undefined."""
    if len(rets) < 2:
        return None
    sd = statistics.pstdev(rets)
    if sd <= 0:
        return None
    return (sum(rets) / len(rets)) / sd * ANNUALIZE


def beta(port_rets: Sequence[float], bench_rets: Sequence[float]) -> float | None:
    """OLS beta of portfolio returns on benchmark returns; None when undefined."""
    n = min(len(port_rets), len(bench_rets))
    if n < 2:
        return None
    pe, xe = list(port_rets[:n]), list(bench_rets[:n])
    var = statistics.pvariance(xe)
    if var <= 0:
        return None
    mean_p = sum(pe) / n
    mean_x = sum(xe) / n
    cov = sum((pe[i] - mean_p) * (xe[i] - mean_x) for i in range(n)) / n
    return cov / var


def closed_trade_stats(returns: Sequence[float]) -> dict | None:
    """Distribution stats for a list of per-trade returns (realized P&L / cost).

    Returns None when there are no trades. ``payoff`` is inf when there are no
    losses; ``kelly`` is NaN when payoff is 0 or inf (undefined).
    """
    rets = [float(r) for r in returns]
    if not rets:
        return None
    n = len(rets)
    mean = sum(rets) / n
    sd = statistics.pstdev(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss else float("inf")
    kelly = win_rate - (1 - win_rate) / payoff if payoff not in (0, float("inf")) else float("nan")
    return {
        "n": n,
        "expectancy": mean,
        "sd": sd,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "per_trade_sharpe": (mean / sd) if sd else None,
        "kelly": kelly,
    }


def equity_curve_stats(
    equity: Sequence[float], benchmark: Sequence[float | None] | None = None
) -> dict | None:
    """Curve-level stats for a daily equity series, optionally with an aligned
    benchmark series (same length; None where the benchmark had no close).

    Returns None when there are fewer than 3 snapshots.
    """
    eq = [float(e) for e in equity]
    if len(eq) < 3:
        return None
    rets = simple_returns(eq)
    out = {
        "n": len(eq),
        "period_return": eq[-1] / eq[0] - 1 if eq[0] else None,
        "ann_vol": statistics.pstdev(rets) * ANNUALIZE if len(rets) >= 2 else None,
        "sharpe": sharpe(rets),
        "max_drawdown": max_drawdown(eq),
        "beta": None,
        "beta_days": 0,
    }
    if benchmark is not None:
        pairs = [
            (eq[i], float(benchmark[i]))
            for i in range(min(len(eq), len(benchmark)))
            if benchmark[i] is not None
        ]
        if len(pairs) >= 3:
            pe = simple_returns([p[0] for p in pairs])
            xe = simple_returns([p[1] for p in pairs])
            b = beta(pe, xe)
            if b is not None:
                out["beta"] = b
                out["beta_days"] = min(len(pe), len(xe))
    return out


def benchmark_reference_stats(closes: Sequence[float], min_days: int = 60) -> dict | None:
    """Long-run yardstick for a benchmark close series (annualized return, vol,
    Sharpe, max drawdown). Returns None below ``min_days`` of history.
    """
    px = [float(c) for c in closes]
    if len(px) <= min_days:
        return None
    rets = simple_returns(px)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    sd = statistics.pstdev(rets)
    return {
        "n": len(px),
        "ann_return": (1 + mean) ** 252 - 1,
        "ann_vol": sd * ANNUALIZE,
        "sharpe": (mean / sd) * ANNUALIZE if sd else None,
        "max_drawdown": max_drawdown(px),
    }
