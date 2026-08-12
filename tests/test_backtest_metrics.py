"""Backtest portfolio metric tests."""

from __future__ import annotations

from layers.composite.backtest_metrics import (
    equity_curve,
    max_drawdown,
    per_trade_sharpe,
    summarize,
)


def test_equity_curve():
    curve = equity_curve([0.1, -0.5, 0.2])
    assert curve[0] == 1.0
    assert round(curve[-1], 4) == round(1.1 * 0.5 * 1.2, 4)


def test_max_drawdown():
    curve = [1.0, 1.2, 0.6, 0.9]
    assert max_drawdown(curve) == round(0.6 / 1.2 - 1.0, 6)


def test_per_trade_sharpe_none_when_flat():
    assert per_trade_sharpe([0.05, 0.05, 0.05]) is None
    assert per_trade_sharpe([0.1]) is None


def test_summarize_basic():
    s = summarize([0.2, -0.1, 0.3], [0.01, -0.005, 0.015])
    assert s["n_trades"] == 3
    assert s["hit_rate"] == round(2 / 3, 4)
    assert "max_drawdown" in s and "per_trade_sharpe" in s
