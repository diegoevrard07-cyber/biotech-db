"""Unit tests for layers/portfolio/stats.py (pure math, no DB)."""

from __future__ import annotations

import math

import pytest

from layers.portfolio import stats


class TestSimpleReturns:
    def test_basic(self):
        assert stats.simple_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])

    def test_zero_base_skipped(self):
        assert stats.simple_returns([0.0, 50.0, 100.0]) == pytest.approx([1.0])

    def test_short_series(self):
        assert stats.simple_returns([100.0]) == []
        assert stats.simple_returns([]) == []


class TestMaxDrawdown:
    def test_monotonic_up_is_zero(self):
        assert stats.max_drawdown([1.0, 2.0, 3.0]) == 0.0

    def test_known_drawdown(self):
        # peak 100 -> trough 60 = -40%
        assert stats.max_drawdown([80.0, 100.0, 60.0, 90.0]) == pytest.approx(-0.40)

    def test_empty(self):
        assert stats.max_drawdown([]) == 0.0


class TestSharpe:
    def test_needs_two_returns(self):
        assert stats.sharpe([0.01]) is None

    def test_zero_vol_is_none(self):
        assert stats.sharpe([0.01, 0.01, 0.01]) is None

    def test_known_value(self):
        rets = [0.01, -0.01]
        # mean 0, so Sharpe 0
        assert stats.sharpe(rets) == pytest.approx(0.0)


class TestBeta:
    def test_identical_series_is_one(self):
        r = [0.01, -0.02, 0.03, 0.005]
        assert stats.beta(r, r) == pytest.approx(1.0)

    def test_scaled_series(self):
        x = [0.01, -0.02, 0.03, 0.005]
        p = [2 * v for v in x]
        assert stats.beta(p, x) == pytest.approx(2.0)

    def test_flat_benchmark_is_none(self):
        assert stats.beta([0.01, 0.02], [0.0, 0.0]) is None

    def test_too_short_is_none(self):
        assert stats.beta([0.01], [0.01]) is None


class TestClosedTradeStats:
    def test_empty_is_none(self):
        assert stats.closed_trade_stats([]) is None

    def test_known_distribution(self):
        # 3 wins (+10%, +20%, +30%), 1 loss (-10%)
        s = stats.closed_trade_stats([0.10, 0.20, 0.30, -0.10])
        assert s["n"] == 4
        assert s["win_rate"] == pytest.approx(0.75)
        assert s["expectancy"] == pytest.approx(0.125)
        assert s["avg_win"] == pytest.approx(0.20)
        assert s["avg_loss"] == pytest.approx(-0.10)
        assert s["payoff"] == pytest.approx(2.0)
        # kelly = wr - (1-wr)/payoff = 0.75 - 0.25/2 = 0.625
        assert s["kelly"] == pytest.approx(0.625)
        assert s["per_trade_sharpe"] is not None

    def test_all_wins_payoff_inf_kelly_nan(self):
        s = stats.closed_trade_stats([0.05, 0.10])
        assert s["payoff"] == float("inf")
        assert math.isnan(s["kelly"])
        assert s["win_rate"] == 1.0

    def test_flat_trades_sharpe_none(self):
        s = stats.closed_trade_stats([0.05, 0.05])
        assert s["per_trade_sharpe"] is None


class TestEquityCurveStats:
    def test_too_short_is_none(self):
        assert stats.equity_curve_stats([100.0, 101.0]) is None

    def test_basic_curve(self):
        s = stats.equity_curve_stats([100.0, 110.0, 105.0, 115.0])
        assert s["n"] == 4
        assert s["period_return"] == pytest.approx(0.15)
        assert s["max_drawdown"] == pytest.approx(105.0 / 110.0 - 1)
        assert s["ann_vol"] is not None and s["ann_vol"] > 0
        assert s["beta"] is None  # no benchmark passed

    def test_beta_vs_identical_benchmark(self):
        eq = [100.0, 110.0, 105.0, 115.0]
        s = stats.equity_curve_stats(eq, benchmark=eq)
        assert s["beta"] == pytest.approx(1.0)
        assert s["beta_days"] == 3

    def test_benchmark_gaps_handled(self):
        eq = [100.0, 110.0, 105.0, 115.0]
        bench = [50.0, None, 52.0, 53.0]
        s = stats.equity_curve_stats(eq, benchmark=bench)
        # only 3 valid pairs -> 2 paired returns -> beta computable
        assert s["beta"] is not None

    def test_sparse_benchmark_no_beta(self):
        eq = [100.0, 110.0, 105.0]
        s = stats.equity_curve_stats(eq, benchmark=[50.0, None, None])
        assert s["beta"] is None


class TestBenchmarkReferenceStats:
    def test_below_min_days_is_none(self):
        assert stats.benchmark_reference_stats([100.0] * 60) is None

    def test_long_series(self):
        px = [100.0 * (1.001**i) for i in range(120)]
        s = stats.benchmark_reference_stats(px)
        assert s["n"] == 120
        assert s["ann_return"] == pytest.approx((1.001) ** 252 - 1, rel=1e-6)
        assert s["max_drawdown"] == 0.0
