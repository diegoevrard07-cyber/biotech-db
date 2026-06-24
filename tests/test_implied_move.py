"""ATM implied-move calculation tests with synthetic option chains."""

from __future__ import annotations

import pandas as pd

from layers.marketdata.options import compute_implied_move, pick_expiry


def _chain():
    calls = pd.DataFrame({
        "strike": [8.0, 10.0, 12.0],
        "bid": [2.0, 1.0, 0.4],
        "ask": [2.2, 1.2, 0.6],
        "lastPrice": [2.1, 1.1, 0.5],
        "impliedVolatility": [0.9, 1.0, 1.1],
    })
    puts = pd.DataFrame({
        "strike": [8.0, 10.0, 12.0],
        "bid": [0.4, 1.0, 2.0],
        "ask": [0.6, 1.2, 2.2],
        "lastPrice": [0.5, 1.1, 2.1],
        "impliedVolatility": [1.1, 1.0, 0.9],
    })
    return calls, puts


def test_atm_straddle_implied_move():
    calls, puts = _chain()
    out = compute_implied_move(calls, puts, spot=10.0)
    assert out["atm_strike"] == 10.0
    # mid call 1.1 + mid put 1.1 = 2.2 ; /10 = 0.22
    assert out["implied_move_pct"] == 0.22
    assert out["atm_iv"] == 1.0


def test_single_leg_doubled():
    calls, _ = _chain()
    out = compute_implied_move(calls, None, spot=10.0)
    # only call mid 1.1 -> straddle approximated as 2.2 -> 0.22
    assert out["implied_move_pct"] == 0.22


def test_no_spot_returns_none():
    calls, puts = _chain()
    out = compute_implied_move(calls, puts, spot=None)
    assert out["implied_move_pct"] is None


def test_pick_expiry():
    exps = ["2026-07-01", "2026-08-15", "2026-09-19"]
    assert pick_expiry(exps, "2026-08-01") == "2026-08-15"
    assert pick_expiry(exps, "2026-10-01") == "2026-09-19"  # none after -> last
    assert pick_expiry(exps, None) == "2026-09-19"
    assert pick_expiry([], "2026-08-01") is None
