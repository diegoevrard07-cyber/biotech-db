"""Forward risk projection and sizing surfaces for the paper book.

Pure numpy, no DB, fully unit-tested. Used by the terminal's Risk Lab page.

Methods are deliberately simple and inspectable:
- Equity projection: bootstrap resampling of observed daily returns (no
  distributional assumption). When the book's own history is too short the
  caller passes benchmark returns scaled by beta as a labeled proxy.
- Scenarios: first-order beta approximation (book move = beta * index shock),
  which is exactly the assumption the regime filter and drawdown tiers hedge.
- Kelly surface: the classic f* = p - (1-p)/b, clipped at zero, evaluated on a
  grid so the current book's operating point can be shown in context.
"""

from __future__ import annotations

import numpy as np

DEFAULT_QUANTILES = (5, 25, 50, 75, 95)


def bootstrap_equity_paths(
    daily_returns: list[float] | np.ndarray,
    start_equity: float,
    days: int = 126,
    n_paths: int = 2000,
    seed: int = 7,
) -> np.ndarray:
    """Simulate equity paths by IID bootstrap of observed daily returns.

    Returns an array of shape (n_paths, days + 1) whose first column is
    ``start_equity``. Raises ValueError with fewer than 5 observed returns.
    """
    rets = np.asarray(daily_returns, dtype=float)
    rets = rets[np.isfinite(rets)]
    if rets.size < 5:
        raise ValueError("need at least 5 daily returns to bootstrap")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, rets.size, size=(n_paths, days))
    growth = np.cumprod(1.0 + rets[idx], axis=1)
    paths = np.empty((n_paths, days + 1), dtype=float)
    paths[:, 0] = start_equity
    paths[:, 1:] = start_equity * growth
    return paths


def path_quantiles(
    paths: np.ndarray, quantiles: tuple[int, ...] = DEFAULT_QUANTILES
) -> dict[int, np.ndarray]:
    """Per-day equity quantiles across simulated paths, keyed by percentile."""
    return {q: np.percentile(paths, q, axis=0) for q in quantiles}


def projection_summary(paths: np.ndarray) -> dict:
    """Headline stats of a simulation: terminal quantiles and loss probabilities."""
    start = float(paths[0, 0])
    terminal = paths[:, -1]
    return {
        "p05": float(np.percentile(terminal, 5)),
        "p50": float(np.percentile(terminal, 50)),
        "p95": float(np.percentile(terminal, 95)),
        "prob_loss": float((terminal < start).mean()),
        "prob_down_10": float((terminal < start * 0.90).mean()),
        "expected_return": float(terminal.mean() / start - 1.0),
    }


def scenario_impacts(
    beta: float,
    gross_exposure: float,
    equity: float,
    shocks: tuple[float, ...] = (-0.20, -0.10, -0.05, 0.05, 0.10, 0.20),
) -> list[dict]:
    """First-order book impact of benchmark shocks: pnl = beta * shock * gross.

    ``gross_exposure`` is deployed fraction of equity (e.g. 0.8 = 80% long).
    Returns one row per shock with pct and dollar impact.
    """
    rows = []
    for s in shocks:
        pnl_pct = beta * s * gross_exposure
        rows.append(
            {
                "shock": s,
                "book_pct": pnl_pct,
                "book_usd": pnl_pct * equity,
            }
        )
    return rows


def kelly_fraction(win_prob: float, payoff: float) -> float:
    """Full-Kelly fraction f* = p - (1-p)/b, clipped at 0 (no negative bets)."""
    if payoff <= 0:
        return 0.0
    return max(0.0, win_prob - (1.0 - win_prob) / payoff)


def kelly_surface(
    win_probs: np.ndarray | None = None, payoffs: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Grid of full-Kelly fractions over win probability x payoff ratio.

    Returns (win_probs, payoffs, surface) where surface[i, j] is the Kelly
    fraction at payoffs[i], win_probs[j] (plotly Surface orientation).
    """
    if win_probs is None:
        win_probs = np.linspace(0.30, 0.90, 61)
    if payoffs is None:
        payoffs = np.linspace(0.5, 3.0, 51)
    p = np.asarray(win_probs, dtype=float)
    b = np.asarray(payoffs, dtype=float)[:, None]
    surface = np.clip(p[None, :] - (1.0 - p[None, :]) / b, 0.0, None)
    return np.asarray(win_probs), np.asarray(payoffs), surface
