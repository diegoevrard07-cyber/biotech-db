"""Tests for layers/portfolio/risk.py — stop-loss, graded drawdown, regime filter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers.portfolio import risk

TIERS = [(0.06, 0.75), (0.10, 0.50), (0.15, 0.25)]


def test_stop_loss_triggers_only_below_threshold():
    assert risk.stop_loss_hit("long", 10.0, 8.4, 0.15) is True  # -16%
    assert risk.stop_loss_hit("long", 10.0, 8.5, 0.15) is True  # exactly -15%
    assert risk.stop_loss_hit("long", 10.0, 8.6, 0.15) is False  # -14%
    assert risk.stop_loss_hit("long", 10.0, 11.0, 0.15) is False  # winner
    assert risk.stop_loss_hit("short", 10.0, 20.0, 0.15) is False  # shorts untouched
    assert risk.stop_loss_hit("long", 10.0, None, 0.15) is False  # unpriced
    assert risk.stop_loss_hit("long", 0.0, 5.0, 0.15) is False  # bad entry


def test_drawdown_scale_tiers():
    assert risk.drawdown_scale(10_000, 10_000, TIERS) == 1.0  # at peak
    assert risk.drawdown_scale(9_500, 10_000, TIERS) == 1.0  # -5%: above first tier
    assert risk.drawdown_scale(9_350, 10_000, TIERS) == 0.75  # -6.5%
    assert risk.drawdown_scale(8_900, 10_000, TIERS) == 0.50  # -11%
    assert risk.drawdown_scale(8_000, 10_000, TIERS) == 0.25  # -20%
    assert risk.drawdown_scale(10_500, 10_000, TIERS) == 1.0  # new high
    assert risk.drawdown_scale(9_000, 0, TIERS) == 1.0  # no peak yet


def test_regime_scale():
    up = [100 + i * 0.5 for i in range(20)]  # rising: last >= sma
    assert risk.regime_scale(up, 20, 0.6) == 1.0
    down = [120 - i for i in range(20)]  # falling: last < sma
    assert risk.regime_scale(down, 20, 0.6) == 0.6
    assert risk.regime_scale(down[:10], 20, 0.6) == 1.0  # insufficient data
    assert risk.regime_scale([], 20, 0.6) == 1.0
